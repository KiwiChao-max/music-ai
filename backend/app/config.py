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

    # ---- uploads -----------------------------------------------------------
    upload_dir: Path = Field(default=Path(__file__).parent / "uploads")

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
