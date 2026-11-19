from sqlalchemy import func, select, text

from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.media import Video
from app.services.analysis.requests import queue_automatic


def remaining_recording_bytes(db):
    used = db.scalar(select(func.coalesce(func.sum(Video.size), 0)))
    return get_settings().recording_quota_bytes - used


def store_recording(db, storage, temp, video, *, automatic=False):
    # Serialize ordinary uploads and provider imports at publication.
    db.execute(text("SELECT pg_advisory_xact_lock(219073,5)"))
    if video.size > remaining_recording_bytes(db):
        raise StudioError(507, "recording_quota")
    db.add(video)
    db.flush()
    if automatic:
        queue_automatic(db, video.id)
    temp.replace(storage.path(video.id))
    db.commit()
    return video
