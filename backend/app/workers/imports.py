import time

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.media import Video, VideoImport
from app.services.media import validate_source
from app.services.storage.local import LocalMedia
from app.services.storage.recordings import remaining_recording_bytes, store_recording
from app.services.url_ingestion import download, url_cipher
from app.workers.celery import app
from app.workers.rendering import engine


def finish_error(db, job, code):
    db.refresh(job, with_for_update=True)
    job.state = "cancelled" if job.state == "cancelling" else "failed"
    job.error = None if job.state == "cancelled" else code
    job.sealed_url = None
    db.commit()


def process(db, job, storage):
    cfg = get_settings()
    if job.state in {"completed", "cancelled", "failed"}:
        return
    key = job.id
    # A previous worker may have died mid-download or between rename and commit.
    with storage.stage(key) as temp:
        temp.unlink(missing_ok=True)
        if job.state != "queued":
            storage.path(key).unlink(missing_ok=True)
            finish_error(db, job, "import_interrupted")
            return
        try:
            url = url_cipher().decrypt(job.sealed_url.encode()).decode()
            remaining = remaining_recording_bytes(db)
            if remaining <= 0:
                raise StudioError(507, "recording_quota")
            job.state = "downloading"
            job.sealed_url = None
            db.commit()
            last_update = 0.0

            def checkpoint(received=None, total=None):
                nonlocal last_update
                now = time.monotonic()
                if now - last_update < 0.5:
                    return
                db.refresh(job, with_for_update=True)
                if job.state == "cancelling":
                    raise StudioError(409, "import_cancelled")
                if received is not None:
                    job.received, job.total = received, total
                db.commit()
                last_update = now

            size = download(url, temp, checkpoint, quota=remaining)
            # Only the numeric count is persisted. Discard the URL after use.
            del url
            db.refresh(job, with_for_update=True)
            if job.state == "cancelling":
                raise StudioError(409, "import_cancelled")
            job.received = size
            job.state = "validating"
            db.commit()
            meta = validate_source(
                temp,
                duration_limit=cfg.import_max_duration_seconds,
                timeout=cfg.import_verify_seconds,
                check=checkpoint,
            )
            db.refresh(job, with_for_update=True)
            if job.state == "cancelling":
                raise StudioError(409, "import_cancelled")
            job.state = "completed"
            video = Video(id=key, name=f"Imported recording {str(key)[:8]}.mp4", **meta)
            store_recording(
                db, storage, temp, video, automatic=bool(job.generation_key)
            )
        except Exception as exc:
            db.rollback()
            if db.get(Video, key) is None:
                storage.path(key).unlink(missing_ok=True)
                finish_error(
                    db,
                    job,
                    exc.code if isinstance(exc, StudioError) else "import_interrupted",
                )


@app.task(name="studio.import_next", soft_time_limit=14800, time_limit=14900)
def import_next():
    # NullPool closes the physical connection, releasing the singleton lock on exit.
    with engine().connect() as conn, Session(conn, expire_on_commit=False) as db:
        if not db.scalar(text("SELECT pg_try_advisory_lock(219073,7)")):
            return
        job = db.scalar(
            select(VideoImport)
            .where(
                VideoImport.state.in_(
                    ("queued", "downloading", "validating", "cancelling")
                )
            )
            .order_by(VideoImport.created_at)
            .with_for_update()
            .limit(1)
        )
        if job:
            process(db, job, LocalMedia(get_settings().media_root))
