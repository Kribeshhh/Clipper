from celery import Celery
from celery.signals import setup_logging

from app.core.logging import configure_logging
from app.core.settings import get_settings

settings = get_settings()
app = Celery("clipper", broker=settings.broker_url)
app.conf.update(
    accept_content=["json"],
    task_serializer="json",
    task_ignore_result=True,
    worker_prefetch_multiplier=1,
    broker_connection_retry_on_startup=True,
)


@setup_logging.connect
def worker_logging(**kwargs):
    configure_logging(settings.log_level)


app.conf.update(
    imports=(
        "app.workers.rendering",
        "app.workers.analysis",
        "app.workers.live",
        "app.workers.imports",
    ),
    beat_schedule={
        "render-pending": {
            "task": "studio.render_next",
            "schedule": 3.0,
            "options": {"expires": 3},
        }
    },
)
app.conf.beat_schedule["clean-abandoned-media"] = {
    "task": "studio.clean_abandoned",
    "schedule": 3600.0,
    "options": {"expires": 3600},
}
app.conf.beat_schedule["analyze-pending"] = {
    "task": "studio.analyze_next",
    "schedule": 5.0,
    "options": {"queue": "analysis", "expires": 5},
}

app.conf.beat_schedule["live-buffer"] = {
    "task": "studio.live_tick",
    "schedule": 3.0,
    "options": {"expires": 3},
}

app.conf.beat_schedule["import-pending"] = {
    "task": "studio.import_next",
    "schedule": 3.0,
    "options": {"queue": "imports", "expires": 3},
}
