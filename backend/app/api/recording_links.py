import re
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert

from app.api.media import DB, require
from app.api.session import operator
from app.core.errors import StudioError
from app.models.media import RecordingLink, Video
from app.services.analysis.requests import queue_automatic

router = APIRouter(prefix="/api/recording-links", dependencies=[Depends(operator)])


class Link(BaseModel):
    kind: Literal["youtube", "twitch_vod", "twitch_clip"]
    external_id: str = Field(min_length=1, max_length=160)
    title: str = Field(min_length=1, max_length=100)
    start: int = Field(ge=0, le=604800)
    end: int = Field(gt=0, le=604800)

    @model_validator(mode="after")
    def validate_selection(self):
        patterns = {
            "youtube": r"[A-Za-z0-9_-]{11}",
            "twitch_vod": r"[0-9]{1,20}",
            "twitch_clip": r"[A-Za-z0-9_-]{1,160}",
        }
        if (
            not re.fullmatch(patterns[self.kind], self.external_id)
            or self.end <= self.start
        ):
            raise ValueError("Invalid recording or selection")
        return self


@router.get("", response_model=list[Link])
def recent_links(db: DB):
    return db.scalars(
        select(RecordingLink).order_by(RecordingLink.updated_at.desc()).limit(100)
    ).all()


@router.post("", response_model=Link)
def save_link(body: Link, db: DB):
    statement = insert(RecordingLink).values(**body.model_dump())
    db.execute(
        statement.on_conflict_do_update(
            index_elements=[RecordingLink.kind, RecordingLink.external_id],
            set_={
                "start": body.start,
                "end": body.end,
                "title": body.title,
                "updated_at": func.now(),
            },
        )
    )
    db.commit()
    return body


class GenerateFromLink(Link):
    video_id: UUID | None = None


@router.post("/generate", status_code=202)
def generate(body: GenerateFromLink, db: DB):
    if body.video_id:
        require(db, Video, body.video_id)
        statement = insert(RecordingLink).values(**body.model_dump())
        db.execute(statement.on_conflict_do_nothing())
    link = db.get(RecordingLink, (body.kind, body.external_id), with_for_update=True)
    if link is None or (link.video_id is None and body.video_id is None):
        raise StudioError(409, "recording_required")
    if body.video_id and link.video_id and link.video_id != body.video_id:
        raise StudioError(409, "recording_mismatch")
    video_id = link.video_id or body.video_id
    require(db, Video, video_id)
    link.video_id = video_id
    link.updated_at = func.now()
    queue_automatic(db, video_id)
    db.commit()
    return {"video_id": video_id}
