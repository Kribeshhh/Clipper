import hmac
from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import func, select, text

from app.api.media import DB, require
from app.api.providers import twitch_clip
from app.api.session import operator
from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.media import RecordingLink, Video, VideoImport
from app.services.analysis.requests import queue_automatic
from app.services.recording_sources import direct_recording, resolve_recording
from app.services.url_ingestion import url_cipher

router = APIRouter(prefix="/api/imports", dependencies=[Depends(operator)])


class ImportRequest(BaseModel):
    generate_clips: bool = False
    url: SecretStr = Field(min_length=1, max_length=4096)


class ImportInfo(BaseModel):
    id: UUID
    state: str
    received: int
    total: int | None
    error: str | None


@router.get("", response_model=list[ImportInfo])
def recent(db: DB, automatic: bool = False):
    query = select(VideoImport)
    if automatic:
        query = query.where(VideoImport.generation_key.is_not(None))
    jobs = db.scalars(query.order_by(VideoImport.created_at.desc()).limit(10)).all()
    if not automatic:
        return jobs
    linked = db.execute(
        select(
            RecordingLink.video_id, func.max(RecordingLink.updated_at).label("latest")
        )
        .where(RecordingLink.video_id.is_not(None))
        .group_by(RecordingLink.video_id)
        .order_by(func.max(RecordingLink.updated_at).desc())
        .limit(10)
    )
    history = [(job.created_at, job) for job in jobs] + [
        (stamp, local_run(key)) for key, stamp in linked
    ]
    ordered = {}
    for _, job in sorted(history, key=lambda row: row[0], reverse=True):
        ordered.setdefault(job.id, job)
    return list(ordered.values())[:10]


def local_run(key):
    return ImportInfo(id=key, state="linked", received=0, total=None, error=None)


@router.post("", status_code=202, response_model=ImportInfo)
def submit(body: ImportRequest, db: DB, request: Request):
    url = body.url.get_secret_value()

    def twitch_download(clip_id):
        provider, account, token = twitch_clip(db, request, clip_id)
        return provider.download(token, account, clip_id)

    def saved_source(kind, external_id):
        link = db.get(RecordingLink, (kind, external_id), with_for_update=True)
        if link and link.video_id:
            link.updated_at = func.now()
            return link.video_id
        return None

    generation_key = None
    if body.generate_clips:
        source = resolve_recording(url, twitch_download, saved_source)
        if isinstance(source, UUID):
            require(db, Video, source)
            queue_automatic(db, source)
            db.commit()
            return local_run(source)
        generation_key = hmac.digest(
            get_settings().redis_password.get_secret_value().encode(),
            b"generation:v1:" + source.identity.encode(),
            "sha256",
        ).hex()
    else:
        source = direct_recording(url)
    db.execute(text("SELECT pg_advisory_xact_lock(219073,8)"))
    if generation_key:
        previous = db.scalar(
            select(VideoImport).where(VideoImport.generation_key == generation_key)
        )
        if previous:
            if previous.state not in {"failed", "cancelled"}:
                return previous
            previous.generation_key = None
            db.flush()
    if (
        db.scalar(
            select(func.count())
            .select_from(VideoImport)
            .where(
                VideoImport.state.in_(
                    ("queued", "downloading", "validating", "cancelling")
                )
            )
        )
        >= 4
    ):
        raise StudioError(429, "import_busy")
    job = VideoImport(
        generation_key=generation_key,
        sealed_url=url_cipher().encrypt(source.url.encode()).decode(),
    )
    db.add(job)
    db.commit()
    return job


@router.get("/{key}", response_model=ImportInfo)
def lookup(key: UUID, db: DB):
    job = db.get(VideoImport, key)
    if not job and db.scalar(
        select(RecordingLink.video_id).where(RecordingLink.video_id == key).limit(1)
    ):
        return local_run(key)
    if not job:
        raise StudioError(404, "not_found")
    return job


@router.delete("/{key}", response_model=ImportInfo)
def cancel(key: UUID, db: DB):
    job = db.get(VideoImport, key, with_for_update=True)
    if not job:
        raise StudioError(404, "not_found")
    if job.state in ("queued", "downloading", "validating"):
        job.state = "cancelled" if job.state == "queued" else "cancelling"
        job.sealed_url = None
        db.commit()
    return job
