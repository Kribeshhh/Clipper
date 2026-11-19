import os
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text

from app.core.settings import get_settings
from app.models.media import Analysis, Clip, Job

pytestmark = pytest.mark.skipif(
    os.getenv("RUN_INTEGRATION") != "1",
    reason="requires migrated PostgreSQL, Redis and FFmpeg",
)


def upload(c, data):
    return c.post(
        "/api/videos",
        content=data,
        headers={"content-type": "video/mp4", "x-file-name": "..%2Fmatch.mp4"},
    )


def test_access(studio):
    c, _ = studio
    key = str(uuid4())
    token = c.cookies.get("clipper_session")
    assert c.delete("/api/session").status_code == 200
    c.cookies.set("clipper_session", token)
    for url in [
        "/studio",
        f"/videos/{key}/analysis",
        f"/videos/{key}",
        f"/videos/{key}/preview",
        f"/jobs/{key}",
        f"/videos/{key}/jobs",
        f"/clips/{key}/preview",
        f"/clips/{key}/download",
    ]:
        assert c.get("/api" + url).status_code == 401
    assert upload(c, b"private").status_code == 401
    assert c.post("/api/session", json={"key": "incorrect"}).status_code == 401
    del c.headers["x-studio"]
    assert c.post("/api/session", json={"key": "incorrect"}).status_code == 403


