"""
ML-ready dataset (stage 3): the user's own rules per column.

GET  /v1/datasets/{id}/ml            -> {version, rows, plan, suggestions{column: {...}}}
PUT  /v1/datasets/{id}/ml/plan       body = plan -> {plan}
POST /v1/datasets/{id}/ml/preview    body = plan (unsaved is fine) -> stats before/after, first rows, warnings
GET  /v1/datasets/{id}/ml/export     the saved plan applied to the current version: .csv, or .zip (train/test) when split is on
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Response

from app.api.deps import get_job_limiter, get_ml_service
from app.core.auth import AuthUser, get_current_user
from app.core.jobs import JobLimiter
from app.engine.mlprep import MLPlan
from app.services.mlprep import MLService

router = APIRouter(prefix="/v1/datasets/{dataset_id}/ml", tags=["ml-ready"])


@router.get("")
async def ml_view(
    dataset_id: UUID, user: AuthUser = Depends(get_current_user), svc: MLService = Depends(get_ml_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    return await jobs.run(svc.view, user.id, dataset_id)


@router.put("/plan")
async def ml_save(
    dataset_id: UUID, body: MLPlan, user: AuthUser = Depends(get_current_user), svc: MLService = Depends(get_ml_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    return await jobs.run(svc.save, user.id, dataset_id, body)


@router.post("/preview")
async def ml_preview(
    dataset_id: UUID, body: MLPlan, user: AuthUser = Depends(get_current_user), svc: MLService = Depends(get_ml_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    return await jobs.run(svc.preview, user.id, dataset_id, body)


@router.get("/export")
async def ml_export(
    dataset_id: UUID, user: AuthUser = Depends(get_current_user), svc: MLService = Depends(get_ml_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> Response:
    content, filename, media_type = await jobs.run(svc.export, user.id, dataset_id, heavy=True)
    return Response(content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{filename}"'})
