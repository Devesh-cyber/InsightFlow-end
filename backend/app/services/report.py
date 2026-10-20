"""
Gather everything the report needs, reusing the same cached results the
Explore pages use (so a report after browsing the app costs almost nothing),
then hand it to the builder and a renderer.

Memory: at most one version's frame is loaded at a time, and only on a cache
miss. Column details are computed from that one frame instead of re-reading
the Parquet file once per column.
"""

from dataclasses import asdict, replace
from uuid import UUID

from app.engine import profile, quality, relationships
from app.engine.recipe import pandas_recipe
from app.report.builder import build_report
from app.services.analysis import AnalysisService, Resolved
from app.services.cleaning import version_view

MAX_PROFILED_COLUMNS = 25


class ReportService:
    def __init__(self, analysis: AnalysisService, keep_versions: int | None = None, checks=None) -> None:
        self.analysis = analysis
        self.keep_versions = keep_versions
        self.checks = checks  # ChecksService: the report counts the user's failing checks too

    def _frame_loader(self, resolved: Resolved):
        holder: dict = {}

        def load():
            if "df" not in holder:
                holder["df"] = self.analysis.frame(resolved)
            return holder["df"]

        return load

    def build(self, user_id: UUID, dataset_id: UUID, version: int | None = None,
              title: str | None = None, author: str | None = None, tz: str | None = None) -> dict:
        svc = self.analysis
        r = svc.resolve(user_id, dataset_id, version)
        frame = self._frame_loader(r)

        # Same cache kinds and parameters as the Explore endpoints.
        overview = svc.cached(r, "overview", None, lambda: profile.overview(frame()))
        health = svc.cached(r, "health", None, lambda: quality.health_report(frame()))
        if self.checks is not None:
            health = self.checks.health_with_checks(user_id, r, health)
        columns = svc.cached(r, "columns", None, lambda: {"columns": profile.columns_profile(frame())})["columns"]
        correlations = svc.cached(r, "correlations", {"method": "pearson"},
                                  lambda: relationships.correlation_matrix(frame(), "pearson"))
        details = [
            svc.cached(r, "column_detail", {"name": c["name"]}, lambda name=c["name"]: profile.column_detail(frame(), name))
            for c in columns[:MAX_PROFILED_COLUMNS]
        ]

        all_versions = svc.repo.list_versions(user_id, dataset_id)
        history = sorted((v for v in all_versions if v.version <= r.version.version), key=lambda v: v.version)
        steps = [version_view(v, r.version.version) for v in history]

        before = None
        if history and history[0].version != r.version.version:
            first = Resolved(r.dataset, history[0])
            first_frame = self._frame_loader(first)
            before = {
                "version": first.version.version,
                "overview": svc.cached(first, "overview", None, lambda: profile.overview(first_frame())),
                "health": svc.cached(first, "health", None, lambda: quality.health_report(first_frame())),
            }
            if self.checks is not None:  # judge "before" by the same saved checks, so the comparison is fair
                before["health"] = self.checks.health_with_checks(user_id, first, before["health"])

        dataset = asdict(r.dataset)
        return build_report(
            dataset=dataset,
            version=r.version.version,
            overview={k: v for k, v in overview.items() if k != "preview"},
            health=health,
            columns=columns,
            details=details,
            correlations=correlations,
            steps=steps,
            recipe=pandas_recipe(replace(r.dataset, current_version=r.version.version), history),
            before=before,
            keep_versions=self.keep_versions,
            title=title,
            author=author,
            tz=tz,
        )
