"""Application configuration.

Order of precedence for settings:
    1. environment variables
    2. a `.env` file in the project root (loaded by pydantic-settings)
    3. the defaults below (which match the local Postgres from `docker-compose.yml`)
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator, model_validator
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

    # Default administrator seeded on first boot.  In dev mode the
    # defaults below create a working admin account out of the box so
    # the SPA has something to log in with.
    #
    # In production (`PRODUCTION_MODE=true`):
    #   - If `bootstrap_admin_email` is still the dev placeholder
    #     ("[email protected]"), the seed is DISABLED (email is
    #     forced to "").  The operator must provision the first admin
    #     manually (e.g. via a DB migration or CLI command).
    #   - If the operator explicitly sets `BOOTSTRAP_ADMIN_EMAIL` but
    #     leaves the password as `admin1234`, startup is refused.
    bootstrap_admin_email: str = "[email protected]"
    bootstrap_admin_username: str = "admin"
    bootstrap_admin_password: str = "admin1234"
    bootstrap_admin_full_name: str = "Bootstrap Admin"

    # ---- LLM (commentary) -------------------------------------------------
    # When `llm_api_key` is empty, the worker falls back to `MockLlm` which
    # produces a deterministic commentary built from the analysis fields.
    # That means the UI is always populated, even in environments that
    # don't want to spend money on LLM calls.
    #
    # `llm_base_url` is the provider's root (no `/chat/completions`),
    # so any OpenAI-compatible endpoint works — OpenAI, Together,
    # DeepSeek, OpenRouter, or a local llama.cpp server.
    llm_api_key: str = ""
    llm_base_url: str = "https://api.openai.com/v1"
    llm_model: str = "gpt-4o-mini"
    llm_timeout_seconds: float = 30.0
    # If false, the worker skips the LLM step entirely (no commentary is
    # generated, the column stays null). Useful for unit tests and for
    # deployments that don't want the LLM cost.
    llm_enabled: bool = True

    # ---- ADTOS (drum transcription) ----------------------------------------
    # When false (default), the worker uses the rule-based DrumMidiService
    # baseline. When true, it tries to load the ADTOS drum-transcription
    # model and falls back to the baseline on any failure (missing
    # checkpoint, missing torch, inference exception, low-confidence
    # sub-classifier crash).
    #
    # The ADTOS package itself is research code (not on PyPI) — to enable,
    # install torch + torchaudio, clone https://github.com/AMAAI-Lab/ADTOS,
    # download a checkpoint, and set ADT_MODEL_PATH. The service is
    # deliberately opt-in so workers in lean environments (no torch)
    # keep working out of the box.
    adt_enabled: bool = False
    adt_model_path: Path | None = None
    # Optional extra sys.path entry for an ADTOS checkout. When unset, the
    # service uses the default `import adtos` lookup.
    adt_python_path: Path | None = None
    # Confidence threshold below which the cymbal sub-classifier refines
    # an ADTOS "cymbal" prediction into crash / ride / china / splash /
    # ride_bell. Above the threshold the label collapses to "crash"
    # (the most common cymbal in a GM kit) without spectral refinement.
    adt_cymbal_confidence_threshold: float = 0.6

    # ---- rate limiting -----------------------------------------------------
    # When false, the rate-limit middleware is a no-op. Useful for tests
    # and CI where many requests are fired in rapid succession.
    rate_limit_enabled: bool = True

    # ---- websocket ---------------------------------------------------------
    # Hard cap on concurrent WS progress connections per client IP. Prevents
    # a single client (or botnet) from opening thousands of sockets and
    # exhausting the event loop / DB pool.
    ws_max_connections_per_ip: int = 10
    # Maximum lifetime of a single WS progress connection, in seconds. A
    # progress stream that never reaches a terminal state (worker crashed
    # without publishing) is force-closed after this duration so the client
    # can reconnect and re-snapshot.
    ws_max_lifetime_seconds: int = 1800  # 30 minutes

    # ---- database pool -----------------------------------------------------
    # SQLAlchemy connection-pool sizing. `pool_size` is the steady-state
    # number of connections held open per engine; `max_overflow` is the
    # number of extra connections allowed under burst load before
    # requests block. `pool_recycle` discards a connection after this
    # many seconds so we never hand out a connection that the server has
    # already closed (Postgres' default `idle_session_timeout` is 0 but
    # `tcp_keepalive` is what catches dead peers — recycling is the
    # belt-and-suspenders complement to `pool_pre_ping`).
    db_pool_size: int = 10
    db_max_overflow: int = 20
    db_pool_recycle_seconds: int = 60 * 30  # 30 minutes

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

    # IPs / CIDRs of trusted reverse proxies (Nginx, Traefik, load
    # balancers).  ``X-Forwarded-For`` is ONLY honoured when the
    # direct TCP peer matches an entry here — otherwise the header is
    # ignored (it could be spoofed by the client).
    #
    # Comma-separated in the env, e.g.:
    #   TRUSTED_PROXIES="10.0.0.1,172.16.0.0/12"
    #
    # Defaults to localhost only — safe for direct-uvicorn dev setups.
    # In production behind a reverse proxy, set this to the proxy's IP
    # or the Docker network CIDR.
    trusted_proxies: list[str] = Field(
        default_factory=lambda: ["127.0.0.1", "::1"]
    )

    @field_validator("trusted_proxies", mode="before")
    @classmethod
    def _parse_trusted_proxies(cls, value):
        if isinstance(value, str):
            return [entry.strip() for entry in value.split(",") if entry.strip()]
        return value

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _parse_cors_origins(cls, value):
        if isinstance(value, str):
            return [origin.strip() for origin in value.split(",") if origin.strip()]
        return value

    @model_validator(mode="after")
    def _validate_production_security(self):
        if not self.production_mode:
            return self

        errors: list[str] = []

        # 1. Force authentication — no anonymous access in production.
        if not self.auth_required:
            # Auto-corrected; no error raised — the remaining checks
            # below are what the operator must fix manually.
            self.auth_required = True

        # 2. JWT secret must not be the dev default.
        if self.jwt_secret == "dev-only-secret-please-change-in-production":
            errors.append(
                "JWT_SECRET must be changed from the dev default "
                "('dev-only-secret-please-change-in-production') when PRODUCTION_MODE=true."
            )

        # 3. Database password must not be the dev default.
        if self.db_password == "postgres123":
            errors.append(
                "DB_PASSWORD must be changed from the dev default "
                "('postgres123') when PRODUCTION_MODE=true."
            )

        # 4. Bootstrap admin: disable auto-creation if the email is still
        # the dev placeholder.  This prevents an attacker from logging in
        # with [email protected] / admin1234 when the deployer forgets
        # to set env vars.  The operator must explicitly set both
        # BOOTSTRAP_ADMIN_EMAIL and BOOTSTRAP_ADMIN_PASSWORD to enable
        # the seed in production.
        if self.bootstrap_admin_email == "[email protected]":
            self.bootstrap_admin_email = ""

        if self.bootstrap_admin_password == "admin1234":
            errors.append(
                "BOOTSTRAP_ADMIN_PASSWORD must be changed from the dev default "
                "('admin1234') when PRODUCTION_MODE=true."
            )

        # 5. CORS origins must not be the dev defaults.
        dev_cors = {"http://localhost:5173", "http://127.0.0.1:5173"}
        if set(self.cors_origins) == dev_cors:
            errors.append(
                "CORS_ORIGINS must be changed from the dev defaults "
                "(http://localhost:5173, http://127.0.0.1:5173) when PRODUCTION_MODE=true."
            )

        # 6. Redis URL must not be the dev default.
        if self.redis_url == "redis://localhost:6379/0":
            errors.append(
                "REDIS_URL must be changed from the dev default "
                "('redis://localhost:6379/0') when PRODUCTION_MODE=true."
            )

        if errors:
            raise ValueError(
                "Production security checks failed:\n  - "
                + "\n  - ".join(errors)
            )

        return self

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
