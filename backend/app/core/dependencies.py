from redis import Redis
from redis.backoff import NoBackoff
from redis.exceptions import RedisError
from redis.retry import Retry
from sqlalchemy import create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from app.core.settings import Settings


class Dependencies:
    def __init__(self, settings: Settings):
        self.db = create_engine(
            settings.database_url,
            pool_pre_ping=True,
            pool_size=3,
            max_overflow=2,
            pool_timeout=2,
            connect_args={"connect_timeout": 2, "options": "-c statement_timeout=2000"},
        )
        self.redis = Redis.from_url(
            settings.broker_url,
            socket_timeout=2,
            socket_connect_timeout=2,
            retry=Retry(NoBackoff(), 0),
        )

    def check(self) -> dict[str, bool]:
        try:
            with self.db.connect() as conn:
                database = conn.scalar(text("SELECT 1")) == 1
        except SQLAlchemyError:
            database = False
        try:
            broker = bool(self.redis.ping())
        except RedisError:
            broker = False
        return {"postgresql": database, "redis": broker}

    def close(self) -> None:
        self.redis.close()
        self.db.dispose()
