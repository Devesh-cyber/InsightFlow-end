"""
ML-ready dataset service: store the user's rules, suggest, preview, export.

Locked until the data is clean: saving, previewing and exporting need primary cleaning and
explore & review to have no open items (409 clean_first otherwise). Viewing is always allowed.

Preview and export always run on the dataset's CURRENT version (the cleaned data),
so stage 3 never changes the dataset itself: it produces a separate ML-ready file.
"""

import io
import json
import zipfile
from uuid import UUID

import polars as pl
from pydantic import ValidationError

from app.core.cache import params_key
from app.core.errors import ConflictError, NotFoundError
from app.engine.infer import dtype_label
from app.engine.jsonsafe import to_json
from app.engine.mlprep import MLPlan, run_plan, suggest
from app.engine.profile import is_numeric
from app.engine.quality import health_report
from app.engine.validity import find_relationships
from app.services.analysis import AnalysisService
from app.services.export import safe_filename

PREVIEW_ROWS = 15


def _stats(s: pl.Series) -> dict:
    out = {"dtype": dtype_label(s.dtype), "missing": s.null_count()}
    if is_numeric(s.dtype) and s.len() - s.null_count() > 0:
        x = s.cast(pl.Float64)
        out.update({"mean": x.mean(), "std": x.std(ddof=0), "min": x.min(), "max": x.max(),
                    "skew": x.drop_nulls().skew() if x.drop_nulls().len() > 2 else None})
    else:
        out["unique"] = s.drop_nulls().n_unique()
    return to_json(out)


