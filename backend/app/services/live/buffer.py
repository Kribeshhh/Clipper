import hashlib
import secrets
import time

from sqlalchemy import delete, select
from sqlalchemy.orm import load_only

from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.live import Feed, Segment
from app.models.media import Analysis, Video

OPEN = {"waiting", "receiving", "interrupted"}
MAX_SEGMENT_BYTES = 32 * 1024**2
MAX_SEGMENT_SECONDS = 30


def authenticate(db, key, bearer, *, lock=False):
    feed = db.get(Feed, key, with_for_update=lock, populate_existing=True)
    prefix = "Bearer "
    token = bearer[len(prefix) :] if bearer.startswith(prefix) else ""
    digest = hashlib.sha256(token.encode()).hexdigest()
    if (
        not feed
        or len(token) != 43
        or not secrets.compare_digest(feed.secret_hash, digest)
    ):
        raise StudioError(401, "invalid_feed_key")
    return feed


def buffered(db, feed, *, metadata_only=False):
    query = (
        select(Segment, Video, Analysis)
        .join(Video, Segment.video_id == Video.id)
        .join(Analysis, Analysis.video_id == Video.id)
        .where(Segment.feed_id == feed.id)
        .order_by(Segment.sequence)
    )
    if metadata_only:
        query = query.options(load_only(Video.size), load_only(Analysis.video_id))
    return list(db.execute(query))


def remove(db, rows):
    ids = [v.id for _, v, _ in rows]
    if ids:
        db.execute(delete(Segment).where(Segment.video_id.in_(ids)))
        db.execute(delete(Analysis).where(Analysis.video_id.in_(ids)))
        db.execute(delete(Video).where(Video.id.in_(ids)))
    return ids


def accept(db, feed, sequence, start, digest, meta, video_id):
    cfg = get_settings()
    old = db.get(Segment, (feed.id, sequence))
    if old:
        if old.digest != digest:
            raise StudioError(409, "segment_conflict")
        return False, []
    if feed.state not in OPEN:
        raise StudioError(409, "feed_closed")
    if sequence <= feed.latest or start < feed.end_time - 0.25:
        raise StudioError(409, "segment_sequence")
    if sequence == feed.latest + 1 and abs(start - feed.end_time) <= 0.25:
        start = feed.end_time
    end = start + meta["duration"]
    if end > 86400:
        raise StudioError(422, "feed_duration_limit")
    rows = buffered(db, feed, metadata_only=True)
    # Always keep the preceding segment for pre-event context until its successor
    # has been handled. Backpressure protects unprocessed observations.
    keep_from = next(
        (i - 1 for i, (s, _, _) in enumerate(rows) if not s.processed), len(rows) - 1
    )
    eligible = rows[: max(0, keep_from)]
    retained = list(rows)
    discarded = []
    size = sum(v.size for _, v, _ in retained) + meta["size"]
    while retained and (
        end - retained[0][0].start > cfg.live_buffer_seconds
        or size > cfg.live_buffer_bytes
        or len(retained) >= 64
    ):
        if not eligible or retained[0] != eligible[0]:
            # A genuine timeline gap requires no context from before the gap.
            if (
                all(s.processed for s, _, _ in retained)
                and start > feed.end_time + 0.25
            ):
                discarded.extend(retained)
                retained.clear()
                size = meta["size"]
                break
            raise StudioError(429, "live_buffer_full")
        row = eligible.pop(0)
        retained.pop(0)
        discarded.append(row)
        size -= row[1].size
    if size > cfg.live_buffer_bytes:
        raise StudioError(413, "segment_size")
    removed = remove(db, discarded)
    db.add(Video(id=video_id, name=f"Segment {sequence}.mp4", **meta))
    db.flush()
    db.add(Analysis(video_id=video_id, weights=cfg.highlight_weights.model_dump()))
    db.add(
        Segment(
            feed_id=feed.id,
            sequence=sequence,
            start=start,
            digest=digest,
            video_id=video_id,
        )
    )
    feed.latest, feed.end_time, feed.state, feed.last_seen = (
        sequence,
        end,
        "receiving",
        time.time(),
    )
    return True, removed
