"""
Saved checks.

GET /v1/datasets/{id}/checks?version=   -> {version, checks: [{id, name, conditions, combine, result}]}
PUT /v1/datasets/{id}/checks            -> body {checks: [...]} replaces the whole list (max 25)

result = {status: passing|failing|error, violations, rows[{row, values}], columns, rule, error}
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_checks_service, get_job_limiter
from app.core.auth import AuthUser, get_current_user
from app.core.jobs import JobLimiter
from app.services.checks import ChecksPayload, ChecksService

router = APIRouter(prefix="/v1/datasets/{dataset_id}", tags=["checks"])


@router.get("/checks")
async def list_checks(
    dataset_id: UUID, version: int | None = Query(None, ge=1),
    user: AuthUser = Depends(get_current_user), svc: ChecksService = Depends(get_checks_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    return await jobs.run(svc.view, user.id, dataset_id, version)


@router.put("/checks")
async def save_checks(
    dataset_id: UUID, body: ChecksPayload,
    user: AuthUser = Depends(get_current_user), svc: ChecksService = Depends(get_checks_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    return await jobs.run(svc.save, user.id, dataset_id, body)
