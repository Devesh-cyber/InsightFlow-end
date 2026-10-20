"""
Saved checks: rules the user wants to hold on EVERY version ("age is between 0 and 120",
"order_id is never repeated", "total = units × price").

A check is written as the condition that marks a BAD row (the same condition builder
as custom rules). Results are evaluated per version, cached in memory, and merged
into Health, Recommendations and the report as `check_failed` issues, so a dataset
can't look "clean" while one of the user's own rules is broken.
"""

import re
from uuid import UUID

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.cache import params_key
from app.core.errors import NotFoundError, UnprocessableError
from app.engine.jsonsafe import to_json
from app.engine.matching import with_matches
from app.engine.rules import Condition, describe_conditions, evaluate
from app.services.analysis import AnalysisService, Resolved

MAX_CHECKS = 25
SAMPLE_ROWS = 5
CHECK_PENALTY = 5.0
CHECK_PENALTY_MAX = 30.0


class CheckDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[A-Za-z0-9_-]{1,40}$")
    name: str = Field(min_length=1, max_length=80)
    conditions: list[Condition] = Field(min_length=1, max_length=10)
    combine: str = Field(default="and", pattern="^(and|or)$")

    @field_validator("name")
    @classmethod
    def _strip(cls, v: str) -> str:
        v = re.sub(r"\s+", " ", v).strip()
        if not v:
            raise ValueError("name can't be empty")
        return v


class ChecksPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    checks: list[CheckDefinition] = Field(max_length=MAX_CHECKS)

    @field_validator("checks")
    @classmethod
    def _unique_ids(cls, checks):
        ids = [c.id for c in checks]
        if len(ids) != len(set(ids)):
            raise ValueError("check ids must be unique")
        return checks


def parse_checks(raw: list[dict]) -> list[CheckDefinition]:
    """Stored checks are trusted to have been validated on save; skip any that no longer parse."""
    parsed = []
    for item in raw or []:
        try:
            parsed.append(CheckDefinition.model_validate(item))
        except ValidationError:
            continue
    return parsed


def evaluate_checks(df: pl.DataFrame, checks: list[CheckDefinition]) -> list[dict]:
    results = []
    for check in checks:
        result = {"id": check.id, "name": check.name, "rule": describe_conditions(check.conditions, check.combine)}
        try:
            mask = evaluate(df, check.conditions, check.combine)
        except UnprocessableError as exc:  # e.g. a column was renamed or dropped since the check was saved
            results.append({**result, "status": "error", "violations": None, "error": exc.message, "rows": []})
            continue
        violations = int(mask.sum())
        columns = list(dict.fromkeys(
            [c.column for c in check.conditions]
            + [t.column for c in check.conditions for t in (c.formula or []) if not isinstance(t, str) and t.column]
        ))
        sample = df.with_row_index("__row").filter(mask).head(SAMPLE_ROWS)
        rows = [{"row": int(r["__row"]) + 1, "values": to_json([r[c] for c in columns])} for r in sample.iter_rows(named=True)]
        results.append({**result, "status": "failing" if violations else "passing", "violations": violations,
                        "columns": columns, "rows": rows, "error": None})
    return results


