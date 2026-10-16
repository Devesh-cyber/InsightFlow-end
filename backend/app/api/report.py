"""
GET /v1/datasets/{id}/report?format=html|docx|json

- html: print-ready page in the Sage theme; the user saves it as PDF from the browser
- docx: editable Word document with the same content
- json: the report data itself, for showing a preview inside the frontend

Optional `title` and `author` fill the cover page (for coursework / presentations);
`tz` (e.g. Asia/Kolkata) shows the generated time in the user's zone.
"""

import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse, Response

from app.api.deps import get_job_limiter, get_report_service
from app.core.auth import AuthUser, get_current_user
from app.core.jobs import JobLimiter
from app.report.docx import render_docx
from app.report.html import CONTENT_SECURITY_POLICY, render_html
from app.services.export import safe_filename
from app.services.report import ReportService

router = APIRouter(prefix="/v1/datasets/{dataset_id}", tags=["report"])

DOCX_MEDIA = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


@router.get("/report")
async def report(
    dataset_id: UUID,
    format: str = Query("html", pattern="^(html|docx|json)$"),
    version: int | None = Query(None, ge=1, description="Version to report on (default: current)"),
    title: str | None = Query(None, max_length=120, description="Cover title (default: 'Data report: <name>')"),
    author: str | None = Query(None, max_length=80, description="'Prepared by' line on the cover"),
    tz: str | None = Query(None, max_length=64, pattern=r"^[A-Za-z_]+(/[A-Za-z0-9_+\-]+){0,2}$",
                           description="IANA time zone for the 'Generated' time, e.g. Asia/Kolkata (default UTC)"),
    user: AuthUser = Depends(get_current_user),
    svc: ReportService = Depends(get_report_service),
    jobs: JobLimiter = Depends(get_job_limiter),
) -> Response:
    def work():
        data = svc.build(user.id, dataset_id, version, title, author, tz)
        if format == "json":
            return data, None
        if format == "docx":
            return data, render_docx(data)
        return data, render_html(data).encode("utf-8")

    # Cache misses inside build() take a heavy slot themselves; rendering is light.
    data, body = await jobs.run(work)
    if format == "json":
        return JSONResponse(data)

    stamp = datetime.date.today().isoformat()
    filename = f"{safe_filename(data['meta']['dataset_name'])}_report_v{data['meta']['version']}_{stamp}.{format}"
    headers = {"Content-Disposition": f'attachment; filename="{filename}"'}
    if format == "html":
        headers["Content-Security-Policy"] = CONTENT_SECURITY_POLICY
        return Response(body, media_type="text/html; charset=utf-8", headers=headers)
    return Response(body, media_type=DOCX_MEDIA, headers=headers)
