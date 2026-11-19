from functools import lru_cache
from pathlib import Path
from typing import Literal
from urllib.parse import quote, urlsplit

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    SecretStr,
    field_validator,
    model_validator,
)
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy import URL


class SignalWeights(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    audio: float = Field(default=1.2, ge=0, le=10)
    scene: float = Field(default=0.8, ge=0, le=10)
    motion: float = Field(default=1.2, ge=0, le=10)
    speech: float = Field(default=0.8, ge=0, le=10)
    reaction: float = Field(default=1.5, ge=0, le=10)


class Settings(BaseSettings):
    auto_target_min_seconds: int = Field(default=20, ge=5, le=60)
    auto_target_max_seconds: int = Field(default=60, ge=5, le=180)

    @model_validator(mode="after")
    def check_duration_targets(self):
        if self.auto_target_min_seconds > self.auto_target_max_seconds:
            raise ValueError("Minimum automatic target must fit within maximum target")
        return self

    highlight_weights: SignalWeights = Field(default_factory=SignalWeights)
    whisper_model_dir: Path = Path("/opt/whisper-tiny")

    model_config = SettingsConfigDict(env_file="../.env", extra="ignore")

    app_env: Literal["development", "production"] = "development"
    log_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"
    cors_origins: list[str] = []
    postgres_host: str = Field(min_length=1, default="db")
    postgres_port: int = Field(default=5432, ge=1, le=65535)
    postgres_db: str = Field(default="clipper", min_length=1)
    postgres_user: str = Field(min_length=1, default="clipper")
    postgres_password: SecretStr = Field(min_length=16)
    redis_host: str = Field(default="redis", pattern=r"^[a-zA-Z0-9.-]+$")
    redis_port: int = Field(default=6379, ge=1, le=65535)
    redis_password: SecretStr = Field(min_length=16)

    operator_key: SecretStr | None = Field(default=None, min_length=32)
    recording_quota_bytes: int = Field(default=20 * 1024**3, ge=65536)
    export_quota_bytes: int = Field(default=5 * 1024**3, ge=65536)
    studio_origin: str = "http://127.0.0.1:5173"
    google_client_id: str = ""
    google_client_secret: SecretStr | None = None
    twitch_client_id: str = ""
    twitch_client_secret: SecretStr | None = None
    oauth_encryption_key: SecretStr | None = None
    live_buffer_seconds: int = Field(default=120, ge=90, le=600)
    live_buffer_bytes: int = Field(
        default=256 * 1024**2, ge=8 * 1024**2, le=2 * 1024**3
    )
    live_idle_seconds: int = Field(default=180, ge=60, le=1800)

    @field_validator("studio_origin")
    @classmethod
    def trusted_origin(cls, value):
        url = urlsplit(value)
        if (
            not url.hostname
            or url.username
            or url.password
            or url.path
            or url.query
            or url.fragment
            or (
                url.scheme != "https"
                and not (
                    url.scheme == "http"
                    and url.hostname in {"127.0.0.1", "localhost", "::1"}
                )
            )
        ):
            raise ValueError("Use an HTTPS studio origin or loopback HTTP")
        return value

    media_root: Path = Path("/var/lib/clipper")
    import_max_bytes: int = Field(default=8 * 1024**3, ge=65536, le=64 * 1024**3)
    import_max_duration_seconds: int = Field(default=14400, ge=1, le=86400)
    import_download_seconds: int = Field(default=3600, ge=10, le=7200)
    import_verify_seconds: int = Field(default=1800, ge=5, le=7200)
    import_bytes_per_second: int = Field(
        default=8 * 1024**2, ge=65536, le=128 * 1024**2
    )
    import_reserve_bytes: int = Field(default=512 * 1024**2, ge=16 * 1024**2)
    max_upload_bytes: int = Field(default=512 * 1024**2, ge=1, le=4 * 1024**3)
    max_duration_seconds: int = Field(default=3600, ge=1, le=14400)
    media_timeout_seconds: int = Field(default=300, ge=5, le=1800)

    @field_validator("media_root")
    @classmethod
    def absolute_storage(cls, root: Path) -> Path:
        if not root.is_absolute():
            raise ValueError("MEDIA_ROOT must be absolute")
        return root

    @field_validator("cors_origins")
    @classmethod
    def explicit_origins(cls, origins: list[str]) -> list[str]:
        for origin in origins:
            parts = urlsplit(origin)
            if (
                parts.scheme not in ("http", "https")
                or not parts.hostname
                or parts.username is not None
                or any(c in origin for c in "*?#")
                or parts.path
            ):
                raise ValueError("CORS entries must be explicit HTTP(S) origins")
            _ = parts.port
        return origins

    @property
    def database_url(self) -> URL:
        return URL.create(
            "postgresql+psycopg",
            self.postgres_user,
            self.postgres_password.get_secret_value(),
            self.postgres_host,
            self.postgres_port,
            self.postgres_db,
        )

    @property
    def broker_url(self) -> str:
        password = quote(self.redis_password.get_secret_value(), safe="")
        return f"redis://:{password}@{self.redis_host}:{self.redis_port}/0"


@lru_cache
def get_settings() -> Settings:
    return Settings()
