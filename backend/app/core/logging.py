import json
import logging
from logging.config import dictConfig


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(
            {
                "level": record.levelname,
                "logger": record.name,
                "message": record.getMessage(),
                **(
                    {"request_id": record.request_id}
                    if hasattr(record, "request_id")
                    else {}
                ),
                **(
                    {"exception_type": record.exc_info[0].__name__}
                    if record.exc_info and record.exc_info[0]
                    else {}
                ),
            }
        )


def configure_logging(level: str) -> None:
    dictConfig(
        {
            "version": 1,
            "disable_existing_loggers": False,
            "formatters": {"json": {"()": JsonFormatter}},
            "handlers": {
                "console": {"class": "logging.StreamHandler", "formatter": "json"}
            },
            "root": {"level": level, "handlers": ["console"]},
        }
    )
