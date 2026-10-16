"""Shared FastAPI dependencies: the service objects created at startup."""

from fastapi import Request

from app.core.errors import ServiceUnavailableError
from app.core.jobs import JobLimiter
from app.core.ratelimit import RateLimiter
from app.services.datasets import DatasetService


def get_dataset_service(request: Request) -> DatasetService:
    service = getattr(request.app.state, "dataset_service", None)
    if service is None:
        raise ServiceUnavailableError(
            "Datasets are not configured on this server (set DATABASE_URL and SUPABASE_SECRET_KEY).",
            code="not_configured",
        )
    return service


def _service(request: Request, name: str):
    service = getattr(request.app.state, name, None)
    if service is None:
        raise ServiceUnavailableError(
            "Datasets are not configured on this server (set DATABASE_URL and SUPABASE_SECRET_KEY).",
            code="not_configured",
        )
    return service


def get_analysis_service(request: Request):
    return _service(request, "analysis_service")


def get_cleaning_service(request: Request):
    return _service(request, "cleaning_service")


def get_export_service(request: Request):
    return _service(request, "export_service")


def get_report_service(request: Request):
    return _service(request, "report_service")


def get_job_limiter(request: Request) -> JobLimiter:
    return request.app.state.job_limiter


def get_upload_limiter(request: Request) -> RateLimiter:
    return request.app.state.upload_limiter
