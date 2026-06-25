"""Application configuration.

Order of precedence for settings:
    1. environment variables
    2. a `.env` file in the project root (loaded by pydantic-settings)
    3. the defaults below (which match the local Postgres from `docker-compose.yml`)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
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

    # ---- celery / redis ----------------------------------------------------
    # The Celery broker (queue transport) and result backend share the same
    # Redis instance by default. Override `celery_broker_url` /
    # `celery_result_backend` directly if they need to diverge (e.g. a managed
    # broker for production but a local Redis for results).
    redis_url: str = "redis://localhost:6379/0"
    celery_broker_url: str | None = None
    celery_result_backend: str | None = None

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