class MLService:
    def __init__(self, analysis: AnalysisService, checks=None) -> None:
        self.analysis = analysis
        self.checks = checks  # ChecksService: the user's saved checks and "looks fine" marks count toward readiness

    def readiness(self, user_id: UUID, resolved) -> dict:
        """ML-ready unlocks once primary cleaning AND explore & review have no open items on the current version.
        Items marked "looks fine, keep it" count as done. A new cleaning step that opens issues locks it again."""
        health = self.analysis.cached(resolved, "health", None, lambda: health_report(self.analysis.frame(resolved)))
        if self.checks is not None:
            health = self.checks.health_with_checks(user_id, resolved, health)
        stages = health.get("stages") or {}
        primary = (stages.get("primary") or {}).get("open", 0)
        secondary = (stages.get("secondary") or {}).get("open", 0)
        return {"locked": bool(primary or secondary), "primary_open": primary, "secondary_open": secondary,
                "version": resolved.version.version}

    def _require_ready(self, user_id: UUID, resolved) -> None:
        ready = self.readiness(user_id, resolved)
        if ready["locked"]:
            parts = [f"{n} {label}" for n, label in ((ready["primary_open"], "primary cleaning issue(s)"),
                                                    (ready["secondary_open"], "explore & review item(s)")) if n]
            raise ConflictError(
                f"Finish cleaning first: {' and '.join(parts)} still open. Fix them or mark them 'looks fine' in Clean.",
                code="clean_first", details=ready,
            )

    def _stored(self, user_id: UUID, dataset_id: UUID) -> dict:
        raw = self.analysis.repo.get_ml_plan(user_id, dataset_id)
        if raw is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        return raw

    def _plan(self, user_id: UUID, dataset_id: UUID) -> MLPlan:
        raw = self._stored(user_id, dataset_id)
        try:
            return MLPlan.model_validate(raw)
        except ValidationError:
            return MLPlan()  # an old, no longer valid plan starts fresh rather than breaking the page

    def view(self, user_id: UUID, dataset_id: UUID) -> dict:
        resolved = self.analysis.resolve(user_id, dataset_id)
        plan = self._plan(user_id, dataset_id)
        suggestions = self.analysis.cached(
            resolved, "ml_suggest", None,
            lambda: {"columns": suggest(df := self.analysis.frame(resolved), find_relationships(df))},
        )["columns"]
        return {"version": resolved.version.version, "plan": plan.model_dump(mode="json"), "suggestions": suggestions,
                "rows": resolved.version.row_count, "readiness": self.readiness(user_id, resolved)}

    def save(self, user_id: UUID, dataset_id: UUID, plan: MLPlan) -> dict:
        self._require_ready(user_id, self.analysis.resolve(user_id, dataset_id))
        stored = self.analysis.repo.set_ml_plan(user_id, dataset_id, plan.model_dump(mode="json"))
        if stored is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        return {"plan": stored}

    def preview(self, user_id: UUID, dataset_id: UUID, plan: MLPlan) -> dict:
        resolved = self.analysis.resolve(user_id, dataset_id)
        self._require_ready(user_id, resolved)
        payload = plan.model_dump(mode="json")

        def work():
            df = self.analysis.frame(resolved)
            result = run_plan(df, plan)
            out: pl.DataFrame = result["df"]
            train = result["is_train"]
            columns = []
            for name in out.columns:
                source = result["sources"].get(name, name)
                columns.append({
                    "name": name, "source": source,
                    "after": _stats(out.get_column(name)),
                    "before": _stats(df.get_column(source)) if source in df.columns else None,
                })
            return {
                "shape_before": [df.height, df.width],
                "shape_after": [out.height, out.width],
                "split": {"how": result["split"], "train": int(train.sum()), "test": int((~train).sum())} if plan.split.enabled else None,
                "target": result["target"],
                "columns": columns,
                "preview": {"columns": out.columns, "rows": [to_json(list(r)) for r in out.head(PREVIEW_ROWS).iter_rows()]},
                "warnings": result["warnings"],
                "learned": to_json(result["learned"]),
            }

        return {"version": resolved.version.version,
                **self.analysis.cached(resolved, "ml_preview", {"plan": params_key({"p": payload})}, work)}

    def export(self, user_id: UUID, dataset_id: UUID) -> tuple[bytes, str, str]:
        """The ML-ready file built from the SAVED plan: a CSV, or a ZIP with train/test when the split is on."""
        resolved = self.analysis.resolve(user_id, dataset_id)
        self._require_ready(user_id, resolved)
        plan = self._plan(user_id, dataset_id)
        df = self.analysis.frame(resolved)
        result = run_plan(df, plan)
        out: pl.DataFrame = result["df"]
        base = f"{safe_filename(resolved.dataset.name)}_v{resolved.version.version}_ml_ready"
        if not plan.split.enabled:
            buf = io.BytesIO()
            out.write_csv(buf)
            return buf.getvalue(), f"{base}.csv", "text/csv"

        train = result["is_train"]
        meta = {
            "dataset": resolved.dataset.name, "version": resolved.version.version, "target": result["target"],
            "split": {"how": result["split"], "test_size": plan.split.test_size, "seed": plan.split.seed,
                      "train_rows": int(train.sum()), "test_rows": int((~train).sum())},
            "plan": plan.model_dump(mode="json"), "learned_from_train_rows": to_json(result["learned"]),
            "warnings": result["warnings"],
        }
        readme = (
            f"ML-ready data from InsightFlow: '{resolved.dataset.name}', version {resolved.version.version}\n\n"
            f"train.csv  {int(train.sum())} rows\n"
            f"test.csv   {int((~train).sum())} rows   ({meta['split']['how']} split, seed {plan.split.seed})\n"
            f"plan.json  your rules, plus every value learned (means, scales, categories, bin edges)\n\n"
            "Everything that learns from the data (filling gaps, clipping, scaling, encoding, bins) was learned from the\n"
            "TRAIN rows only and then applied to both files, so the test rows didn't leak into training.\n"
            + (f"\nTarget column: {result['target']} (last column).\n" if result["target"] else "")
        )
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name, part in (("train.csv", out.filter(train)), ("test.csv", out.filter(~train))):
                data = io.BytesIO()
                part.write_csv(data)
                z.writestr(name, data.getvalue())
            z.writestr("plan.json", json.dumps(meta, indent=2, ensure_ascii=False))
            z.writestr("README.txt", readme)
        return buf.getvalue(), f"{base}.zip", "application/zip"
