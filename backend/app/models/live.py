from uuid import UUID, uuid4

from sqlalchemy import JSON, BigInteger, ForeignKey
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base


class Connection(Base):
    __tablename__ = "provider_connections"
    provider: Mapped[str] = mapped_column(primary_key=True)
    account: Mapped[str]
    name: Mapped[str]
    sealed: Mapped[str]
    checked: Mapped[int] = mapped_column(BigInteger)


class Feed(Base):
    __tablename__ = "live_feeds"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    provider: Mapped[str]
    account: Mapped[str]
    broadcast: Mapped[str]
    title: Mapped[str]
    secret_hash: Mapped[str]
    state: Mapped[str] = mapped_column(default="waiting")
    last_seen: Mapped[float]
    latest: Mapped[int] = mapped_column(default=-1)
    end_time: Mapped[float] = mapped_column(default=0.0)
    last_highlight_end: Mapped[float] = mapped_column(default=-1.0)
    error: Mapped[str | None]


class Segment(Base):
    __tablename__ = "live_segments"
    feed_id: Mapped[UUID] = mapped_column(ForeignKey("live_feeds.id"), primary_key=True)
    sequence: Mapped[int] = mapped_column(primary_key=True)
    video_id: Mapped[UUID] = mapped_column(ForeignKey("videos.id"), unique=True)
    start: Mapped[float]
    digest: Mapped[str]
    processed: Mapped[bool] = mapped_column(default=False)


class Moment(Base):
    __tablename__ = "live_moments"
    job_id: Mapped[UUID] = mapped_column(
        ForeignKey("processing_jobs.id"), primary_key=True
    )
    feed_id: Mapped[UUID] = mapped_column(ForeignKey("live_feeds.id"), index=True)
    start: Mapped[float]
    end: Mapped[float]
    evidence: Mapped[dict] = mapped_column(JSON)


class Association(Base):
    __tablename__ = "broadcast_recordings"
    video_id: Mapped[UUID] = mapped_column(ForeignKey("videos.id"), primary_key=True)
    provider: Mapped[str]
    account: Mapped[str]
    broadcast: Mapped[str]
