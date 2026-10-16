"""
Explore endpoints (read-only): overview, rows, health, columns, relationships, charts.

All accept an optional `version` (default: current). Heavy work runs through the
job limiter in a worker thread; results are cached per (dataset, version).
Responses are plain JSON documents described in docs/API.md.
"""

from typing import Any
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.deps import get_analysis_service, get_job_limiter
from app.core.auth import AuthUser, get_current_user
from app.core.errors import UnprocessableError
from app.core.jobs import JobLimiter
from app.engine import charts, profile, quality, relationships
from app.services.analysis import AnalysisService

router = APIRouter(prefix="/v1/datasets/{dataset_id}", tags=["explore"])

VersionQuery = Query(None, ge=1, description="Dataset version (default: current)")
MAX_PAGE = 500


def _require_column(df_columns: list[str], name: str) -> None:
    if name not in df_columns:
        raise UnprocessableError(f"Column '{name}' does not exist.", code="column_not_found")


async def _run(jobs: JobLimiter, func, *args):
    return await jobs.run(func, *args)


@router.get("/overview")
async def overview(
    dataset_id: UUID, version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    def work():
        r = svc.resolve(user.id, dataset_id, version)
        return {"version": r.version.version, **svc.cached(r, "overview", None, lambda: profile.overview(svc.frame(r)))}
    return await _run(jobs, work)


@router.get("/rows")
async def rows(
    dataset_id: UUID,
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=MAX_PAGE),
    version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    """A page of rows. Reads only the requested slice from the Parquet file."""
    def work():
        r = svc.resolve(user.id, dataset_id, version)
        page = svc.lazy(r).slice(offset, limit).collect()
        return {"version": r.version.version, "total_rows": r.version.row_count, "offset": offset, "limit": limit,
                **profile.rows_payload(page)}
    return await _run(jobs, work)


@router.get("/health")
async def health(
    dataset_id: UUID, version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    def work():
        r = svc.resolve(user.id, dataset_id, version)
        return {"version": r.version.version, **svc.cached(r, "health", None, lambda: quality.health_report(svc.frame(r)))}
    return await _run(jobs, work)


@router.get("/columns")
async def columns(
    dataset_id: UUID, version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    def work():
        r = svc.resolve(user.id, dataset_id, version)
        result = svc.cached(r, "columns", None, lambda: {"columns": profile.columns_profile(svc.frame(r))})
        return {"version": r.version.version, **result}
    return await _run(jobs, work)


@router.get("/columns/detail")
async def column_detail(
    dataset_id: UUID,
    name: str = Query(..., min_length=1, max_length=255, description="Column name (as a query parameter, so any characters work)"),
    version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    def work():
        r = svc.resolve(user.id, dataset_id, version)

        def compute():
            df = svc.frame(r)
            _require_column(df.columns, name)
            return profile.column_detail(df, name)

        return {"version": r.version.version, **svc.cached(r, "column_detail", {"name": name}, compute)}
    return await _run(jobs, work)


@router.get("/relationships")
async def relationship(
    dataset_id: UUID,
    a: str = Query(..., min_length=1, max_length=255),
    b: str = Query(..., min_length=1, max_length=255),
    version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    def work():
        r = svc.resolve(user.id, dataset_id, version)
        result = svc.cached(r, "relationship", {"a": a, "b": b}, lambda: relationships.analyze(svc.frame(r), a, b))
        return {"version": r.version.version, **result}
    return await _run(jobs, work)


@router.get("/correlations")
async def correlations(
    dataset_id: UUID,
    method: str = Query("pearson", pattern="^(pearson|spearman)$"),
    version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    def work():
        r = svc.resolve(user.id, dataset_id, version)
        result = svc.cached(r, "correlations", {"method": method}, lambda: relationships.correlation_matrix(svc.frame(r), method))
        return {"version": r.version.version, **result}
    return await _run(jobs, work)


@router.get("/charts/options")
async def chart_options(
    dataset_id: UUID,
    x: str = Query(..., min_length=1, max_length=255),
    y: str | None = Query(None, max_length=255),
    version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    def work():
        r = svc.resolve(user.id, dataset_id, version)
        result = svc.cached(r, "chart_options", {"x": x, "y": y}, lambda: charts.options(svc.frame(r), x, y))
        return {"version": r.version.version, **result}
    return await _run(jobs, work)


@router.get("/charts/data")
async def chart_data(
    dataset_id: UUID,
    type: str = Query(..., pattern="^(histogram|box|bar|line|scatter|heatmap)$"),
    x: str = Query(..., min_length=1, max_length=255),
    y: str | None = Query(None, max_length=255),
    agg: str = Query("count", pattern="^(count|sum|mean|median|min|max)$"),
    bins: int | None = Query(None, ge=5, le=50),
    version: int | None = VersionQuery,
    user: AuthUser = Depends(get_current_user), svc: AnalysisService = Depends(get_analysis_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> dict[str, Any]:
    def work():
        r = svc.resolve(user.id, dataset_id, version)
        params = {"type": type, "x": x, "y": y, "agg": agg, "bins": bins}
        result = svc.cached(r, "chart", params, lambda: charts.data(svc.frame(r), type, x, y, agg, bins))
        return {"version": r.version.version, **result}
    return await _run(jobs, work)
