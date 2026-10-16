"""System endpoints: liveness check and "who am I"."""

from uuid import UUID

from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app import __version__
from app.api.schemas import Usage
from app.core.auth import AuthUser, get_current_user

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str


class MeResponse(BaseModel):
    user_id: UUID
    email: str | None
    usage: Usage | None


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    """Liveness probe for Render. No auth, no database, always cheap."""
    return HealthResponse(status="ok", version=__version__)


@router.head("/health", include_in_schema=False)
def health_head() -> None:
    return None


@router.get("/v1/me", response_model=MeResponse, tags=["system"])
def me(request: Request, user: AuthUser = Depends(get_current_user)) -> MeResponse:
    """The authenticated user and their usage against the limits."""
    settings = request.app.state.settings
    service = getattr(request.app.state, "dataset_service", None)
    usage = None
    if service is not None:
        current = service.repo.usage(user.id)
        usage = Usage(
            datasets=current.datasets,
            max_datasets=settings.max_datasets_per_user,
            storage_bytes=current.storage_bytes,
            max_upload_mb=settings.max_upload_mb,
            max_xlsx_upload_mb=settings.upload_limit_mb("xlsx"),
        )
    return MeResponse(user_id=user.id, email=user.email, usage=usage)
