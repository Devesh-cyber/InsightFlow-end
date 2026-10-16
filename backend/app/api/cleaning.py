"""
Cleaning endpoints: catalogue, recommendations, preview, apply, versions, revert,
plus export and the pandas recipe.

The frontend flow is: recommendations (or the catalogue) -> preview -> apply.
Apply and revert require `expected_version` = the version the user was looking
at; if the dataset changed meanwhile (another tab), they get 409 instead of
silently overwriting.
"""

import shutil
import tempfile
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel, Field
from starlette.background import BackgroundTask

from app.api.deps import get_cleaning_service, get_export_service, get_job_limiter
from app.api.schemas import DatasetDetail
from app.core.auth import AuthUser, get_current_user
from app.core.jobs import JobLimiter
from app.engine.operations import OPERATIONS
from app.services.cleaning import CleaningService
from app.services.export import ExportService

router = APIRouter(tags=["cleaning"])


class OperationRequest(BaseModel):
    type: str = Field(min_length=1, max_length=64)
    params: dict[str, Any] = Field(default_factory=dict)


class ApplyRequest(OperationRequest):
    expected_version: int = Field(ge=1, description="The version you previewed; 409 if it changed")


class RevertRequest(BaseModel):
    to_version: int = Field(ge=1)
    expected_version: int = Field(ge=1)


class ApplyResponse(BaseModel):
    dataset: DatasetDetail
    applied: dict[str, Any]


@router.get("/v1/operations")
def list_operations() -> dict[str, Any]:
    """The catalogue of cleaning operations with their JSON parameter schemas (for building forms)."""
    return {
        "operations": [
            {"type": name, "params_schema": spec.params.model_json_schema()} for name, spec in sorted(OPERATIONS.items())
        ]
    }


@router.get("/v1/datasets/{dataset_id}/recommendations")
async def recommendations(
    dataset_id: UUID, user: AuthUser = Depends(get_current_user),
    svc: CleaningService = Depends(get_cleaning_service), jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    return await jobs.run(svc.recommendations, user.id, dataset_id)


@router.post("/v1/datasets/{dataset_id}/preview")
async def preview(
    dataset_id: UUID, body: OperationRequest, user: AuthUser = Depends(get_current_user),
    svc: CleaningService = Depends(get_cleaning_service), jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    return await jobs.run(svc.preview, user.id, dataset_id, body.type, body.params, heavy=True)


@router.post("/v1/datasets/{dataset_id}/apply", response_model=ApplyResponse)
async def apply(
    dataset_id: UUID, body: ApplyRequest, user: AuthUser = Depends(get_current_user),
    svc: CleaningService = Depends(get_cleaning_service), jobs: JobLimiter = Depends(get_job_limiter),
) -> ApplyResponse:
    workdir = Path(tempfile.mkdtemp(prefix="apply-"))
    try:
        record, version, applied = await jobs.run(
            svc.apply, user.id, dataset_id, body.type, body.params, body.expected_version, workdir, heavy=True
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return ApplyResponse(dataset=DatasetDetail.build(record, version), applied=applied)


@router.get("/v1/datasets/{dataset_id}/versions")
async def versions(
    dataset_id: UUID, user: AuthUser = Depends(get_current_user),
    svc: CleaningService = Depends(get_cleaning_service), jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    return await jobs.run(svc.versions, user.id, dataset_id)


@router.post("/v1/datasets/{dataset_id}/revert", response_model=DatasetDetail)
async def revert(
    dataset_id: UUID, body: RevertRequest, user: AuthUser = Depends(get_current_user),
    svc: CleaningService = Depends(get_cleaning_service), jobs: JobLimiter = Depends(get_job_limiter),
) -> DatasetDetail:
    record, version = await jobs.run(svc.revert, user.id, dataset_id, body.to_version, body.expected_version)
    return DatasetDetail.build(record, version)


@router.get("/v1/datasets/{dataset_id}/export")
async def export(
    dataset_id: UUID,
    format: str = Query("csv", pattern="^(csv|xlsx)$"),
    version: int | None = Query(None, ge=1),
    user: AuthUser = Depends(get_current_user),
    svc: ExportService = Depends(get_export_service), jobs: JobLimiter = Depends(get_job_limiter),
) -> FileResponse:
    workdir = Path(tempfile.mkdtemp(prefix="export-"))
    try:
        path, filename, media_type = await jobs.run(svc.export, user.id, dataset_id, format, version, workdir, heavy=True)
    except Exception:
        shutil.rmtree(workdir, ignore_errors=True)
        raise
    # The temp folder is removed after the file has been sent.
    return FileResponse(
        path, media_type=media_type, filename=filename,
        background=BackgroundTask(shutil.rmtree, workdir, ignore_errors=True),
    )


@router.get("/v1/datasets/{dataset_id}/recipe", response_class=PlainTextResponse)
async def recipe(
    dataset_id: UUID, user: AuthUser = Depends(get_current_user),
    svc: ExportService = Depends(get_export_service), jobs: JobLimiter = Depends(get_job_limiter),
) -> PlainTextResponse:
    code, filename = await jobs.run(svc.recipe, user.id, dataset_id)
    return PlainTextResponse(
        code, media_type="text/x-python; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )
