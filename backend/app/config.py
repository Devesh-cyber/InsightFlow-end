"""
Application settings, loaded from environment variables (and a local .env file).

Every setting has one place it is defined: here. Code never calls os.getenv()
directly, so a missing or misspelled variable fails loudly at startup instead
of halfway through a request.
"""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- General -----------------------------------------------------------
    app_env: Literal["development", "test", "production"] = "development"
    log_level: str = "INFO"

    # --- Supabase ----------------------------------------------------------
    # Project URL, e.g. https://abcdxyz.supabase.co  (Dashboard -> Project Settings -> API)
    supabase_url: str = Field(..., min_length=1)

    # Server-only secret key (service role / sb_secret_...). Used from Phase 1
    # for Storage. NEVER put this in the frontend or commit it.
    supabase_secret_key: SecretStr | None = None

    # Postgres connection string (Supabase pooler). Used from Phase 1.
    database_url: SecretStr | None = None

    # Only needed if your Supabase project still signs tokens with the legacy
    # HS256 shared secret. New asymmetric (ES256/RS256) keys need nothing here.
    supabase_jwt_secret: SecretStr | None = None

    jwt_audience: str = "authenticated"

    # --- HTTP --------------------------------------------------------------
    # Comma-separated list of frontend origins allowed to call the API.
    allowed_origins: str = "http://localhost:5173"

    # --- Limits (tuned for Render free tier: 512 MB RAM) --------------------
    # Measured (Phase 1): a 25 MB CSV peaks ~210 MB RAM, an 11 MB XLSX ~235 MB.
    # Excel is zipped XML and expands far more in memory, hence its lower limit.
    max_upload_mb: int = Field(25, ge=1, le=500)
    max_xlsx_upload_mb: int = Field(10, ge=1, le=500)
    max_datasets_per_user: int = Field(10, ge=1)
    max_versions_per_dataset: int = Field(20, ge=2)
    # One heavy job at a time fits 512 MB; raise on a bigger instance.
    max_concurrent_jobs: int = Field(1, ge=1)
    job_queue_timeout_seconds: float = Field(30, gt=0)
    uploads_per_hour: int = Field(30, ge=1)
    # In-memory cache of computed results (profiles, charts, relationships).
    cache_max_mb: int = Field(64, ge=0)
    # On-disk cache of downloaded Parquet files (immutable, so never stale).
    file_cache_dir: str = "/tmp/insightflow-cache"
    file_cache_max_mb: int = Field(512, ge=0)

    # XLSX export is CPU-heavy (~15 s per 2.4M cells on a laptop; slower on a
    # small cloud CPU). Above this, users are asked to export CSV instead.
    max_xlsx_export_cells: int = Field(1_000_000, ge=1000)

    # --- Storage / database ------------------------------------------------
    storage_bucket: str = "datasets"
    db_pool_max_size: int = Field(4, ge=1, le=20)

    @field_validator("supabase_url")
    @classmethod
    def _strip_trailing_slash(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        if not value.startswith(("https://", "http://")):
            raise ValueError("SUPABASE_URL must start with https://")
        return value

    @model_validator(mode="after")
    def _production_requirements(self) -> "Settings":
        if self.app_env == "production":
            if not self.supabase_url.startswith("https://"):
                raise ValueError("SUPABASE_URL must use https in production")
            if "*" in self.cors_origins:
                raise ValueError("ALLOWED_ORIGINS must not contain '*' in production")
            if not (self.database_url and self.supabase_secret_key):
                raise ValueError("DATABASE_URL and SUPABASE_SECRET_KEY are required in production")
        return self

    # --- Derived values ----------------------------------------------------
    @property
    def cors_origins(self) -> list[str]:
        return [o.strip().rstrip("/") for o in self.allowed_origins.split(",") if o.strip()]

    @property
    def jwks_url(self) -> str:
        return f"{self.supabase_url}/auth/v1/.well-known/jwks.json"

    @property
    def jwt_issuer(self) -> str:
        return f"{self.supabase_url}/auth/v1"

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024

    def upload_limit_mb(self, fmt: str) -> int:
        return min(self.max_xlsx_upload_mb, self.max_upload_mb) if fmt == "xlsx" else self.max_upload_mb

    @property
    def docs_enabled(self) -> bool:
        return self.app_env != "production"


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
