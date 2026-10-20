"""
Application factory.

Run locally:   uvicorn app.main:app --reload
Run in Docker: see Dockerfile (same command, without --reload)
"""

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app import __version__
from app.api import checks, cleaning, datasets, explore, mlprep, report, system
from app.config import Settings, get_settings
from pathlib import Path

from app.core.auth import TokenVerifier
from app.core.cache import FileCache, ResultCache
from app.core.errors import register_error_handlers
from app.core.jobs import JobLimiter
from app.core.logging import configure_logging
from app.core.middleware import RequestContextMiddleware
from app.core.ratelimit import RateLimiter
from app.services.analysis import AnalysisService
from app.services.checks import ChecksService
from app.services.mlprep import MLService
from app.services.cleaning import CleaningService
from app.services.datasets import DatasetLimits, DatasetService
from app.services.export import ExportService
from app.services.report import ReportService

logger = logging.getLogger("app")


def _build_dataset_service(settings: Settings):
    """Real Postgres + Supabase Storage, if configured. Returns (service, closers)."""
    if not (settings.database_url and settings.supabase_secret_key):
        logger.warning("DATABASE_URL / SUPABASE_SECRET_KEY not set: dataset endpoints will return 503")
        return None, []

    from app.db.repository import PostgresRepository, create_pool
    from app.storage.client import SupabaseStorage

    pool = create_pool(settings.database_url.get_secret_value(), settings.db_pool_max_size)
    storage = SupabaseStorage(
        settings.supabase_url, settings.supabase_secret_key.get_secret_value(), settings.storage_bucket
    )
    service = DatasetService(PostgresRepository(pool), storage, _limits(settings))
    return service, [pool, storage]


def _limits(settings: Settings) -> DatasetLimits:
    return DatasetLimits(
        max_datasets=settings.max_datasets_per_user,
        keep_versions=settings.max_versions_per_dataset,
    )


def create_app(
    settings: Settings | None = None,
    token_verifier: TokenVerifier | None = None,
    dataset_service: DatasetService | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    closers: list = []
    if dataset_service is None:
        dataset_service, closers = _build_dataset_service(settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        logger.info("starting InsightFlow API %s (env=%s)", __version__, settings.app_env)
        for resource in closers:
            if hasattr(resource, "open"):
                resource.open(wait=False)  # don't block startup if the DB is slow to wake
        yield
        for resource in closers:
            resource.close()
        logger.info("shutting down")

    app = FastAPI(
        title="InsightFlow API",
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs" if settings.docs_enabled else None,
        redoc_url=None,
        openapi_url="/openapi.json" if settings.docs_enabled else None,
    )

    app.state.settings = settings
    app.state.token_verifier = token_verifier or TokenVerifier.from_settings(settings)
    app.state.dataset_service = dataset_service
    app.state.job_limiter = JobLimiter(settings.max_concurrent_jobs, settings.job_queue_timeout_seconds)
    app.state.analysis_service = app.state.cleaning_service = app.state.export_service = None
    app.state.report_service = app.state.checks_service = app.state.ml_service = None
    if dataset_service is not None:
        analysis = AnalysisService(
            dataset_service.repo,
            dataset_service.storage,
            FileCache(Path(settings.file_cache_dir), settings.file_cache_max_mb * 1024 * 1024),
            ResultCache(settings.cache_max_mb * 1024 * 1024),
            heavy=app.state.job_limiter.heavy,
        )
        app.state.analysis_service = analysis
        app.state.checks_service = ChecksService(analysis)
        app.state.ml_service = MLService(analysis, app.state.checks_service)
        app.state.cleaning_service = CleaningService(analysis, dataset_service, app.state.checks_service)
        app.state.export_service = ExportService(analysis, settings.max_xlsx_export_cells)
        app.state.report_service = ReportService(analysis, settings.max_versions_per_dataset, app.state.checks_service)
    app.state.upload_limiter = RateLimiter(settings.uploads_per_hour, 3600)

    # Order matters: the LAST added middleware is the OUTERMOST.
    #   CORS (outermost) -> request context/crash handler -> routes
    # CORS must be outermost so that even a 500 from the crash handler carries
    # CORS headers; otherwise the browser reports a misleading "CORS error".
    app.add_middleware(RequestContextMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,  # we use bearer tokens, not cookies
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["Content-Disposition", "X-Request-ID"],
        max_age=600,
    )

    register_error_handlers(app)
    app.include_router(system.router)
    app.include_router(datasets.router)
    app.include_router(explore.router)
    app.include_router(cleaning.router)
    app.include_router(report.router)
    app.include_router(checks.router)
    app.include_router(mlprep.router)
    return app


# `uvicorn app.main:app` imports this. Tests build their own app via create_app().
app = create_app()
