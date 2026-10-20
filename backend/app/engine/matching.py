"""
"Show me the rows": every issue (and every rule or check) can say which rows it is about.

An issue's `match` is either
  {"conditions": [...], "combine": "and"|"or"}   evaluated with the rule engine, or
  {"duplicate_rows": true}                       every copy of a fully repeated row,
or None when the issue is about a whole column (empty / constant / a type to convert).
"""

import polars as pl

from app.core.errors import UnprocessableError
from app.engine.jsonsafe import to_json
from app.engine.rules import Condition, evaluate

MAX_PAGE = 100


def _from_options(issue: dict) -> dict | None:
    for option in issue.get("options", []):
        op = option.get("operation", {})
        params = op.get("params", {})
        if op.get("type") == "apply_rule" and params.get("conditions"):
            return {"conditions": params["conditions"], "combine": params.get("combine", "and")}
    return None


def match_for(issue: dict) -> dict | None:
    category, column, evidence = issue["category"], issue.get("column"), issue.get("evidence") or {}
    if category == "duplicates":
        return {"duplicate_rows": True}
    if category == "missing_values":
        return {"conditions": [{"column": column, "op": "is_empty"}], "combine": "and"}
    if category == "duplicate_id":
        return {"conditions": [{"column": column, "op": "duplicate"}], "combine": "and"}
    if category == "whitespace":
        return {"conditions": [{"column": column, "op": "extra_spaces"}], "combine": "and"}
    if category == "inconsistent_spelling" and evidence.get("mapping"):
        values = sorted(set(evidence["mapping"]) | set(evidence["mapping"].values()))
        return {"conditions": [{"column": column, "op": "in", "value": values}], "combine": "and"}
    if category == "outliers" and "lower_bound" in evidence:
        return {"conditions": [{"column": column, "op": "not_between",
                                "value": evidence["lower_bound"], "value2": evidence["upper_bound"]}], "combine": "and"}
    if category == "type_mismatch" and issue["id"].startswith(("convert_to_number:", "parse_formatted_number:")):
        return {"conditions": [{"column": column, "op": "invalid_number"}], "combine": "and"}
    if category == "type_mismatch" and issue["id"].startswith("convert_to_date:"):
        return {"conditions": [{"column": column, "op": "invalid_date", "value": "%Y-%m-%d"}], "combine": "and"}
    return _from_options(issue)  # validity checks and saved checks carry their own conditions


def with_matches(issues: list[dict]) -> list[dict]:
    for issue in issues:
        issue["match"] = match_for(issue)
    return issues


def matching_rows(df: pl.DataFrame, conditions: list[Condition] | None, combine: str, duplicate_rows: bool,
                  offset: int, limit: int) -> dict:
    if duplicate_rows:
        mask = df.is_duplicated()
        highlight = []
    else:
        if not conditions:
            raise UnprocessableError("Add at least one condition.", code="invalid_rule")
        mask = evaluate(df, conditions, combine)
        highlight = list(dict.fromkeys(
            [c.column for c in conditions]
            + [t.column for c in conditions for t in (c.formula or []) if not isinstance(t, str) and t.column]
        ))
    total = int(mask.sum())
    page = df.with_row_index("__row").filter(mask)
    if duplicate_rows:  # put copies next to each other
        page = page.sort([c for c in df.columns], nulls_last=True, maintain_order=True)
    page = page.slice(offset, min(limit, MAX_PAGE))
    return {
        "total": total,
        "rows_in_version": df.height,
        "columns": df.columns,
        "highlight": highlight,
        "rows": [{"row": int(r[0]) + 1, "values": to_json(list(r[1:]))} for r in page.iter_rows()],
        "offset": offset,
    }
