import asyncio
import hashlib
import secrets
import time
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat, model_validator
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from app.api.media import DB
from app.api.providers import own_broadcast
from app.api.session import operator
from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.live import Feed, Moment, Segment
from app.models.media import Analysis, Job
from app.services.live import buffer
from app.services.media import validate_source
from app.services.providers.oauth import Provider
from app.services.storage.local import LocalMedia

router = APIRouter()
private = APIRouter(prefix="/api/live", dependencies=[Depends(operator)])


class Start(BaseModel):
    model_config = ConfigDict(extra="forbid")
    provider: Provider
    broadcast: str = Field(min_length=1, max_length=128)

    @model_validator(mode="before")
    @classmethod
    def reject_url(cls, value):
        if (
            isinstance(value, dict)
            and {"url", "stream_url", "media_url"} & value.keys()
        ):
            raise StudioError(422, "independent_media_required")
        return value


@private.post("", status_code=201)
def start(body: Start, request: Request, db: DB):
    account, item = own_broadcast(db, request, body.provider, body.broadcast)
    if item["state"] != "live":
        raise StudioError(409, "stream_offline")
    db.execute(text("SELECT pg_advisory_xact_lock(219073,4)"))
    if db.scalar(
        select(Feed.id).where(Feed.state.not_in(["ended", "failed"])).limit(1)
    ):
        raise StudioError(409, "feed_busy")
    secret = secrets.token_urlsafe(32)
    feed = Feed(
        provider=body.provider,
        account=account,
        broadcast=body.broadcast,
        title=item["title"],
        secret_hash=hashlib.sha256(secret.encode()).hexdigest(),
        last_seen=time.time(),
    )
    db.add(feed)
    db.commit()
    return {"id": feed.id, "ingest_key": secret}


@private.get("")
def feeds(db: DB):
    result = []
    for f in db.scalars(select(Feed).order_by(Feed.last_seen.desc()).limit(10)):
        moments = db.execute(
            select(Moment, Job)
            .join(Job, Job.id == Moment.job_id)
            .where(Moment.feed_id == f.id)
            .order_by(Moment.start.desc())
            .limit(50)
        )
        pending = db.execute(
            select(Analysis.stage, Analysis.progress)
            .join(Segment, Segment.video_id == Analysis.video_id)
            .where(Segment.feed_id == f.id, Segment.processed.is_(False))
            .order_by(Segment.sequence)
            .limit(1)
        ).first()
        result.append(
            {
                "id": f.id,
                "title": f.title,
                "state": f.state,
                "latest": f.latest,
                "end": f.end_time,
                "error": f.error,
                "stage": pending.stage if pending else None,
                "progress": pending.progress if pending else None,
                "provider": f.provider,
                "moments": [
                    {
                        "job": j.id,
                        "progress": j.progress,
                        "video": j.video_id,
                        "state": j.state,
                        "start": m.start,
                        "end": m.end,
                        "score": m.evidence["score"],
                        "signals": list(m.evidence["signals"]),
                    }
                    for m, j in moments
                ],
            }
        )
    return result


def end(feed, db):
    if feed.state in buffer.OPEN:
        feed.state = "ending"
        feed.last_seen = time.time()
    db.commit()
    return {"state": feed.state}


@private.post("/{key}/stop")
def stop(key: UUID, db: DB):
    feed = db.get(Feed, key, with_for_update=True)
    if not feed:
        raise StudioError(404, "not_found")
    return end(feed, db)


@router.get("/api/live/{key}/cursor")
def cursor(key: UUID, request: Request, db: DB):
    f = buffer.authenticate(db, key, request.headers.get("authorization", ""))
    return {
        "sequence": f.latest,
        "end": f.end_time,
        "state": f.state,
        "last_seen": f.last_seen,
    }


@router.put("/api/live/{key}/end")
def eof(key: UUID, request: Request, db: DB):
    return end(
        buffer.authenticate(
            db, key, request.headers.get("authorization", ""), lock=True
        ),
        db,
    )


@router.put("/api/live/{key}/segments/{sequence}")
async def ingest(
    key: UUID, sequence: int, start: FiniteFloat, request: Request, db: DB
):
    auth = request.headers.get("authorization", "")
    feed = buffer.authenticate(db, key, auth)
    if feed.state not in buffer.OPEN:
        raise StudioError(409, "feed_closed")
    db.commit()
    if not 0 <= sequence <= 100000 or not 0 <= start < 86400:
        raise StudioError(422, "segment_sequence")
    if request.headers.get("content-type") != "video/mp4":
        raise StudioError(415, "unsupported_media")
    storage = LocalMedia(get_settings().media_root)
    limit = min(
        get_settings().max_upload_bytes,
        buffer.MAX_SEGMENT_BYTES,
        get_settings().live_buffer_bytes // 4,
    )
    slots = request.app.state.upload_slots
    if slots.locked():
        raise StudioError(429, "studio_busy")
    video_id = uuid4()
    async with slots:
        with storage.stage(video_id) as tmp:
            digest = hashlib.sha256()
            try:
                async with asyncio.timeout(90):
                    with tmp.open("xb") as f:
                        async for chunk in request.stream():
                            if f.tell() + len(chunk) > limit:
                                raise StudioError(413, "segment_size")
                            digest.update(chunk)
                            await run_in_threadpool(f.write, chunk)
            except TimeoutError as exc:
                raise StudioError(408, "upload_timeout") from exc
            meta = await run_in_threadpool(
                validate_source, tmp, buffer.MAX_SEGMENT_SECONDS
            )
            feed = buffer.authenticate(db, key, auth, lock=True)
            created, removed = buffer.accept(
                db, feed, sequence, start, digest.hexdigest(), meta, video_id
            )
            if created:
                tmp.replace(storage.path(video_id))
            db.commit()
            for vid in removed:
                storage.path(vid).unlink(missing_ok=True)
    return {"sequence": sequence, "accepted": created}


router.include_router(private)
