import secrets
import subprocess
from uuid import UUID

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import delete, select

from app.core.settings import get_settings
from app.main import create_app
from app.models.media import Analysis, Clip, Job, Video


@pytest.fixture(scope="module")
def recording(tmp_path_factory):
    path = tmp_path_factory.mktemp("samples") / "sample.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=160x90:rate=24",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440",
            "-t",
            "2",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-movflags",
            "+faststart",
            str(path),
        ],
        check=True,
    )
    return path.read_bytes()


@pytest.fixture
def studio(monkeypatch, tmp_path):
    key = secrets.token_hex(32)
    monkeypatch.setenv("OPERATOR_KEY", key)
    monkeypatch.setenv("APP_ENV", "development")
    monkeypatch.setenv("MEDIA_ROOT", str(tmp_path))
    get_settings.cache_clear()
    with TestClient(create_app()) as c:
        c.headers["x-studio"] = "1"
        assert c.post("/api/session", json={"key": key}).status_code == 200
        yield c, tmp_path
        with c.app.state.dependencies.db.begin() as db:
            # Only clean records owned by this test fixture.
            ids = [
                UUID(p.stem.removeprefix("video-"))
                for p in tmp_path.glob("video-*.mp4")
            ]
            jobs = select(Job.id).where(Job.video_id.in_(ids))
            db.execute(delete(Analysis).where(Analysis.video_id.in_(ids)))
            db.execute(delete(Clip).where(Clip.id.in_(jobs)))
            db.execute(delete(Job).where(Job.video_id.in_(ids)))
            db.execute(delete(Video).where(Video.id.in_(ids)))
        c.delete("/api/session")
    get_settings.cache_clear()
