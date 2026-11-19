from uuid import uuid5

from sqlalchemy.dialects.postgresql import insert

from app.core.settings import get_settings
from app.models.media import Job
from app.services.analysis.scoring import rank
from app.services.editing.planner import plans


def schedule(db, analysis, video):
    cfg = get_settings()
    # Re-rank even older cached observations using the current editorial scoring.
    candidates = analysis.candidates
    if len(candidates) <= 12 or any("quality" not in c for c in candidates):
        candidates = rank(
            video.duration,
            analysis.levels or [],
            analysis.samples or [],
            analysis.transcript or [],
            analysis.weights,
            limit=60,
        )
    ranked = plans(
        video,
        analysis,
        candidates,
        cfg.auto_target_min_seconds,
        cfg.auto_target_max_seconds,
    )
    ids = []
    for plan in ranked:
        key = uuid5(video.id, f"auto:v1:{plan['event']}")
        edit = plan.pop("edit")
        edit["evidence"] = plan
        db.execute(
            insert(Job)
            .values(
                id=key,
                video_id=video.id,
                start=plan["start"],
                end=plan["end"],
                edit=edit,
            )
            .on_conflict_do_nothing(index_elements=[Job.id])
        )
        ids.append(str(key))
    analysis.auto_jobs = ids
    analysis.auto_state = "planned"
    db.commit()
