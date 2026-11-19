from sqlalchemy.dialects.postgresql import insert

from app.core.settings import get_settings
from app.models.media import Analysis


def queue_automatic(db, key):
    db.execute(
        insert(Analysis)
        .values(video_id=key, weights=get_settings().highlight_weights.model_dump())
        .on_conflict_do_nothing(index_elements=[Analysis.video_id])
    )
    row = db.get(Analysis, key, with_for_update=True)
    if row.auto_state != "planned":
        row.auto_state = "pending"
        if row.state == "completed" and (row.samples is None or row.levels is None):
            row.state = "queued"
        if row.state in {"queued", "failed"}:
            row.state, row.stage, row.progress, row.attempts = "queued", "Queued", 0, 0
