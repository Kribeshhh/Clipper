import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.live import Feed
from app.services.live import buffer, highlights
from app.services.storage.local import LocalMedia
from app.workers.celery import app
from app.workers.rendering import engine


def advance(db, feed, storage, now):
    idle = now - feed.last_seen
    if feed.state in buffer.OPEN:
        if idle >= get_settings().live_idle_seconds:
            feed.state = "ending"
        elif idle >= 40:
            feed.state = "interrupted"
    rows = buffer.buffered(db, feed)
    for i, (seg, _video, analysis) in enumerate(rows):
        if seg.processed:
            continue
        if analysis.state == "failed":
            feed.error = "segment_analysis_failed"
            seg.processed = True
            continue
        if analysis.state != "completed":
            break
        left = (
            rows[i - 1] if i and highlights.contiguous(rows[i - 1], rows[i]) else None
        )
        right = (
            rows[i + 1]
            if i + 1 < len(rows) and highlights.contiguous(rows[i], rows[i + 1])
            else None
        )
        if (
            analysis.candidates
            and i + 1 == len(rows)
            and feed.state not in {"interrupted", "ending"}
        ):
            break
        if (
            right
            and right[2].state not in {"completed", "failed"}
            and analysis.candidates
        ):
            break
        highlights.publish(db, feed, left, rows[i], right, storage)
        seg.processed = True
        break
    removed = []
    if feed.state == "ending" and all(s.processed for s, _, _ in rows):
        removed = buffer.remove(db, rows)
        feed.state = "ended"
        feed.secret_hash = ""
    db.commit()
    for vid in removed:
        storage.path(vid).unlink(missing_ok=True)


@app.task(name="studio.live_tick")
def live_tick():
    with Session(engine(), expire_on_commit=False) as db:
        feed = db.scalar(
            select(Feed)
            .where(Feed.state.not_in(["ended", "failed"]))
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if not feed:
            return
        storage = LocalMedia(get_settings().media_root)
        try:
            advance(db, feed, storage, time.time())
        except Exception as exc:
            db.rollback()
            logging.getLogger(__name__).exception("Live pipeline interrupted")
            # Stop admission, preserve completed jobs and drain pending AI work.
            feed = db.get(Feed, feed.id, with_for_update=True)
            feed.error = (
                exc.code if isinstance(exc, StudioError) else "live_processing_failed"
            )
            feed.state = "ending"
            for seg, _, analysis in buffer.buffered(db, feed):
                if analysis.state in {"completed", "failed"}:
                    seg.processed = True
            db.commit()
