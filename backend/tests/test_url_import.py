import io
import ipaddress
import os
import subprocess
from unittest.mock import MagicMock, patch
from uuid import UUID, uuid4

import pytest
from sqlalchemy.orm import Session

from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.media import Job, VideoImport
from app.services import url_ingestion as ingestion
from app.services.storage.local import LocalMedia
from app.workers import imports, rendering

URL = "https://recordings.example/movie.mp4?signature=secret"
PUBLIC = ipaddress.ip_address("1.1.1.1")


@pytest.mark.parametrize(
    "url",
    [
        "movie.mp4",
        "http://files.example/x",
        "https://operator@files.example/x",
        "https://127.0.0.1/x",
        "https://localhost/x",
        "https://[::1]/x",
        "https://169.254.169.254/x",
        "https://10.0.0.1/x",
        "https://224.0.0.1/x",
        "https://168.63.129.16/x",
        "https://[::ffff:127.0.0.1]/x",
        "https://youtu.be/example",
        "https://www.twitch.tv/videos/123",
        "https://www.youtube.com/watch?v=abc",
    ],
)
def test_blocked_sources(url):
    with pytest.raises(StudioError):
        ingestion.destination(url)


def test_mixed_dns_answers(monkeypatch):
    monkeypatch.setattr(
        ingestion, "resolve", lambda _: [PUBLIC, ipaddress.ip_address("10.0.0.1")]
    )
    with pytest.raises(StudioError, match="import_destination"):
        ingestion.destination(URL)


def test_connect_uses_validated_address_and_original_tls_name():
    conn = ingestion.PinnedHTTPS("recordings.example", PUBLIC, 3)
    with (
        patch.object(
            ingestion.socket, "getaddrinfo", side_effect=AssertionError("DNS relookup")
        ),
        patch.object(ingestion.socket, "socket") as factory,
        patch.object(conn._context, "wrap_socket") as wrap,
    ):
        conn.connect()
        raw = factory.return_value.__enter__.return_value
        raw.connect.assert_called_once_with(("1.1.1.1", 443))
        wrap.assert_called_once_with(raw, server_hostname="recordings.example")


def fake_server(monkeypatch, body=b"", status=200, headers=None):
    response = MagicMock()
    response.__enter__.return_value = response
    response.status = status
    response.getheader.side_effect = (headers or {}).get
    response.read1.side_effect = io.BytesIO(body).read
    conn = MagicMock()
    conn.getresponse.return_value = response
    factory = MagicMock(return_value=conn)
    monkeypatch.setattr(ingestion, "PinnedHTTPS", factory)
    return factory, response


def test_redirect_revalidates_before_connect(tmp_path, monkeypatch):
    factory, _ = fake_server(
        monkeypatch, status=302, headers={"Location": "https://127.0.0.1/private"}
    )
    monkeypatch.setattr(
        ingestion,
        "resolve",
        lambda h: [PUBLIC] if h == "recordings.example" else [ipaddress.ip_address(h)],
    )
    with pytest.raises(StudioError, match="import_destination"):
        ingestion.download(URL, tmp_path / "test", lambda *_: None)
    assert factory.call_count == 1


@pytest.mark.parametrize(
    ("body", "headers", "status", "error"),
    [
        (b"", {"Content-Length": "10000000000000"}, 200, "import_size"),
        (b"1" * 65537, {}, 200, "import_size"),
        (b"incomplete", {"Content-Length": "20"}, 200, "import_incomplete"),
        (b"", {}, 403, "import_access"),
        (b"", {"Location": "/again"}, 302, "import_redirect"),
    ],
)
def test_failed_downloads(tmp_path, monkeypatch, body, headers, status, error):
    fake_server(monkeypatch, body, status, headers)
    monkeypatch.setattr(ingestion, "resolve", lambda _: [PUBLIC])
    monkeypatch.setattr(get_settings(), "import_max_bytes", 65536)
    with pytest.raises(StudioError, match=error):
        with LocalMedia(tmp_path).stage(uuid4()) as path:
            ingestion.download(URL, path, lambda *_: None)
    assert not list(tmp_path.iterdir())


@pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1", reason="requires private studio"
)
@pytest.mark.parametrize(
    "mode",
    ["valid", "invalid", "interrupted", "cancel", "cancel_active", "crash", "rebind"],
)
def test_import_lifecycle(mode, studio, recording, monkeypatch):
    c, root = studio
    monkeypatch.setattr(ingestion, "resolve", lambda _: [PUBLIC])
    factory, response = fake_server(
        monkeypatch,
        recording if mode != "invalid" else b"not a video",
        headers={"Content-Type": "video/mp4"},
    )
    engine = c.app.state.dependencies.db
    submitted = c.post("/api/imports", json={"url": URL})
    assert submitted.status_code == 202 and "signature" not in submitted.text
    key = UUID(submitted.json()["id"])
    storage = LocalMedia(root)
    with Session(engine, expire_on_commit=False) as db:
        job = db.get(VideoImport, key)
        try:
            assert "secret" not in job.sealed_url
            if mode == "cancel":
                assert c.delete(f"/api/imports/{key}").json()["state"] == "cancelled"
            else:
                if mode == "crash":
                    job.state = "downloading"
                    db.commit()
                    storage.path(key).with_suffix(".partial").write_bytes(b"abandoned")
                if mode == "interrupted":
                    response.read1.side_effect = [b"first", ConnectionResetError()]
                if mode == "rebind":
                    monkeypatch.setattr(
                        ingestion, "resolve", lambda _: [ipaddress.ip_address("::1")]
                    )
                if mode == "cancel_active":

                    def abort(_):
                        assert (
                            c.delete(f"/api/imports/{key}").json()["state"]
                            == "cancelling"
                        )
                        return b""

                    response.read1.side_effect = abort
                imports.process(db, job, storage)
            db.refresh(job)
            if mode == "valid":
                assert job.state == "completed" and job.received == len(recording)
                assert c.get(f"/api/videos/{key}/preview").content == recording
                render_id = uuid4()
                assert (
                    c.post(
                        f"/api/videos/{key}/clips",
                        json={"request_id": str(render_id), "start": 0, "end": 1},
                    ).status_code
                    == 202
                )
                rendering.process(db, db.get(Job, render_id), storage)
                assert b"ftyp" in c.get(f"/api/clips/{render_id}/download").content[:32]
                assert c.post(f"/api/videos/{key}/analysis").is_success
            else:
                assert job.state == (
                    "cancelled" if mode.startswith("cancel") else "failed"
                )
                assert not storage.path(key).exists()
            if mode == "rebind":
                factory.assert_not_called()
            assert job.sealed_url is None and not list(root.glob("*.partial"))
        finally:
            db.delete(job)
            db.commit()
    assert c.delete("/api/session").is_success
    assert c.post("/api/imports", json={"url": URL}).status_code == 401
    assert c.get("/api/imports").status_code == 401
    assert c.delete(f"/api/imports/{key}").status_code == 401


def test_three_hour_recording_uses_import_duration_limit(tmp_path):
    from app.services.media import validate_source

    path = tmp_path / "podcast.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=s=32x32:r=1/60",
            "-t",
            "10800",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
    )
    assert validate_source(path, duration_limit=14400)["duration"] == 10800
    with pytest.raises(StudioError, match="duration_limit"):
        validate_source(path, duration_limit=10799)


def test_disk_and_bandwidth_limits(tmp_path, monkeypatch):
    monkeypatch.setattr(ingestion, "resolve", lambda _: [PUBLIC])
    cfg = get_settings()
    fake_server(monkeypatch, b"abc")
    monkeypatch.setattr(cfg, "import_reserve_bytes", 2**62)
    with pytest.raises(StudioError, match="import_space"):
        ingestion.download(URL, tmp_path / "sample", lambda *_: None)
    monkeypatch.setattr(cfg, "import_reserve_bytes", 1)
    fake_server(monkeypatch, b"x" * 65536)
    monkeypatch.setattr(cfg, "import_bytes_per_second", 65536)
    now = [0.0]
    clock = MagicMock()
    clock.monotonic.side_effect = lambda: now[0]
    clock.sleep.side_effect = lambda t: now.__setitem__(0, now[0] + t)
    monkeypatch.setattr(ingestion, "time", clock)
    assert ingestion.download(URL, tmp_path / "ok", lambda *_: None) == 65536
    assert now[0] >= 1
    monkeypatch.setattr(cfg, "import_download_seconds", 1)
    fake_server(monkeypatch, b"x" * 65536)
    with pytest.raises(StudioError, match="import_timeout"):
        ingestion.download(URL, tmp_path / "late", lambda *_: None)


@pytest.mark.skipif(os.getenv("RUN_INTEGRATION") != "1", reason="requires studio")
def test_import_large_mp4_in_bounded_reads(studio, recording, monkeypatch):
    """Append a valid ISO BMFF free-space box, streamed without a giant fixture."""
    import struct

    client, root = studio
    box_size = 64 * 1024**2
    prefix = recording + struct.pack(">I4s", box_size, b"free")
    size = len(recording) + box_size
    offset = 0

    def read(count):
        nonlocal offset
        assert 0 < count <= ingestion.CHUNK
        length = min(count, size - offset)
        data = prefix[offset : offset + length].ljust(length, b"\0")
        offset += length
        return data

    monkeypatch.setattr(ingestion, "resolve", lambda _: [PUBLIC])
    monkeypatch.setattr(get_settings(), "import_bytes_per_second", 128 * 1024**2)
    _, response = fake_server(monkeypatch, headers={"Content-Length": str(size)})
    response.read1.side_effect = read
    job_id = UUID(client.post("/api/imports", json={"url": URL}).json()["id"])
    with Session(client.app.state.dependencies.db, expire_on_commit=False) as db:
        job = db.get(VideoImport, job_id)
        try:
            imports.process(db, job, LocalMedia(root))
            assert job.state == "completed" and job.received == offset == size
            assert LocalMedia(root).path(job_id).stat().st_size == size
        finally:
            db.delete(job)
            db.commit()
