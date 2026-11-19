import asyncio
import os
from contextlib import asynccontextmanager
from typing import Annotated, Literal
from urllib.parse import unquote
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field, FiniteFloat
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session
from starlette.concurrency import run_in_threadpool

from app.api.session import operator
from app.core.errors import StudioError
from app.core.settings import get_settings
from app.models.live import Segment
from app.models.media import Analysis, Clip, Job, Video
from app.services.editing.crop import suggested_focus
from app.services.editing.subtitles import prepare_cues
from app.services.editing.timing import validate_interval
from app.services.media import validate_source
from app.services.storage.local import LocalMedia
from app.services.storage.recordings import store_recording

router = APIRouter(prefix="/api", dependencies=[Depends(operator)])


def database(request: Request):
    with Session(request.app.state.dependencies.db) as session:
        yield session


DB = Annotated[Session, Depends(database)]


class VideoInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    duration: float
    width: int
    height: int
    fps: float
    codec: str
    has_audio: bool
    size: int


class Portrait(BaseModel):
    model_config = ConfigDict(extra="forbid")
    position: FiniteFloat = Field(default=0.5, ge=0, le=1)
    captions: bool = True


class Selection(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: UUID
    start: FiniteFloat = Field(ge=0)
    end: FiniteFloat = Field(gt=0)
    portrait: Portrait | None = None


class JobInfo(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    video_id: UUID
    layout: Literal["source", "vertical"]
    error: str | None
    state: str
    progress: int
    start: float
    end: float


def require(db, model, key):
    record = db.get(model, key)
    if record is None or (
        model is Video
        and db.scalar(select(Segment.video_id).where(Segment.video_id == key))
    ):
        raise StudioError(404, "not_found")
    return record


@router.get("/studio")
def studio(db: DB, response: Response):
    settings = get_settings()
    response.headers["Cache-Control"] = "private, no-store"
    return {
        "max_bytes": settings.max_upload_bytes,
        "max_seconds": settings.max_duration_seconds,
        "videos": [
            VideoInfo.model_validate(v)
            for v in db.scalars(
                select(Video)
                .where(Video.id.not_in(select(Segment.video_id)))
                .order_by(Video.created_at.desc())
                .limit(100)
            )
        ],
    }


@asynccontextmanager
async def upload_deadline():
    try:
        async with asyncio.timeout(900):
            yield
    except TimeoutError as exc:
        raise StudioError(408, "upload_timeout") from exc


@router.post("/videos", status_code=201, response_model=VideoInfo)
async def upload(request: Request, db: DB):
    settings = get_settings()
    if request.headers.get("content-type", "").split(";")[0] != "video/mp4":
        raise StudioError(415, "unsupported_media")
    declared = request.headers.get("content-length")
    if declared and (
        not declared.isascii()
        or not declared.isdigit()
        or len(declared) > 12
        or int(declared) > settings.max_upload_bytes
    ):
        raise StudioError(413, "upload_limit")
    slots = request.app.state.upload_slots
    if slots.locked():
        raise StudioError(429, "studio_busy")
    key = uuid4()
    storage = LocalMedia(settings.media_root)
    name = unquote(request.headers.get("x-file-name", "recording.mp4"))
    name = (
        "".join(c for c in name if c.isprintable())
        .replace("\\", "/")
        .split("/")[-1][:160]
    )
    async with slots, upload_deadline():
        with storage.stage(key) as temp:
            size = 0
            with open(temp, "xb", opener=lambda p, f: os.open(p, f, 0o600)) as output:
                async for chunk in request.stream():
                    size += len(chunk)
                    if size > settings.max_upload_bytes:
                        raise StudioError(413, "upload_limit")
                    await run_in_threadpool(output.write, chunk)
            metadata = await run_in_threadpool(validate_source, temp)
            video = Video(id=key, name=name or "recording.mp4", **metadata)
            return store_recording(db, storage, temp, video)


@router.get("/videos/{key}", response_model=VideoInfo)
def metadata(key: UUID, db: DB):
    return require(db, Video, key)


def media_response(db, key, *, clip=False, download=False):
    require(db, Clip if clip else Video, key)
    path = LocalMedia(get_settings().media_root).path(key, clip=clip)
    if not path.is_file():
        raise StudioError(404, "media_missing")
    return FileResponse(
        path,
        media_type="video/mp4",
        filename=f"clip-{key}.mp4" if download else None,
        headers={
            "Cache-Control": "private, no-store",
            "X-Content-Type-Options": "nosniff",
            "Cross-Origin-Resource-Policy": "same-origin",
        },
    )


@router.get("/videos/{key}/preview")
def preview(key: UUID, db: DB):
    return media_response(db, key)


def editing_data(db, video_id):
    return db.execute(
        select(Analysis.transcript, Analysis.focus_points).where(
            Analysis.video_id == video_id
        )
    ).first()


@router.get("/videos/{key}/edit-preview")
def edit_preview(key: UUID, start: FiniteFloat, end: FiniteFloat, db: DB):
    video = require(db, Video, key)
    validate_interval(start, end, video.duration, video.fps)
    data = editing_data(db, key)
    transcript = data.transcript if data else None
    return {
        "caption_ready": transcript is not None or not video.has_audio,
        "cues": prepare_cues(transcript, start, end),
        "focus": suggested_focus(data.focus_points if data else None, start, end),
    }


def enqueue(db, video, body, data):
    validate_interval(body.start, body.end, video.duration, video.fps)
    options = body.portrait.model_dump() if body.portrait else None

    def matches(job):
        saved = {k: v for k, v in job.edit.items() if k != "cues"} if job.edit else None
        return (job.video_id, job.start, job.end, saved) == (
            video.id,
            body.start,
            body.end,
            options,
        )

    job = db.get(Job, body.request_id)
    if job is None:
        edit = None
        if options:
            transcript = data.transcript if data else None
            if options["captions"] and video.has_audio and transcript is None:
                raise StudioError(409, "transcript_unavailable")
            edit = {
                **options,
                "cues": prepare_cues(transcript, body.start, body.end)
                if options["captions"]
                else [],
            }
        db.execute(
            insert(Job)
            .values(
                id=body.request_id,
                video_id=video.id,
                start=body.start,
                end=body.end,
                edit=edit,
            )
            .on_conflict_do_nothing(index_elements=[Job.id])
        )
        job = require(db, Job, body.request_id)
    if not matches(job):
        raise StudioError(409, "request_conflict")
    return job


@router.post("/videos/{key}/clips", response_model=JobInfo, status_code=202)
def create_clip(key: UUID, body: Selection, db: DB):
    job = enqueue(
        db,
        require(db, Video, key),
        body,
        editing_data(db, key) if body.portrait else None,
    )
    db.commit()
    return job


class Batch(BaseModel):
    model_config = ConfigDict(extra="forbid")
    selections: list[Selection] = Field(min_length=1, max_length=12)


@router.post("/videos/{key}/clips/batch", response_model=list[JobInfo], status_code=202)
def create_batch(key: UUID, body: Batch, db: DB):
    video = require(db, Video, key)
    data = editing_data(db, key)
    by_id = {}
    for item in sorted(body.selections, key=lambda item: item.request_id):
        if item.request_id in by_id:
            raise StudioError(422, "duplicate_request_id")
        by_id[item.request_id] = enqueue(db, video, item, data)
    db.commit()
    return [by_id[item.request_id] for item in body.selections]


@router.get("/videos/{key}/jobs", response_model=list[JobInfo])
def jobs(key: UUID, db: DB):
    require(db, Video, key)
    return db.scalars(
        select(Job)
        .where(Job.video_id == key)
        .order_by(Job.created_at.desc())
        .limit(100)
    ).all()


@router.get("/jobs/{key}", response_model=JobInfo)
def status(key: UUID, db: DB):
    return require(db, Job, key)


@router.post("/jobs/{key}/retry", response_model=JobInfo, status_code=202)
def retry(key: UUID, db: DB):
    job = db.get(Job, key, with_for_update=True)
    if job is None:
        raise StudioError(404, "not_found")
    if job.state == "failed":
        job.error = None
        job.state, job.progress, job.attempts = "queued", 0, 0
        db.commit()
    return job


@router.get("/clips/{key}/preview")
def clip_preview(key: UUID, db: DB):
    return media_response(db, key, clip=True)


@router.get("/clips/{key}/download")
def download(key: UUID, db: DB):
    return media_response(db, key, clip=True, download=True)
