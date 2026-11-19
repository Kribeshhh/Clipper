import logging
from functools import lru_cache
from shutil import disk_usage, rmtree

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.orm import Session
from sqlalchemy.pool import NullPool

from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.media import Clip, Job, Video
from app.services.editing.render import render
from app.services.storage.local import LocalMedia
from app.workers.celery import app


@lru_cache
def engine():
    return create_engine(get_settings().database_url, poolclass=NullPool)


def remaining_bytes(db):
    used = db.scalar(select(func.coalesce(func.sum(Clip.size), 0)))
    return get_settings().export_quota_bytes - used


def process(db, job, storage):
    if job.attempts >= 3:
        job.error = "render_interrupted"
        job.state = "failed"
        db.commit()
        return
    job.state, job.progress, job.attempts = "rendering", 1, job.attempts + 1
    db.commit()

    duration = (job.edit or {}).get("duration", job.end - job.start)

    def progress(seconds):
        percent = min(94, int(94 * seconds / duration))
        if percent >= job.progress + 2:
            job.progress = percent
            db.commit()

    job.error = None
    target = storage.path(job.id, clip=True)
    try:
        with storage.stage(job.id, clip=True) as temp:
            # A killed worker may have left an open descriptor to the old inode.
            temp.unlink(missing_ok=True)
            budget = min(
                remaining_bytes(db),
                get_settings().max_upload_bytes,
                disk_usage(storage.root).free - 16 * 1024**2,
            )
            if budget < 65536:
                raise StudioError(507, "export_quota")
            result = render(
                storage.path(job.video_id),
                temp,
                job.start,
                job.end,
                progress,
                edit=job.edit,
                byte_limit=budget,
                expect_audio=db.get(Video, job.video_id).has_audio,
            )
            db.execute(text("SELECT pg_advisory_xact_lock(219073,3)"))
            if result["size"] > remaining_bytes(db):
                raise StudioError(507, "export_quota")
            temp.replace(target)
            db.add(
                Clip(
                    id=job.id,
                    duration=result["duration"],
                    size=result["size"],
                    details=result,
                )
            )
            job.state, job.progress = "completed", 100
            db.commit()
    except Exception as exc:
        db.rollback()
        logging.getLogger(__name__).exception("Clip rendering failed")
        # If the commit succeeded before a lost connection, retain its output.
        if db.get(Clip, job.id) is None:
            target.unlink(missing_ok=True)
            job.error = exc.code if isinstance(exc, StudioError) else "render_failed"
            job.state = "failed"
            db.commit()


@app.task(name="studio.render_next")
def render_next():
    # PostgreSQL is the durable queue. The session-level lock survives progress
    # commits and is released automatically when a worker connection dies.
    with engine().connect() as conn, Session(conn, expire_on_commit=False) as db:
        ids = db.scalars(
            select(Job.id)
            .where(Job.state.in_(["queued", "rendering"]))
            .order_by(Job.created_at)
            .limit(64)
        ).all()
        for key in ids:
            lock = int.from_bytes(key.bytes[:8], "big", signed=True)
            if not db.scalar(text("SELECT pg_try_advisory_lock(:key)"), {"key": lock}):
                continue
            try:
                job = db.get(Job, key)
                if job.state in {"queued", "rendering"}:
                    process(db, job, LocalMedia(get_settings().media_root))
                    return
            finally:
                db.rollback()
                db.execute(text("SELECT pg_advisory_unlock(:key)"), {"key": lock})
                db.commit()


@app.task(name="studio.clean_abandoned")
def clean_abandoned():
    """Reclaim files left by a killed upload or an interrupted database commit."""
    import time
    from uuid import UUID

    root = LocalMedia(get_settings().media_root).root
    cutoff = time.time() - 86400
    with Session(engine()) as db:
        for path in root.iterdir():
            try:
                if (
                    path.name.startswith("clip-render-")
                    and path.is_dir()
                    and not path.is_symlink()
                ):
                    if path.stat().st_mtime < cutoff:
                        rmtree(path)
                    continue
                if (
                    path.suffix not in {".partial", ".mp4"}
                    or path.stat().st_mtime > cutoff
                ):
                    continue
                kind, _, value = path.stem.partition("-")
                if kind not in {"video", "clip"}:
                    continue
                key = UUID(hex=value)
                model = Clip if kind == "clip" else Video
                if path.suffix == ".partial" or db.get(model, key) is None:
                    path.unlink(missing_ok=True)
            except (ValueError, FileNotFoundError):
                continue
