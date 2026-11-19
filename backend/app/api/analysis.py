from uuid import UUID

from fastapi import APIRouter, Depends
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import load_only

from app.api.media import DB, require
from app.api.session import operator
from app.core.settings import get_settings
from app.models.media import Analysis, Clip, Job, Video
from app.services.analysis.context import WINDOW_SECONDS
from app.services.analysis.requests import queue_automatic

router = APIRouter(prefix="/api/videos", dependencies=[Depends(operator)])
PUBLIC = (
    Analysis.state,
    Analysis.stage,
    Analysis.progress,
    Analysis.warning,
    Analysis.weights,
    Analysis.candidates,
)


def result(job):
    if job is None:
        return {
            "state": "idle",
            "stage": "",
            "progress": 0,
            "candidates": [],
            "warning": None,
        }
    return {
        **{col.key: getattr(job, col.key) for col in PUBLIC},
        "candidates": job.candidates[:12],
        "outdated": job.weights != get_settings().highlight_weights.model_dump(),
    }


@router.get("/{key}/analysis")
def status(key: UUID, db: DB):
    require(db, Video, key)
    return result(
        db.scalar(
            select(Analysis).where(Analysis.video_id == key).options(load_only(*PUBLIC))
        )
    )


@router.post("/{key}/analysis", status_code=202)
def request_analysis(key: UUID, db: DB):
    video = require(db, Video, key)
    weights = get_settings().highlight_weights.model_dump()
    db.execute(
        insert(Analysis)
        .values(video_id=key, weights=weights)
        .on_conflict_do_nothing(index_elements=[Analysis.video_id])
    )
    job = db.get(Analysis, key, with_for_update=True)
    if job.state == "failed" or (
        job.state == "completed" and (job.warning or job.weights != weights)
    ):
        job.state, job.stage, job.progress = "queued", "Queued", 0
        job.attempts, job.weights, job.candidates = 0, weights, []
        if job.warning:
            job.transcript = None
            if video.duration > WINDOW_SECONDS:
                job.checkpoint = job.samples = job.levels = None
        if job.auto_state == "planned" and not job.auto_jobs:
            job.auto_state = "pending"
        job.warning = None
    db.commit()
    return result(job)


@router.post("/{key}/auto-clips", status_code=202)
def generate(key: UUID, db: DB):
    require(db, Video, key)
    queue_automatic(db, key)
    db.commit()
    return {"accepted": True}


@router.get("/{key}/auto-clips")
def generated(key: UUID, db: DB):
    require(db, Video, key)
    row = db.get(
        Analysis,
        key,
        options=[
            load_only(
                Analysis.auto_state,
                Analysis.auto_jobs,
                Analysis.state,
                Analysis.stage,
                Analysis.progress,
                Analysis.warning,
            )
        ],
    )
    if not row or not row.auto_state:
        return {"state": "idle", "clips": []}
    state = row.auto_state if row.state == "completed" else row.state
    clips = []
    keys = [UUID(x) for x in row.auto_jobs or []]
    jobs = (
        {
            j.id: (j, c)
            for j, c in db.execute(
                select(Job, Clip)
                .outerjoin(Clip, Clip.id == Job.id)
                .where(Job.video_id == key, Job.id.in_(keys))
            )
        }
        if keys
        else {}
    )
    for k in keys:
        j, c = jobs[k]
        evidence = j.edit["evidence"]
        clips.append(
            {
                "id": k,
                "state": j.state,
                "progress": j.progress,
                "error": j.error,
                "start": j.start,
                "end": j.end,
                "duration": c.duration if c else j.edit["duration"],
                "description": evidence.get("description", "Audio and visual activity"),
                "score": evidence["score"],
                "quality": evidence["quality"],
                "signals": list(evidence["signals"]),
            }
        )
    return {
        "state": state,
        "stage": row.stage,
        "progress": row.progress,
        "warning": row.warning,
        "clips": clips,
    }
