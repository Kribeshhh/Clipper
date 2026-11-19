from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Video(Base):
    __tablename__ = "videos"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(160))
    duration: Mapped[float]
    width: Mapped[int]
    height: Mapped[int]
    fps: Mapped[float]
    codec: Mapped[str]
    has_audio: Mapped[bool]
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Job(Base):
    __tablename__ = "processing_jobs"
    __table_args__ = (
        Index("ix_processing_jobs_video_created", "video_id", "created_at"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("videos.id"))
    start: Mapped[float]
    end: Mapped[float]
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    progress: Mapped[int] = mapped_column(default=0)
    edit: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    error: Mapped[str | None]
    attempts: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())

    @property
    def layout(self) -> str:
        return "vertical" if self.edit else "source"


class Clip(Base):
    __tablename__ = "clips"
    id: Mapped[UUID] = mapped_column(ForeignKey("processing_jobs.id"), primary_key=True)
    details: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    duration: Mapped[float]
    size: Mapped[int] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class Analysis(Base):
    __tablename__ = "video_analysis"
    __table_args__ = (Index("ix_video_analysis_state_created", "state", "created_at"),)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("videos.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(16), default="queued")
    stage: Mapped[str] = mapped_column(default="Queued")
    progress: Mapped[int] = mapped_column(default=0)
    attempts: Mapped[int] = mapped_column(default=0)
    auto_state: Mapped[str | None] = mapped_column(String(16), index=True)
    auto_jobs: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    checkpoint: Mapped[dict | None] = mapped_column(JSON(none_as_null=True))
    weights: Mapped[dict] = mapped_column(JSON)
    levels: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    focus_points: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    samples: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    transcript: Mapped[list | None] = mapped_column(JSON(none_as_null=True))
    candidates: Mapped[list] = mapped_column(JSON, default=list)
    warning: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())


class RecordingLink(Base):
    __tablename__ = "recording_links"
    video_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("videos.id", ondelete="SET NULL")
    )
    kind: Mapped[str] = mapped_column(String(16), primary_key=True)
    external_id: Mapped[str] = mapped_column(String(160), primary_key=True)
    start: Mapped[int]
    end: Mapped[int]
    title: Mapped[str] = mapped_column(String(100))
    updated_at: Mapped[datetime] = mapped_column(server_default=func.now())


class VideoImport(Base):
    __tablename__ = "video_imports"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    generation_key: Mapped[str | None] = mapped_column(String(64), unique=True)
    sealed_url: Mapped[str | None]
    received: Mapped[int] = mapped_column(BigInteger, default=0)
    total: Mapped[int | None] = mapped_column(BigInteger)
    error: Mapped[str | None]
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
