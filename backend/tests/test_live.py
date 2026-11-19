import json
import os
import time
from contextlib import contextmanager
from urllib.parse import parse_qs, urlsplit
from uuid import UUID

import pytest
from cryptography.fernet import Fernet
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.media import database
from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.live import Connection, Feed, Moment
from app.models.media import Job
from app.services.live import buffer
from app.services.providers import http, oauth
from app.services.storage.local import LocalMedia
from app.workers.live import advance

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1", reason="requires PostgreSQL and Redis"
)


@pytest.fixture
def isolated(studio, monkeypatch):
    c, root = studio
    cfg = get_settings()
    monkeypatch.setattr(
        cfg, "oauth_encryption_key", SecretStr(Fernet.generate_key().decode())
    )
    for prefix in ["twitch", "google"]:
        monkeypatch.setattr(cfg, prefix + "_client_id", "test-client")
        monkeypatch.setattr(cfg, prefix + "_client_secret", SecretStr("test-secret"))
    with c.app.state.dependencies.db.connect() as conn:
        transaction = conn.begin()

        def override():
            with Session(conn, join_transaction_mode="create_savepoint") as db:
                yield db

        c.app.dependency_overrides[database] = override
        with Session(
            conn, join_transaction_mode="create_savepoint", expire_on_commit=False
        ) as db:
            yield c, db, LocalMedia(root)
        c.app.dependency_overrides.pop(database)
        transaction.rollback()


class ProviderMock:
    offline = False
    deny_clip = False
    failed = False
    download_url = "https://production.assets.clips.twitchcdn.net/authorized.mp4"
    clip_visible = True

    def __init__(self):
        self.grants = []

    def __call__(self, url, **kw):
        if url.endswith("/token"):
            self.grants.append(kw["form"]["grant_type"])
            return {
                "access_token": "private-access",
                "refresh_token": "private-refresh",
                "expires_in": 3600,
            }
        if url.endswith("/validate"):
            return {
                "client_id": "test-client",
                "user_id": "123",
                "scopes": ["clips:edit", "channel:manage:clips"],
            }
        if "/helix/users" in url:
            return {"data": [{"id": "123", "display_name": "Test channel"}]}
        if "youtube/v3/channels" in url:
            return {"items": [{"id": "uc", "snippet": {"title": "Owned channel"}}]}
        if "youtube/v3/liveBroadcasts" in url:
            assert parse_qs(urlsplit(url).query)["mine"] == ["true"]
            return {
                "items": [
                    {
                        "id": "owned",
                        "snippet": {"channelId": "uc", "title": "Own broadcast"},
                        "status": {"lifeCycleStatus": "live"},
                    }
                ]
            }
        if self.failed:
            raise StudioError(502, "provider_unavailable")
        if "/helix/streams" in url:
            return {
                "data": []
                if self.offline
                else [{"id": "stream1", "user_id": "123", "title": "Game"}]
            }
        if "/helix/clips/downloads" in url:
            if parse_qs(urlsplit(url).query)["clip_id"] == ["OtherClip"]:
                return {"data": []}
            assert parse_qs(urlsplit(url).query) == {
                "editor_id": ["123"],
                "broadcaster_id": ["123"],
                "clip_id": ["OwnedClip"],
            }
            return {
                "data": [
                    {
                        "clip_id": "OwnedClip",
                        "landscape_download_url": self.download_url,
                    }
                ]
            }
        if "/helix/clips" in url:
            if kw["method"] == "POST":
                if self.deny_clip:
                    raise StudioError(403, "provider_denied")
                return {"data": [{"id": "OwnedClip"}]}
            return {
                "data": [
                    {"id": "OwnedClip", "broadcaster_id": "123", "title": "Example"}
                ]
                if self.clip_visible
                else []
            }
        raise AssertionError("Unexpected provider endpoint")


def begin(c, p):
    res = c.post("/api/providers/" + p + "/connect")
    assert res.status_code == 200, res.text
    return parse_qs(urlsplit(res.json()["url"]).query)


