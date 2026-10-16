"""
Dataset endpoints.

Upload protocol (simpler and safer than multipart forms):

    POST /v1/datasets?filename=sales.xlsx[&sheet=Q1]
    Content-Type: application/octet-stream
    <raw file bytes>

The body is streamed straight to a temporary file while we count bytes, so an
oversized upload is rejected as soon as it crosses the limit; it is never
held in memory. From the browser this is just `fetch(url, {method: "POST", body: file})`.
"""

import shutil
import tempfile
from pathlib import Path
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response, status

from app.api.deps import get_dataset_service, get_job_limiter, get_upload_limiter
from app.api.schemas import DatasetDetail, DatasetList, DatasetSummary, RenameRequest, SelectSheetRequest, SheetsResponse
from app.core.auth import AuthUser, get_current_user
from app.core.errors import BadRequestError, PayloadTooLargeError
from app.core.jobs import JobLimiter
from app.core.ratelimit import RateLimiter
from app.engine.readers import detect_format
from app.services.datasets import DatasetService

router = APIRouter(prefix="/v1/datasets", tags=["datasets"])


def _too_large(limit_mb: int) -> PayloadTooLargeError:
    return PayloadTooLargeError(
        f"The file is larger than the {limit_mb} MB limit.", code="file_too_large", details={"limit_mb": limit_mb}
    )


async def _stream_to_file(request: Request, destination: Path, max_bytes: int, limit_mb: int) -> int:
    declared = request.headers.get("content-length")
    if declared is not None:
        if not declared.isdigit():
            raise BadRequestError("Invalid Content-Length header.")
        if int(declared) > max_bytes:
            raise _too_large(limit_mb)

    size = 0
    with open(destination, "wb") as handle:
        async for chunk in request.stream():
            size += len(chunk)
            if size > max_bytes:
                raise _too_large(limit_mb)
            handle.write(chunk)
    if size == 0:
        raise BadRequestError("The request body is empty. Send the file bytes as the body.", code="empty_upload")
    return size


@router.post("", status_code=status.HTTP_201_CREATED, response_model=DatasetDetail)
async def upload_dataset(
    request: Request,
    filename: str = Query(..., min_length=1, max_length=255, description="Original file name, e.g. sales.csv"),
    sheet: str | None = Query(None, max_length=255, description="Excel only: sheet to load (default: first)"),
    user: AuthUser = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service),
    jobs: JobLimiter = Depends(get_job_limiter),
    upload_limiter: RateLimiter = Depends(get_upload_limiter),
) -> DatasetDetail:
    settings = request.app.state.settings
    fmt = detect_format(filename)  # reject unsupported types before reading any bytes
    limit_mb = settings.upload_limit_mb(fmt)
    upload_limiter.check(str(user.id))
    await jobs.run(service.check_quota, user.id)

    workdir = Path(tempfile.mkdtemp(prefix="upload-"))
    try:
        upload_path = workdir / ("upload" + Path(filename).suffix.lower())
        size = await _stream_to_file(request, upload_path, limit_mb * 1024 * 1024, limit_mb)
        dataset, version = await jobs.run(service.ingest_upload, user.id, upload_path, filename, size, sheet, workdir, heavy=True)
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return DatasetDetail.build(dataset, version)


@router.get("", response_model=DatasetList)
def list_datasets(
    request: Request,
    user: AuthUser = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service),
) -> DatasetList:
    records = service.repo.list_datasets(user.id)
    return DatasetList(
        datasets=[DatasetSummary.from_record(r) for r in records],
        limit=request.app.state.settings.max_datasets_per_user,
    )


@router.get("/{dataset_id}", response_model=DatasetDetail)
def get_dataset(
    dataset_id: UUID,
    user: AuthUser = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service),
) -> DatasetDetail:
    return DatasetDetail.build(*service.get(user.id, dataset_id))


@router.patch("/{dataset_id}", response_model=DatasetDetail)
def rename_dataset(
    dataset_id: UUID,
    body: RenameRequest,
    user: AuthUser = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service),
) -> DatasetDetail:
    return DatasetDetail.build(*service.rename(user.id, dataset_id, body.name))


@router.delete("/{dataset_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_dataset(
    dataset_id: UUID,
    user: AuthUser = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service),
) -> Response:
    service.delete(user.id, dataset_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{dataset_id}/sheets", response_model=SheetsResponse)
def list_sheets(
    dataset_id: UUID,
    user: AuthUser = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service),
) -> SheetsResponse:
    dataset, _ = service.get(user.id, dataset_id)
    return SheetsResponse(sheet_names=dataset.sheet_names or [], current=dataset.sheet_name)


@router.post("/{dataset_id}/sheet", response_model=DatasetDetail)
async def select_sheet(
    dataset_id: UUID,
    body: SelectSheetRequest,
    user: AuthUser = Depends(get_current_user),
    service: DatasetService = Depends(get_dataset_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> DatasetDetail:
    workdir = Path(tempfile.mkdtemp(prefix="sheet-"))
    try:
        dataset, version = await jobs.run(
            service.select_sheet, user.id, dataset_id, body.sheet_name, body.expected_version, workdir, heavy=True
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    return DatasetDetail.build(dataset, version)
