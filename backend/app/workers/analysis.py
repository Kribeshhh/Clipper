import logging
import os
import shutil
import stat
import time
from pathlib import Path
from tempfile import TemporaryDirectory, gettempdir

from billiard.exceptions import SoftTimeLimitExceeded
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.core.settings import get_settings
from app.models.media import Analysis, Video
from app.services.storage.local import LocalMedia
from app.workers.celery import app
from app.workers.rendering import engine

logger = logging.getLogger(__name__)


def analyze(db, job, video, scratch):
    from app.services.analysis import signals
    from app.services.analysis.scoring import rank

    settings = get_settings()
    source = LocalMedia(settings.media_root).path(video.id)

    def stage(name, percent):
        job.stage, job.progress = name, percent
        db.commit()

    job.state = "running"
    job.attempts += 1
    job.warning = None
    stage("Preparing analysis", job.progress if job.checkpoint else 2)
    from app.services.analysis import chunks

    if video.duration > chunks.WINDOW_SECONDS and (
        job.checkpoint
        or job.samples is None
        or job.transcript is None
        or job.levels is None
    ):
        if not chunks.advance(db, job, video, source, scratch, settings):
            return
    wav = scratch / "audio.wav"
    if not video.has_audio:
        job.levels, job.transcript = [], []
    elif job.levels is None or job.transcript is None:
        stage("Extracting audio", 5)
        signals.extract_audio(source, wav, video.duration)
        if job.levels is None:
            job.levels = signals.audio_levels(wav)
        stage("Transcribing speech", 15)
        if job.transcript is None:
            if max(job.levels, default=-100) < -60:
                job.transcript = []
            else:

                def progress(fraction):
                    percent = 15 + int(50 * fraction)
                    if percent >= job.progress + 2:
                        stage("Transcribing speech", percent)

                try:
                    job.transcript = signals.transcribe(
                        wav,
                        video.duration,
                        signals.whisper(settings.whisper_model_dir),
                        progress,
                    )
                except SoftTimeLimitExceeded:
                    raise
                except (OSError, ValueError, RuntimeError, ImportError):
                    logger.exception("Speech inference unavailable")
                    job.warning = "Speech unavailable. Ranking uses remaining signals."
        wav.unlink()
    stage("Sampling visual activity", 68)
    if job.samples is None:
        points = []
        job.samples = signals.visual_samples(
            source, scratch / "samples.avi", video.duration, points
        )
        job.focus_points = points
    stage("Ranking highlights", 92)
    job.candidates = rank(
        video.duration,
        job.levels,
        job.samples,
        job.transcript or [],
        job.weights,
        limit=60 if job.auto_state == "pending" else 12,
    )
    job.state = "completed"
    stage("Completed", 100)


def process(db, job, scratch_root):
    try:
        if job.attempts >= 3:
            raise RuntimeError("Analysis worker interrupted repeatedly")
        with TemporaryDirectory(dir=scratch_root, prefix="run-") as tmp:
            analyze(db, job, db.get(Video, job.video_id), Path(tmp))
    except Exception:
        db.rollback()
        logger.exception("Highlight analysis failed")
        job.state, job.stage = "failed", "Analysis interrupted"
        db.commit()


@app.task(name="studio.analyze_next", soft_time_limit=1740, time_limit=1800)
def analyze_next():
    # NullPool closes the physical connection and releases its lock on every exit.
    with engine().connect() as conn, Session(conn, expire_on_commit=False) as db:
        if not db.scalar(text("SELECT pg_try_advisory_lock(219073, 2)")):
            return
        root = Path(gettempdir()) / f"clipper-analysis-{os.getuid()}"
        root.mkdir(mode=0o700, exist_ok=True)
        info = root.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or info.st_mode & 0o077
        ):
            raise RuntimeError("Private scratch directory required")
        for old in root.glob("run-*"):
            if old.stat().st_mtime < time.time() - 3600:
                shutil.rmtree(old)
        job = db.scalar(
            select(Analysis)
            .where(Analysis.state.in_(["queued", "running"]))
            .order_by(Analysis.created_at)
            .limit(1)
        )
        if job is not None:
            process(db, job, root)
        ready = db.scalar(
            select(Analysis)
            .where(Analysis.auto_state == "pending", Analysis.state == "completed")
            .order_by(Analysis.created_at)
            .limit(1)
            .with_for_update()
        )
        if ready:
            from app.workers.autoclips import schedule

            try:
                schedule(db, ready, db.get(Video, ready.video_id))
            except Exception:
                db.rollback()
                logger.exception("Automatic clip planning failed")
                ready.auto_state = "failed"
                db.commit()