def callback(c, p, params):
    return c.get(
        "/api/providers/" + p + "/callback", params=params, follow_redirects=False
    )


@pytest.mark.parametrize("name", ["youtube", "twitch"])
def test_oauth_and_ownership(isolated, monkeypatch, name):
    c, db, _ = isolated
    mock = ProviderMock()
    monkeypatch.setattr(http, "request_json", mock)
    assert callback(c, name, {"state": "a" * 43, "code": "anything"}).status_code == 403
    state = begin(c, name)["state"][0]
    assert (
        callback(c, name, {"state": state, "error": "access_denied"}).status_code == 403
    )
    params = begin(c, name)
    state = params["state"][0]
    for cookie in list(c.cookies.jar):
        if cookie.name.startswith("oauth_proof"):
            c.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
    assert (
        callback(c, name, {"state": state, "code": "wrong browser"}).status_code == 403
    )
    state = begin(c, name)["state"][0]
    response = callback(c, name, {"state": state, "code": "valid-code"})
    assert response.status_code == 303, response.text
    assert callback(c, name, {"state": state, "code": "valid-code"}).status_code == 403
    row = db.get(Connection, name)
    assert "private-" not in row.sealed
    data = json.loads(oauth.cipher().decrypt(row.sealed.encode()))
    row.sealed = oauth.seal({**data, "expires": 0})
    db.commit()
    response = c.get(f"/api/providers/{name}/broadcasts")
    assert response.status_code == 200, response.text
    assert response.json()[0]["state"] == "live" and "refresh_token" in mock.grants
    assert "private-" not in c.get("/api/providers").text
    if name == "youtube":
        assert (
            c.post(
                "/api/live", json={"provider": "youtube", "broadcast": "someone-else"}
            ).status_code
            == 404
        )
        assert (
            c.post(
                "/api/live",
                json={
                    "provider": "youtube",
                    "broadcast": "owned",
                    "url": "http://localhost/movie",
                },
            ).status_code
            == 422
        )
        assert "download" not in response.text


def test_twitch_clipping_errors_and_download(isolated, monkeypatch, recording):
    c, db, storage = isolated
    mock = ProviderMock()
    monkeypatch.setattr(http, "request_json", mock)
    state = begin(c, "twitch")["state"][0]
    assert callback(c, "twitch", {"state": state, "code": "ok"}).status_code == 303
    base = "/api/providers/twitch/clips"
    assert c.get(base).json()[0]["id"] == "OwnedClip"
    mock.offline = True
    assert c.post(base).json()["error"]["code"] == "stream_offline"
    mock.offline = False
    mock.deny_clip = True
    assert c.post(base).status_code == 403
    mock.deny_clip = False
    mock.failed = True
    assert c.post(base).status_code == 502
    mock.failed = False
    assert c.post(base).status_code == 202
    mock.clip_visible = False
    assert c.get(base + "/OwnedClip").json()["state"] == "pending"
    redis = c.app.state.dependencies.redis
    redis.set("native:123:OwnedClip", str(time.time() - 61), ex=5)
    assert c.get(base + "/OwnedClip").json()["state"] == "failed"
    mock.clip_visible = True
    assert c.get(base + "/OwnedClip").json()["state"] == "ready"
    assert c.post(base + "/OwnedClip/download").json()["url"] == mock.download_url
    import io

    @contextmanager
    def media(url):
        assert url == mock.download_url
        yield io.BytesIO(recording)

    monkeypatch.setattr(http, "open_response", media)
    result = c.post(base + "/OwnedClip/import")
    assert result.status_code == 200 and result.json()["duration"] > 0
    from test_url_import import PUBLIC, fake_server

    from app.models.media import VideoImport
    from app.services import url_ingestion
    from app.workers import imports

    monkeypatch.setattr(url_ingestion, "resolve", lambda host: [PUBLIC])
    link = {"url": "https://clips.twitch.tv/OwnedClip", "generate_clips": True}
    queued = c.post("/api/imports", json=link)
    assert queued.status_code == 202
    alias = {**link, "url": "https://www.twitch.tv/owner/clip/OwnedClip"}
    assert c.post("/api/imports", json=alias).json()["id"] == queued.json()["id"]
    fake_server(monkeypatch, recording)
    key = UUID(queued.json()["id"])
    imports.process(db, db.get(VideoImport, key), storage)
    assert c.get(f"/api/videos/{key}/preview").content == recording
    assert c.get(f"/api/videos/{key}/auto-clips").json()["state"] == "queued"
    mock.download_url = "http://127.0.0.1/private"
    assert (
        c.post(
            "/api/imports", json={**link, "url": "https://clips.twitch.tv/OtherClip"}
        ).status_code
        == 403
    )
    assert c.post(base + "/OwnedClip/import").status_code == 403

    def limited(*args, **kwargs):
        raise StudioError(429, "provider_rate_limited")

    monkeypatch.setattr(http, "request_json", limited)
    assert c.get(base + "/OwnedClip").status_code == 429
    assert redis.ttl("provider:budget:twitch:wait") > 0
    redis.delete("provider:budget:twitch:wait")