def test_invalid_sources(studio, recording, monkeypatch):
    c, root = studio
    assert upload(c, b"not a video").status_code == 422
    assert upload(c, recording[: len(recording) // 2]).status_code == 422
    assert upload(c, recording.replace(b"isom", b"qt  ", 1)).status_code == 422
    assert (
        c.post(
            "/api/videos", content=recording, headers={"content-type": "text/plain"}
        ).status_code
        == 415
    )
    monkeypatch.setattr(get_settings(), "max_duration_seconds", 1)
    assert upload(c, recording).json()["error"]["code"] == "duration_limit"
    monkeypatch.setattr(get_settings(), "max_upload_bytes", 64)
    assert upload(c, recording).status_code == 413
    assert upload(c, iter([b"x" * 40, b"y" * 40])).status_code == 413
    assert not list(root.iterdir())


@pytest.fixture
def reserved_job(studio):
    client, _ = studio
    key = uuid4()
    lock = int.from_bytes(key.bytes[:8], "big", signed=True)
    with client.app.state.dependencies.db.connect() as conn:
        conn.execute(text("SELECT pg_advisory_lock(:lock)"), {"lock": lock})
        try:
            yield str(key)
        finally:
            conn.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": lock})


def test_render_flow(studio, recording, monkeypatch, reserved_job):
    from app.workers import rendering

    c, root = studio
    uploaded = upload(c, recording)
    assert uploaded.status_code == 201, uploaded.text
    video = uploaded.json()
    assert (
        video["name"],
        video["width"],
        video["height"],
        video["fps"],
        video["has_audio"],
    ) == ("match.mp4", 160, 90, 24, True)
    source = "/api/videos/" + video["id"]
    assert c.get(source).json() == video
    assert (
        c.get(source + "/preview", headers={"Range": "bytes=0-11"}).content
        == recording[:12]
    )
    key = reserved_job

    def submit(start, end):
        return c.post(
            source + "/clips", json={"request_id": key, "start": start, "end": end}
        )

    for start, end in [(-1, 1), (1, 1), (1, 0.5), (0, 3), (0, 0.001)]:
        assert submit(start, end).status_code == 422
    assert submit(0.25, 1.25).json()["state"] == "queued"
    assert submit(0.25, 1.25).json()["id"] == key
    assert submit(0, 1).status_code == 409
    job_url = "/api/jobs/" + key

    def broken(source, temp, start, end, progress, **options):
        assert c.get(job_url).json()["state"] == "rendering"
        temp.write_bytes(b"partial")
        raise OSError("test failure")

    # Select only this job for processing; other database jobs belong to real users.
    from sqlalchemy.orm import Session

    from app.services.storage.local import LocalMedia

    def work():
        with Session(c.app.state.dependencies.db, expire_on_commit=False) as db:
            rendering.process(db, db.get(Job, UUID(key)), LocalMedia(root))

    with monkeypatch.context() as patch:
        patch.setattr(rendering, "render", broken)
        work()
    assert c.get(job_url).json()["state"] == "failed"
    assert not list(root.glob("*.partial"))
    assert c.get("/api/clips/" + key + "/preview").status_code == 404
    assert c.post(job_url + "/retry").json()["state"] == "queued"
    work()
    assert c.get(job_url).json()["progress"] == 100
    assert c.get(job_url).json()["state"] == "completed"
    assert len(c.get(source + "/jobs").json()) == 1
    output = c.get("/api/clips/" + key + "/download")
    assert "attachment" in output.headers["content-disposition"]
    assert output.content == c.get("/api/clips/" + key + "/preview").content
    from app.services.media import probe

    meta = probe(next(root.glob("clip-*.mp4")))
    assert abs(meta["duration"] - 1) < 0.05
    assert meta["has_audio"] and not list(root.glob("*.partial"))


def test_abandoned_file_cleanup(studio, recording, monkeypatch):
    from app.workers import rendering

    c, root = studio
    video = upload(c, recording).json()
    kept = root / ("video-" + UUID(video["id"]).hex + ".mp4")
    old_partial = root / ("video-" + uuid4().hex + ".partial")
    old_orphan = root / ("clip-" + uuid4().hex + ".mp4")
    recent = root / ("video-" + uuid4().hex + ".partial")
    for path in [old_partial, old_orphan, recent]:
        path.touch()
    for path in [kept, old_partial, old_orphan]:
        os.utime(path, (1, 1))
    monkeypatch.setattr(rendering, "engine", lambda: c.app.state.dependencies.db)
    rendering.clean_abandoned()
    assert kept.exists() and recent.exists()
    assert not old_partial.exists() and not old_orphan.exists()


def test_analysis_inference_failure_cache_and_worker_retry(
    studio, recording, monkeypatch, tmp_path
):
    pytest.importorskip("cv2")
    from sqlalchemy.orm import Session

    from app.services.analysis import signals
    from app.workers import analysis

    c, _ = studio
    vid = UUID(upload(c, recording).json()["id"])
    url = f"/api/videos/{vid}/analysis"
    assert c.get(url).json()["state"] == "idle"
    with (
        c.app.state.dependencies.db.connect() as conn,
        Session(conn, expire_on_commit=False) as db,
    ):
        # Keep the running scheduler away from this test case.
        db.execute(text("SELECT pg_advisory_lock(219073, 2)"))
        try:
            assert c.post(url).json()["state"] == "queued"
            assert c.post(url).json()["state"] == "queued"
            job = db.get(Analysis, vid)

            def unavailable(*args):
                raise RuntimeError("Injected inference failure")

            monkeypatch.setattr(signals, "whisper", unavailable)
            analysis.process(db, job, tmp_path)
            degraded = c.get(url).json()
            assert degraded["state"] == "completed" and degraded["warning"]
            assert job.transcript is None and job.samples and job.levels
            assert c.post(url).json()["state"] == "queued"
            monkeypatch.setattr(signals, "whisper", lambda path: None)
            monkeypatch.setattr(signals, "transcribe", lambda *args: [])
            monkeypatch.setattr(signals, "visual_samples", unavailable)
            db.refresh(job)
            analysis.process(db, job, tmp_path)
            assert job.state == "completed" and job.warning is None
            assert c.post(url).json()["state"] == "completed"
            monkeypatch.setattr(get_settings().highlight_weights, "audio", 2.5)
            assert c.get(url).json()["outdated"]
            assert c.post(url).json()["state"] == "queued"
            db.refresh(job)
            monkeypatch.setattr(signals, "extract_audio", unavailable)
            analysis.process(db, job, tmp_path)
            assert job.state == "completed" and job.weights["audio"] == 2.5
            # Interrupted worker without a visual checkpoint.
            job.samples = None
            job.state = "running"
            db.commit()
            analysis.process(db, job, tmp_path)
            assert c.get(url).json()["state"] == "failed"
            assert c.post(url).json()["state"] == "queued"
            assert not list(tmp_path.glob("run-*"))
        finally:
            db.execute(text("SELECT pg_advisory_unlock(219073, 2)"))
            db.commit()


def test_portrait_batch_is_atomic_and_retryable(
    studio, recording, reserved_job, monkeypatch
):
    from sqlalchemy.orm import Session

    from app.services.storage.local import LocalMedia
    from app.workers import rendering

    c, root = studio
    video = UUID(upload(c, recording).json()["id"])
    base = f"/api/videos/{video}"
    jobs = base + "/clips/batch"
    options = {"captions": True, "position": 0.5}
    first = {"request_id": reserved_job, "start": 0.25, "end": 1.5, "portrait": options}
    other = uuid4()
    second = {**first, "request_id": str(other), "start": 0.5}
    assert c.post(jobs, json={"selections": [first]}).status_code == 409
    storage = LocalMedia(root)
    with (
        c.app.state.dependencies.db.connect() as conn,
        Session(conn, expire_on_commit=False) as db,
    ):
        lock = int.from_bytes(other.bytes[:8], "big", signed=True)
        db.execute(text("SELECT pg_advisory_lock(:lock)"), {"lock": lock})
        try:
            from app.core.settings import SignalWeights

            analysis = Analysis(
                video_id=video,
                weights=SignalWeights().model_dump(),
                transcript=[{"start": 0.5, "end": 1.25, "text": "Good game!"}],
                state="completed",
            )
            db.add(analysis)
            db.commit()
            view = c.get(base + "/edit-preview?start=0.25&end=1.5").json()
            assert view == {
                "cues": [{"start": 0.25, "end": 1.0, "text": "Good game!"}],
                "focus": None,
                "caption_ready": True,
            }
            for invalid in [
                {**second, "end": 8},
                {**second, "portrait": {"position": 1.1}},
                {**second, "portrait": {"filter": "hflip"}},
            ]:
                assert (
                    c.post(jobs, json={"selections": [first, invalid]}).status_code
                    == 422
                )
                assert not c.get(base + "/jobs").json()
            rows = c.post(jobs, json={"selections": [first, second]}).json()
            assert [j["id"] for j in rows] == [reserved_job, str(other)]
            assert c.post(jobs, json={"selections": [first, second]}).json() == rows
            assert (
                c.post(
                    base + "/clips", json={**first, "portrait": {"position": 0}}
                ).status_code
                == 409
            )
            job = db.get(Job, UUID(reserved_job))
            assert job.edit["cues"] == view["cues"]
            analysis.transcript = []
            db.commit()
            with monkeypatch.context() as patch:
                patch.setattr(rendering, "remaining_bytes", lambda db: 128)
                rendering.process(db, job, storage)
            assert job.state == "failed" and job.error == "export_quota"
            assert not list(root.glob("*.partial"))
            assert (
                c.post("/api/jobs/" + reserved_job + "/retry").json()["state"]
                == "queued"
            )
            db.refresh(job)
            for item in (job, db.get(Job, other)):
                rendering.process(db, item, storage)
                assert item.state == "completed"
                assert db.get(Clip, item.id).details["height"] == 1280
                assert c.get(f"/api/clips/{item.id}/download").status_code == 200
            assert len(list(root.glob("clip-*.mp4"))) == 2
        finally:
            db.execute(text("SELECT pg_advisory_unlock(:lock)"), {"lock": lock})
            db.commit()


def test_private_owner_rotation_and_origin(studio, monkeypatch):
    from pydantic import SecretStr

    from app.core.settings import get_settings

    c, _ = studio
    key = get_settings().operator_key.get_secret_value()
    previous = c.cookies.get("clipper_session")
    assert (
        c.post(
            "/api/session",
            json={"key": key},
            headers={"Origin": "https://different.invalid"},
        ).status_code
        == 403
    )
    assert c.post("/api/session", json={"key": key}).is_success
    current = c.cookies.get("clipper_session")
    assert previous != current
    assert (
        c.get(
            "/api/studio", headers={"Cookie": "clipper_session=" + previous}
        ).status_code
        == 401
    )
    assert c.get("/api/studio").is_success
    monkeypatch.setattr(get_settings(), "operator_key", SecretStr("x" * 64))
    for path in ("/studio", "/live", "/providers", "/providers/twitch/clips"):
        assert c.get("/api" + path).status_code == 401


def test_unlock_throttle(studio):
    c, _ = studio
    redis = c.app.state.dependencies.redis
    try:
        for _ in range(20):
            assert c.post("/api/session", json={"key": "invalid"}).status_code == 401
        assert c.post("/api/session", json={"key": "invalid"}).status_code == 429
        assert 0 < redis.ttl("studio:login-attempts") <= 60
    finally:
        redis.delete("studio:login-attempts")


def test_recording_quota(studio, recording, monkeypatch):
    from app.core.settings import get_settings

    c, root = studio
    monkeypatch.setattr(get_settings(), "recording_quota_bytes", 1)
    assert upload(c, recording).json()["error"]["code"] == "recording_quota"
    assert not list(root.glob("*"))
