"""
Saved checks: rules the user wants to hold on EVERY version ("age is between 0 and 120",
"order_id is never repeated", "total = units × price").

A check is written as the condition that marks a BAD row (the same condition builder
as custom rules). Results are evaluated per version, cached in memory, and merged
into Health, Recommendations and the report as `check_failed` issues, so a dataset
can't look "clean" while one of the user's own rules is broken.
"""

import hashlib
import re
from uuid import UUID

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.cache import params_key
from app.core.errors import NotFoundError, UnprocessableError
from app.engine.jsonsafe import to_json
from app.engine.matching import with_matches
from app.engine.quality import score_issues, tag_stages
from app.engine.rules import Condition, describe_conditions, evaluate
from app.services.analysis import AnalysisService, Resolved

MAX_CHECKS = 25
SAMPLE_ROWS = 5
CHECK_PENALTY = 5.0
CHECK_PENALTY_MAX = 30.0
MAX_REVIEWED = 300


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


def fingerprint(issue: dict) -> str:
    """Changes when the issue changes (its title carries the counts), so a stale 'looks fine' doesn't hide new problems."""
    return hashlib.sha256(f"{issue['id']}|{issue['title']}".encode()).hexdigest()[:16]


def apply_context(health: dict, extra: list[dict], reviewed: list[dict]) -> dict:
    """
    Add check issues and the user's 'looks fine' marks to a (cached) health report, then re-score.
    Never mutates the input.
    """
    marks = {r.get("issue_id"): r for r in reviewed or []}
    issues = []
    for issue in [*health["issues"], *tag_stages([dict(i) for i in extra])]:
        item = {**issue, "fingerprint": fingerprint(issue)}
        mark = marks.get(issue["id"])
        item["reviewed"] = bool(mark and mark.get("fingerprint") == item["fingerprint"])
        item["changed_since_review"] = bool(mark and not item["reviewed"])
        issues.append(item)
    order = {"critical": 0, "warning": 1, "info": 2}
    issues.sort(key=lambda i: (order[i["severity"]], i["category"], i["column"] or ""))
    metrics = health.get("metrics") or {}
    rows = metrics.get("rows", 0)
    cols = metrics.get("columns", 0)
    return {**health, **score_issues(issues, rows, cols), "issues": issues}


def merge_into_health(health: dict, extra: list[dict]) -> dict:  # kept for callers/tests
    return apply_context(health, extra, [])


class ReviewMark(BaseModel):
    model_config = ConfigDict(extra="forbid")
    issue_id: str = Field(min_length=1, max_length=300)
    fingerprint: str = Field(pattern=r"^[0-9a-f]{16}$")
    title: str = Field(default="", max_length=300)
    note: str | None = Field(default=None, max_length=300)


class ReviewPayload(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reviewed: list[ReviewMark] = Field(max_length=MAX_REVIEWED)


class ChecksService:
    def __init__(self, analysis: AnalysisService) -> None:
        self.analysis = analysis

    def _stored(self, user_id: UUID, dataset_id: UUID) -> list[dict]:
        return self._context(user_id, dataset_id)["checks"]

    def _context(self, user_id: UUID, dataset_id: UUID) -> dict:
        context = self.analysis.repo.get_context(user_id, dataset_id)
        if context is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        return context

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
        """Health with the user's saved checks and 'looks fine' marks applied (used by Health, Clean and the report)."""
        context = self._context(user_id, resolved.dataset.id)
        checks = parse_checks(context["checks"])
        extra = check_issues(checks, self.results(resolved, checks)) if checks else []
        return apply_context(health, extra, context["reviewed"])

    def reviewed(self, user_id: UUID, dataset_id: UUID) -> dict:
        return {"reviewed": self._context(user_id, dataset_id)["reviewed"]}

    def save_reviewed(self, user_id: UUID, dataset_id: UUID, payload: ReviewPayload) -> dict:
        stored = self.analysis.repo.set_reviewed(user_id, dataset_id, [m.model_dump(mode="json") for m in payload.reviewed])
        if stored is None:
            raise NotFoundError("Dataset not found.", code="dataset_not_found")
        return {"reviewed": stored}