def test_live_buffer_and_highlight(isolated, monkeypatch, recording):
    c, db, storage = isolated
    mock = ProviderMock()
    monkeypatch.setattr(http, "request_json", mock)
    state = begin(c, "youtube")["state"][0]
    callback(c, "youtube", {"state": state, "code": "ok"})
    form = {"provider": "youtube", "broadcast": "owned"}
    created = c.post("/api/live", json=form)
    assert created.status_code == 201, created.text
    assert c.post("/api/live", json=form).status_code == 409
    fid = UUID(created.json()["id"])
    path = f"/api/live/{fid}"
    headers = {
        "Authorization": "Bearer " + created.json()["ingest_key"],
        "Content-Type": "video/mp4",
    }

    def push(seq, start):
        return c.put(
            f"{path}/segments/{seq}?start={start}", content=recording, headers=headers
        )

    assert c.put(path + "/segments/0?start=0", content=recording).status_code == 401
    assert push(0, 0).json()["accepted"]
    assert not push(0, 0).json()["accepted"]
    assert push(1, 0.1).status_code == 409
    assert push(1, 2).status_code == 200
    feed = db.get(Feed, fid)
    rows = buffer.buffered(db, feed)
    for _s, _v, an in rows:
        an.state = "completed"
        an.transcript = []
        an.levels = []
        an.samples = []
        an.candidates = []
    rows[0][2].candidates = [
        {
            "event": 1,
            "start": 0,
            "end": 2,
            "score": 38,
            "signals": {"audio": 0.8, "motion": 0.6},
            "speech": None,
        }
    ]
    rows[1][2].candidates = rows[0][2].candidates
    db.commit()
    advance(db, feed, storage, time.time() + 45)
    assert feed.state == "interrupted"
    moment = db.scalar(select(Moment).where(Moment.feed_id == fid))
    assert moment.start == 0 and moment.end == pytest.approx(4, abs=0.1)
    assert db.get(Job, moment.job_id).state == "queued"
    assert push(2, 4).status_code == 200
    db.refresh(feed)
    assert feed.state == "receiving" and feed.end_time == pytest.approx(6)
    # A full rolling buffer applies backpressure until analysis catches up.
    monkeypatch.setattr(get_settings(), "live_buffer_seconds", 5)
    assert push(3, 6).status_code == 429
    last = buffer.buffered(db, feed)[-1][2]
    last.state = "completed"
    last.candidates = []
    db.commit()
    advance(db, feed, storage, time.time())
    monkeypatch.setattr(get_settings(), "live_buffer_seconds", 6)
    assert push(3, 6).status_code == 200
    db.refresh(feed)
    assert len(buffer.buffered(db, feed)) == 3
    assert len(db.scalars(select(Moment).where(Moment.feed_id == fid)).all()) == 1
    assert c.put(path + "/end", headers=headers).json()["state"] == "ending"
    last = buffer.buffered(db, feed)[-1][2]
    last.state = "completed"
    last.candidates = []
    db.commit()
    db.refresh(feed)
    for _ in range(4):
        advance(db, feed, storage, time.time())
    assert feed.state == "ended" and not buffer.buffered(db, feed)
    assert push(4, 8).status_code == 401
    assert not list(storage.root.glob("*.partial"))


