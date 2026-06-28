"""Application configuration.

Order of precedence for settings:
    1. environment variables
    2. a `.env` file in the project root (loaded by pydantic-settings)
    3. the defaults below (which match the local Postgres from `docker-compose.yml`)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ---- database ----------------------------------------------------------
    # A full SQLAlchemy URL (e.g. `postgresql+psycopg2://user:pwd@host:port/db`)
    # overrides the individual components below. Useful in production.
    database_url: str | None = None

    db_host: str = "localhost"
    db_port: int = 5432
    db_user: str = "postgres"
    db_password: str = "postgres123"
    db_name: str = "music_ai"

    # ---- storage -----------------------------------------------------------
    # Root directory holding all on-disk artifacts. Per-task files live under
    # dedicated subdirectories so troubleshooting is just `ls <storage>/{uploads,outputs}/<task_id>`.
    #
    #     <storage_dir>/
    #         uploads/<task_id>/original.<ext>   # raw upload
    #         outputs/<task_id>/                  # worker artifacts
    #             vocals.wav, drums.wav, bass.wav, other.wav, *.mid
    #
    # The `storage_dir` itself defaults to `<repo>/storage`; you can point it
    # anywhere via the STORAGE_DIR env var. To override just the uploads or
    # outputs subdirectory (e.g. uploads on a faster SSD, outputs on a big
    # HDD), set UPLOAD_DIR / OUTPUT_DIR directly — they take precedence over
    # the derived `<storage_dir>/{uploads,outputs}`.
    storage_dir: Path = Field(
        default=Path(__file__).resolve().parent.parent.parent / "storage"
    )

    # Where raw uploads land. One subdirectory per task, file is `original.<ext>`.
    upload_dir: Path | None = None
    # Where the worker writes processed outputs. One subdirectory per task.
    output_dir: Path | None = None
    # Hard stop for a single uploaded audio file. The file writer enforces
    # this while streaming so large uploads are rejected before hitting disk.
    max_upload_bytes: int = 200 * 1024 * 1024

    # ---- auth ------------------------------------------------------------
    # JWT signing key. In production this MUST be set via JWT_SECRET (a long
    # random string). The default is only useful for local development and
    # smoke tests; the API will refuse to start if `production_mode=True`
    # and the secret still matches the default.
    jwt_secret: str = "dev-only-secret-please-change-in-production"
    jwt_algorithm: str = "HS256"
    access_token_ttl_minutes: int = 60 * 24  # 24 hours
    refresh_token_ttl_minutes: int = 60 * 24 * 30  # 30 days

    # Default quota applied to every user that has not overridden the value
    # on their row. 0 = unlimited.
    default_max_tasks_per_user: int = 50
    default_max_upload_bytes_per_user: int = 0  # 0 = use global max_upload_bytes

    # Production mode flips on stricter validation (no default JWT secret,
    # verbose CORS errors, etc.). Off by default so local dev keeps working.
    production_mode: bool = False

    # When true, all task endpoints require a valid Bearer token. When
    # false, endpoints still accept the `Authorization` header and stamp
    # the user_id on the task if present, but do not reject anonymous
    # traffic. The frontend always sends the header, so the only
    # environment that ships with `auth_required=False` is local dev /
    # CI smoke tests that don't want to deal with the login dance.
    auth_required: bool = False

    # Default administrator seeded on first boot. Both fields can be
    # overridden via env vars; setting `bootstrap_admin_email=""` skips
    # the seed entirely.
    bootstrap_admin_email: str = "[email protected]"
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin1234"
    bootstrap_admin_full_name: str = "Bootstrap Admin"

    # ---- celery / redis ----------------------------------------------------
    # The Celery broker (queue transport) and result backend share the same
    # Redis instance by default. Override `celery_broker_url` /
    # `celery_result_backend` directly if they need to diverge (e.g. a managed
    # broker for production but a local Redis for results).
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

    # ---- HTTP --------------------------------------------------------------
    # Comma-separated string or repeated env value accepted by pydantic.
    cors_origins: list[str] = Field(
        default_factory=lambda: [
            "http://localhost:5173",
            "http://127.0.0.1:5173",
        ]
    )

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @property
    def resolved_upload_dir(self) -> Path:
        return self.upload_dir or (self.storage_dir / "uploads")

    @property
    def resolved_output_dir(self) -> Path:
        return self.output_dir or (self.storage_dir / "outputs")

    # Absolute path to the `backend/` directory. The API uses this to spawn
    # the worker subprocess with the right CWD so `app.*` imports resolve.
    @property
    def backend_root(self) -> Path:
        return Path(__file__).resolve().parent.parent

    @property
    def resolved_celery_broker_url(self) -> str:
        return self.celery_broker_url or self.redis_url

    @property
    def resolved_celery_result_backend(self) -> str:
        return self.celery_result_backend or self.redis_url

    @property
    def sqlalchemy_url(self) -> str:
        if self.database_url:
            return self.database_url
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/{self.db_name}"
        )

    @property
    def admin_sqlalchemy_url(self) -> str:
        """URL pointing at the default `postgres` database — used to bootstrap."""
        if self.database_url:
            # If a full URL is provided, swap its dbname to `postgres`.
            url = self.database_url
            return url.rsplit("/", 1)[0] + "/postgres"
        return (
            f"postgresql+psycopg2://{self.db_user}:{self.db_password}"
            f"@{self.db_host}:{self.db_port}/postgres"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