def check_issues(checks: list[CheckDefinition], results: list[dict]) -> list[dict]:
    by_id = {c.id: c for c in checks}
    issues = []
    for r in results:
        if r["status"] == "passing":
            continue
        check = by_id[r["id"]]
        if r["status"] == "error":
            issues.append({
                "id": f"check:{r['id']}", "category": "check_error", "severity": "info", "column": None,
                "title": f"Check '{r['name']}' can't run on this version",
                "message": f"{r['error']} Edit or delete the check in Clean → Saved checks.",
                "evidence": {"check_id": r["id"]}, "options": [],
            })
            continue
        conditions = [c.model_dump(mode="json", exclude_none=True) for c in check.conditions]
        options = [{"label": "Remove those rows", "operation": {"type": "apply_rule", "params": {
            "conditions": conditions, "combine": check.combine, "action": "remove_rows"}},
            "recommended": False, "note": f"Removes {r['violations']} row(s)."}]
        if len(check.conditions) == 1:
            options.insert(0, {"label": f"Mark '{check.conditions[0].column}' as missing in those rows", "operation": {
                "type": "apply_rule", "params": {"conditions": conditions, "combine": check.combine, "action": "set_missing",
                                                 "target": check.conditions[0].column}},
                "recommended": False, "note": "Keeps the rows."})
        shown = ", ".join(str(x["row"]) for x in r["rows"])
        issues.append({
            "id": f"check:{r['id']}", "category": "check_failed", "severity": "critical", "column": r["columns"][0],
            "title": f"Your check '{r['name']}' fails on {r['violations']} row(s)",
            "message": f"Rule: rows where {r['rule']} are problems. Found in row(s) {shown}{' …' if r['violations'] > len(r['rows']) else ''}.",
            "evidence": {"check_id": r["id"], "violations": r["violations"], "rows": r["rows"]},
            "options": options,
        })
    return with_matches(issues)


def merge_into_health(health: dict, extra: list[dict]) -> dict:
    """Add check issues to a (cached) health report and re-score it. Never mutates the input."""
    if not extra:
        return health
    issues = health["issues"] + extra
    failing = sum(1 for i in extra if i["category"] == "check_failed")
    deductions = [d for d in health["deductions"] if d["reason"] != "Your checks failing"]
    if failing:
        deductions.append({"reason": "Your checks failing", "points": round(min(CHECK_PENALTY_MAX, CHECK_PENALTY * failing), 1)})
    score = round(max(0.0, 100.0 - sum(d["points"] for d in deductions)), 1)
    grade = "excellent" if score >= 95 else "good" if score >= 80 else "fair" if score >= 60 else "poor"
    summary = dict(health["summary"])
    by_category = dict(summary.get("by_category", {}))
    for i in extra:
        summary[i["severity"]] = summary.get(i["severity"], 0) + 1
        by_category[i["category"]] = by_category.get(i["category"], 0) + 1
    summary["by_category"] = by_category
    order = {"critical": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order[i["severity"]], i["category"], i["column"] or ""))
    return {**health, "issues": issues, "deductions": deductions, "score": score, "grade": grade, "summary": summary}


class ChecksService:
    def __init__(self, analysis: AnalysisService) -> None:
        self.analysis = analysis

    def _stored(self, user_id: UUID, dataset_id: UUID) -> list[dict]:
        raw = self.analysis.repo.get_checks(user_id, dataset_id)
        if raw is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        return raw

    def results(self, resolved: Resolved, checks: list[CheckDefinition]) -> list[dict]:
        if not checks:
            return []
        payload = [c.model_dump(mode="json") for c in checks]
        return self.analysis.cached(
            resolved, "checks", {"checks": params_key({"c": payload})},
            lambda: {"results": evaluate_checks(self.analysis.frame(resolved), checks)},
        )["results"]

    def view(self, user_id: UUID, dataset_id: UUID, version: int | None = None) -> dict:
        resolved = self.analysis.resolve(user_id, dataset_id, version)
        checks = parse_checks(self._stored(user_id, dataset_id))
        results = {r["id"]: r for r in self.results(resolved, checks)}
        return {
            "version": resolved.version.version,
            "checks": [{**c.model_dump(mode="json"), "result": results.get(c.id)} for c in checks],
        }

    def save(self, user_id: UUID, dataset_id: UUID, payload: ChecksPayload) -> dict:
        stored = self.analysis.repo.set_checks(user_id, dataset_id, [c.model_dump(mode="json") for c in payload.checks])
        if stored is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        return self.view(user_id, dataset_id)

    def health_with_checks(self, user_id: UUID, resolved: Resolved, health: dict) -> dict:
        checks = parse_checks(self._stored(user_id, resolved.dataset.id))
        if not checks:
            return health
        return merge_into_health(health, check_issues(checks, self.results(resolved, checks)))