def test_logged_out_callback_cannot_connect(isolated, monkeypatch):
    c, db, _ = isolated
    mock = ProviderMock()
    monkeypatch.setattr(http, "request_json", mock)
    state = begin(c, "youtube")["state"][0]
    assert c.delete("/api/session").status_code == 200
    res = callback(c, "youtube", {"state": state, "code": "unused"})
    assert res.status_code == 403
    assert db.get(Connection, "youtube") is None and not mock.grants


def test_youtube_recording_association(isolated, monkeypatch, recording):
    c, _, _ = isolated
    monkeypatch.setattr(http, "request_json", ProviderMock())
    state = begin(c, "youtube")["state"][0]
    assert callback(c, "youtube", {"state": state, "code": "ok"}).status_code == 303
    video = c.post(
        "/api/videos", content=recording, headers={"content-type": "video/mp4"}
    )
    assert video.is_success, video.text
    key = video.json()["id"]
    res = c.post(
        "/api/providers/youtube/recordings",
        json={"broadcast": "owned", "video_id": key},
    )
    assert res.is_success, res.text
    assert c.get("/api/providers/youtube/broadcasts").json()[0]["recordings"] == [key]
    assert c.get("/api/videos/" + key + "/preview").status_code == 200


def test_linked_recordings_are_private_and_reopen(isolated):
    c, _, _ = isolated
    resource = "/api/recording-links"
    link = dict(
        kind="youtube",
        external_id="dQw4w9WgXcQ",
        title="Podcast",
        start=10700,
        end=10730,
    )
    assert c.post(resource, json=link).json() == link
    link["end"] += 10
    assert c.post(resource, json=link).json() == link
    assert c.get(resource).json() == [link]
    assert (
        c.post(
            resource, json={**link, "external_id": "https://example.com/a"}
        ).status_code
        == 422
    )
    assert c.delete("/api/session").is_success
    assert (
        c.get(resource).status_code == 401
        and c.post(resource, json=link).status_code == 401
    )


def test_owned_vod_selection(isolated, monkeypatch):
    c, _, _ = isolated
    mock = ProviderMock()
    owner = "123"
    created = []

    def helix(url, **kwargs):
        args = parse_qs(urlsplit(url).query)
        if "/helix/videos/clips?" in url:
            assert kwargs["method"] == "POST"
            created.append(args)
            return {"data": [{"id": "OwnedClip"}]}
        if "/helix/videos?" in url:
            return {"data": [{"id": "98765", "user_id": owner, "duration": "3h0m0s"}]}
        return mock(url, **kwargs)

    monkeypatch.setattr(http, "request_json", helix)
    state = begin(c, "twitch")["state"][0]
    assert callback(c, "twitch", {"state": state, "code": "ok"}).status_code == 303
    endpoint = "/api/providers/twitch/vod-clips"
    selection = {
        "vod_id": "98765",
        "start": 10770,
        "end": 10800,
        "title": "Last moment",
    }
    assert c.post(endpoint, json={**selection, "end": 10803}).status_code == 422
    assert c.post(endpoint, json={**selection, "start": 0}).status_code == 422
    owner = "someone_else"
    assert c.post(endpoint, json=selection).status_code == 403 and not created
    owner = "123"
    assert c.post(endpoint, json=selection).json() == {
        "id": "OwnedClip",
        "state": "pending",
    }
    assert created == [
        dict(
            vod_id=["98765"],
            editor_id=["123"],
            broadcaster_id=["123"],
            vod_offset=["10800"],
            duration=["30"],
            title=["Last moment"],
        )
    ]
    assert c.get("/api/providers/twitch/clips/OwnedClip").json()["state"] == "ready"
