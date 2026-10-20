"""
Turn analysis results that already exist (overview, health, columns, column
details, correlations, version history, recipe) into one report document.

Pure function: no database, no storage, no Polars. Everything it says is derived
from the numbers it is given, so a reader can trace every sentence back to
evidence shown elsewhere in the report.
"""

import datetime
import math
from typing import Any

APP_NAME = "InsightFlow"
MAX_ISSUES = 40
MAX_CHART_BARS = 8
SMALL_SAMPLE = 30

GRADE_LABEL = {"excellent": "Excellent", "good": "Good", "fair": "Fair", "poor": "Poor"}
ROLE_LABEL = {
    "numeric": "Numeric", "categorical": "Category", "temporal": "Date / time", "boolean": "True / false",
    "identifier": "Identifier", "free_text": "Free text", "constant": "Constant", "empty": "Empty",
}

METHODOLOGY = [
    {
        "term": "Health score",
        "text": "Starts at 100 and subtracts named deductions: missing values (0.5 points per % of empty cells, "
                "at most 30), duplicate rows (0.5 per % of rows, at most 15), empty columns (5 each, at most 15), "
                "constant columns (2 each, at most 10), columns needing a type fix (3 each, at most 15) and stray "
                "whitespace (1 per column, at most 5). Grades: 95+ excellent, 80+ good, 60+ fair, below 60 poor.",
    },
    {
        "term": "Outliers",
        "text": "A value is flagged when it falls outside Q1 - 1.5 x IQR or Q3 + 1.5 x IQR, where IQR = Q3 - Q1. "
                "Outliers are reported but do not lower the health score, because they are often genuine.",
    },
    {
        "term": "Correlation",
        "text": "Pearson r measures a straight-line relationship between two numeric columns (-1 to 1); Spearman's rho "
                "measures any steadily rising or falling one. |r| of 0.7+ is called strong, 0.4+ moderate, 0.2+ weak. "
                f"Pairs with fewer than {SMALL_SAMPLE} rows holding both values are flagged: with so few points the "
                "number changes a lot by chance. Correlation does not imply causation.",
    },
    {
        "term": "Versions",
        "text": "The uploaded file is never modified. Each cleaning step writes a new version, and reverting adds a "
                "version that matches an earlier one, so every step in the cleaning log can be undone.",
    },
    {
        "term": "Skewness",
        "text": "Describes asymmetry. Between -0.5 and 0.5 is roughly symmetric; beyond 1 (or -1) is strongly skewed, "
                "meaning the mean is pulled toward the long tail and the median is the better 'typical' value.",
    },
]


def _zone(name: str | None):
    """An IANA zone like 'Asia/Kolkata' from the browser; unknown names fall back to UTC."""
    if not name:
        return None
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(name)
    except Exception:
        return None


# --------------------------------------------------------------- formatting
def fmt_num(value: Any, digits: int = 4) -> str:
    """Human formatting: thousands separators, no float noise, '—' for missing."""
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return f"{value:,}"
    if isinstance(value, float):
        if not math.isfinite(value):
            return "—"
        if value == int(value) and abs(value) < 1e15:
            return f"{int(value):,}"
        if abs(value) >= 1000:
            return f"{value:,.2f}".rstrip("0").rstrip(".")
        return f"{value:.{digits}g}"
    return str(value)


def fmt_r(value: Any) -> str:
    """Correlations always show two decimals, so 1.00 doesn't read as a count."""
    return "—" if value is None else f"{float(value):.2f}"


def fmt_pct(value: Any) -> str:
    if value is None:
        return "—"
    return f"{fmt_num(round(float(value), 1))}%"


def fmt_bytes(size: int | None) -> str:
    if not size:
        return "—"
    units = ["B", "KB", "MB", "GB"]
    value = float(size)
    for unit in units:
        if value < 1024 or unit == units[-1]:
            return f"{value:.0f} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return f"{size} B"


def fmt_value(value: Any) -> str:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return fmt_num(value)
    if value is None:
        return "(empty)"
    text = str(value)
    return text if len(text) <= 40 else text[:39] + "…"


def _skew_words(skew: float | None) -> str | None:
    if skew is None:
        return None
    size = abs(skew)
    side = "right (a long tail of high values)" if skew > 0 else "left (a long tail of low values)"
    if size < 0.5:
        return "roughly symmetric"
    if size < 1:
        return f"moderately skewed to the {side}"
    return f"strongly skewed to the {side}"


# --------------------------------------------------------------- sections
def _dictionary(columns: list[dict]) -> list[dict]:
    rows = []
    for col in columns:
        if col.get("min") is not None and col.get("max") is not None:
            summary = f"{fmt_value(col['min'])} to {fmt_value(col['max'])}"
            if col.get("mean") is not None:
                summary += f", mean {fmt_num(col['mean'])}"
        else:
            samples = [fmt_value(v) for v in (col.get("sample_values") or [])[:3]]
            summary = "e.g. " + ", ".join(samples) if samples else "no values"
        rows.append({
            "name": col["name"],
            "dtype": col["dtype"],
            "role": ROLE_LABEL.get(col["role"], col["role"]),
            "missing": f"{fmt_num(col['null_count'])} ({fmt_pct(col['null_pct'])})",
            "missing_flag": col["null_pct"] > 0,
            "unique": fmt_num(col["unique_count"]),
            "summary": summary,
        })
    return rows


def _profile(detail: dict) -> dict:
    stats: list[list[str]] = [
        ["Missing", f"{fmt_num(detail['null_count'])} ({fmt_pct(detail['null_pct'])})"],
        ["Unique values", f"{fmt_num(detail['unique_count'])} ({fmt_pct(detail['unique_pct'])})"],
    ]
    chart: dict | None = None
    notes: list[str] = []
    non_null = detail["rows"] - detail["null_count"]

    if detail.get("numeric"):
        n = detail["numeric"]
        q = n.get("quantiles") or {}
        stats += [
            ["Mean", fmt_num(n.get("mean"))], ["Median", fmt_num(n.get("median"))],
            ["Std deviation", fmt_num(n.get("std"))], ["Min", fmt_num(n.get("min"))], ["Max", fmt_num(n.get("max"))],
            ["25th percentile", fmt_num(q.get("p25"))], ["75th percentile", fmt_num(q.get("p75"))],
            ["Skewness", fmt_num(n.get("skew"), 3)],
        ]
        out = n.get("outliers") or {}
        if out:
            stats.append(["Outliers (1.5 x IQR)", f"{fmt_num(out.get('count'))} outside {fmt_num(out.get('lower_bound'))} to {fmt_num(out.get('upper_bound'))}"])
        words = _skew_words(n.get("skew"))
        if words:
            notes.append(f"The distribution is {words}.")
        if out.get("count"):
            notes.append(f"{fmt_num(out['count'])} value(s) ({fmt_pct(out.get('pct'))}) sit outside the IQR bounds; check them before changing anything.")
        if detail.get("histogram"):
            chart = {
                "kind": "histogram",
                "bars": [{"label": f"{fmt_num(b['start'])} to {fmt_num(b['end'])}", "start": b["start"], "end": b["end"], "value": b["count"]}
                         for b in detail["histogram"]],
            }
    elif detail.get("temporal"):
        t = detail["temporal"]
        stats += [["Earliest", fmt_value(t.get("min"))], ["Latest", fmt_value(t.get("max"))],
                  ["Span", f"{fmt_num(t.get('span_days'))} days"]]
        if t.get("timeline"):
            chart = {"kind": "timeline", "period": t.get("period"),
                     "bars": [{"label": str(p["period"]), "value": p["count"]} for p in t["timeline"]]}
    else:
        if detail.get("text"):
            tx = detail["text"]
            stats.append(["Text length", f"{fmt_num(tx.get('min_length'))} to {fmt_num(tx.get('max_length'))} characters (mean {fmt_num(tx.get('mean_length'))})"])
            if tx.get("padded_with_spaces"):
                notes.append(f"{fmt_num(tx['padded_with_spaces'])} value(s) have leading or trailing spaces.")

    top = detail.get("top_values") or {}
    if chart is None and top.get("items"):
        items = top["items"][:MAX_CHART_BARS]
        chart = {"kind": "top_values",
                 "bars": [{"label": fmt_value(i["value"]), "value": i["count"], "pct": i.get("pct")} for i in items],
                 "other": top.get("other_count", 0)}
        first = top["items"][0]
        stats.append(["Most common", f"{fmt_value(first['value'])} ({fmt_num(first['count'])}, {fmt_pct(first.get('pct'))})"])

    if 0 < non_null < SMALL_SAMPLE:
        notes.append(f"Only {fmt_num(non_null)} non-empty value(s): summary statistics describe these rows, not a wider population.")

    return {"name": detail["name"], "dtype": detail["dtype"], "role": ROLE_LABEL.get(detail["role"], detail["role"]),
            "stats": stats, "chart": chart, "notes": notes}


def _issue(issue: dict) -> dict:
    recommended = next((o["label"] for o in issue.get("options", []) if o.get("recommended")), None)
    return {"severity": issue["severity"], "title": issue["title"], "message": issue["message"],
            "column": issue.get("column"), "recommended": recommended, "stage": issue.get("stage", "primary")}


def _comparison(before_o: dict | None, before_h: dict | None, now_o: dict, now_h: dict) -> list[dict]:
    def issues(h: dict) -> int:
        s = h["summary"]
        return s["critical"] + s["warning"] + s["info"]

    rows = [
        ("Health score", "score", lambda o, h: h["score"], True),
        ("Rows", "rows", lambda o, h: o["rows"], None),
        ("Columns", "columns", lambda o, h: o["columns"], None),
        ("Completeness", "pct", lambda o, h: o["completeness_pct"], True),
        ("Missing cells", "num", lambda o, h: o["missing_cells"], False),
        ("Duplicate rows", "num", lambda o, h: o["duplicate_rows"], False),
        ("Open issues", "num", lambda o, h: issues(h), False),
    ]
    out = []
    for label, kind, get, higher_is_better in rows:
        after = get(now_o, now_h)
        before = get(before_o, before_h) if before_o and before_h else None
        fmt = fmt_pct if kind == "pct" else fmt_num
        change = None
        if before is not None and higher_is_better is not None and before != after:
            change = "better" if (after > before) == higher_is_better else "worse"
        out.append({"label": label, "before": fmt(before) if before is not None else "—", "after": fmt(after), "change": change})
    return out


def _estimate_kind(operation: dict) -> str | None:
    """Steps whose new values are guesses rather than observed data."""
    kind, params = operation.get("type"), operation.get("params") or {}
    if kind == "fill_missing" and params.get("strategy") in ("mean", "median", "mode", "forward", "backward", "constant"):
        return "filled gaps"
    if kind == "handle_outliers" and params.get("action") == "cap":
        return "capped values"
    if kind == "apply_rule" and params.get("action") in ("clamp", "set_value"):
        return "replaced values"
    return None


def _findings(meta: dict, now_o: dict, now_h: dict, before_o: dict | None, before_h: dict | None, steps: list[dict], pairs: list[dict]) -> list[str]:
    found = [f"{fmt_num(now_o['rows'])} rows and {fmt_num(now_o['columns'])} columns from {meta['source_filename']}"
             + (f" (sheet '{meta['sheet_name']}')" if meta.get("sheet_name") else "") + "."]
    cleaning = [s for s in steps if s["operation"].get("type") not in ("upload",)]
    if before_h is not None and cleaning:
        direction = "rose" if now_h["score"] > before_h["score"] else "fell" if now_h["score"] < before_h["score"] else "stayed"
        verb = f"{direction} from {fmt_num(before_h['score'])} to {fmt_num(now_h['score'])}" if direction != "stayed" else f"stayed at {fmt_num(now_h['score'])}"
        found.append(f"The health score {verb} ({GRADE_LABEL.get(now_h['grade'], now_h['grade'])}) over {len(cleaning)} cleaning step(s).")
    else:
        found.append(f"The health score is {fmt_num(now_h['score'])} ({GRADE_LABEL.get(now_h['grade'], now_h['grade'])}). No cleaning steps have been applied yet.")
    cells = sum(s.get("cells_changed") or 0 for s in cleaning)
    removed = sum(s.get("rows_removed") or 0 for s in cleaning)
    if cells or removed:
        found.append(f"Cleaning changed {fmt_num(cells)} cell(s) and removed {fmt_num(removed)} row(s) in total.")
    estimated = sum(s.get("cells_changed") or 0 for s in cleaning if _estimate_kind(s["operation"]))
    if estimated:
        kinds = sorted({_estimate_kind(s["operation"]) for s in cleaning if _estimate_kind(s["operation"])})
        found.append(f"{fmt_num(estimated)} of the changed cells are estimates ({', '.join(kinds)}), not values that were "
                     "in the data. They are marked 'estimate' in the cleaning log; treat totals built on them with care.")
    failing = [i for i in now_h.get("issues", []) if i["category"] == "check_failed"]
    if failing:
        found.append(f"{len(failing)} of your saved checks still fail: " + "; ".join(i["title"].split("'")[1] for i in failing[:3])
                     + (" …" if len(failing) > 3 else "") + ".")
    completeness = f"The data is {fmt_pct(now_o['completeness_pct'])} complete ({fmt_num(now_o['missing_cells'])} empty cell(s))."
    if before_o is not None and now_o["missing_cells"] > before_o["missing_cells"]:
        completeness += (" That is more empty cells than before, because cleaning turned invalid entries "
                         "(such as '?' or '-') into honest gaps rather than keeping them as fake values.")
    found.append(completeness)
    stages = now_h.get("stages")
    if stages:
        p, sec = stages["primary"], stages["secondary"]
        if p["open"]:
            found.append(f"Primary cleaning: {p['open']} issue(s) still open" + (f", {p['reviewed']} reviewed and kept as they are" if p["reviewed"] else "") + ".")
        else:
            found.append("Primary cleaning is complete" + (f" ({p['reviewed']} item(s) reviewed and kept as they are)" if p["reviewed"] else "") + ".")
        if sec["open"]:
            found.append(f"Secondary review: {sec['open']} item(s) to look at while exploring (outliers, skew). They don't lower the score.")
    else:
        s = now_h["summary"]
        total = s["critical"] + s["warning"] + s["info"]
        found.append(f"{total} issue(s) are still open." if total else "No open data-quality issues were detected.")
    dropped = sorted({c for st in cleaning if st["operation"].get("type") == "drop_columns" for c in (st["operation"].get("params") or {}).get("columns") or []})
    if dropped:
        found.append(f"Columns removed during cleaning: {', '.join(dropped)}. Issues in those columns no longer appear.")
    strong = [p for p in pairs if p.get("r") is not None and abs(p["r"]) >= 0.4]
    if strong:
        p = strong[0]
        caveat = f", but from only {p['n']} rows, so treat it as a hint" if p.get("small_sample") else ""
        found.append(f"Strongest numeric relationship: '{p['a']}' and '{p['b']}' (r = {fmt_r(p['r'])}, {p['strength']}{caveat}).")
    return found


# --------------------------------------------------------------- entry point
def build_report(
    *,
    dataset: dict,
    version: int,
    overview: dict,
    health: dict,
    columns: list[dict],
    details: list[dict],
    correlations: dict,
    steps: list[dict],
    recipe: str,
    before: dict | None = None,
    keep_versions: int | None = None,
    title: str | None = None,
    author: str | None = None,
    generated_at: datetime.datetime | None = None,
    tz: str | None = None,
) -> dict:
    """
    dataset: name, source_filename, source_format, source_size_bytes, sheet_name, sheet_names
    before:  {"version": n, "overview": {...}, "health": {...}} for the earliest kept version, or None
    steps:   version_view() dicts, oldest first
    """
    generated_at = generated_at or datetime.datetime.now(datetime.timezone.utc)
    zone = _zone(tz)
    local = generated_at.astimezone(zone) if zone else generated_at.astimezone(datetime.timezone.utc)
    zone_label = local.tzname() or "UTC"
    meta = {
        "title": (title or "").strip() or f"Data report: {dataset['name']}",
        "author": (author or "").strip() or None,
        "dataset_name": dataset["name"],
        "source_filename": dataset["source_filename"],
        "source_format": dataset["source_format"].upper(),
        "source_size": fmt_bytes(dataset.get("source_size_bytes")),
        "sheet_name": dataset.get("sheet_name"),
        "sheet_count": len(dataset.get("sheet_names") or []) or None,
        "version": version,
        "generated_at": local.isoformat(),
        "generated_display": local.strftime("%d %B %Y, %H:%M ") + zone_label,
        "app": APP_NAME,
    }

    before_o = before["overview"] if before else None
    before_h = before["health"] if before else None
    pairs = correlations.get("top_pairs") or []

    all_issues = health.get("issues") or []
    issues = [i for i in all_issues if not i.get("reviewed")]
    reviewed = [i for i in all_issues if i.get("reviewed")]
    pruned_note = None
    if steps and steps[0]["version"] > 1:
        pruned_note = (f"Only the most recent {keep_versions or len(steps)} versions are kept, so the log starts at "
                       f"version {steps[0]['version']}; 'before' figures refer to that version, not the original upload.")

    profiled = {d["name"] for d in details}
    report = {
        "meta": meta,
        "summary": {
            "kpis": [
                {"label": "Rows", "value": fmt_num(overview["rows"])},
                {"label": "Columns", "value": fmt_num(overview["columns"])},
                {"label": "Completeness", "value": fmt_pct(overview["completeness_pct"])},
                {"label": "Health", "value": fmt_num(health["score"]), "hint": GRADE_LABEL.get(health["grade"], health["grade"]),
                 "before": fmt_num(before_h["score"]) if before_h else None},
            ],
            "findings": _findings(meta, overview, health, before_o, before_h, steps, pairs),
        },
        "dataset": {
            "rows": fmt_num(overview["rows"]), "columns": fmt_num(overview["columns"]), "cells": fmt_num(overview["cells"]),
            "types": [{"label": k, "count": v} for k, v in sorted(overview.get("dtype_counts", {}).items(), key=lambda kv: -kv[1])],
            "roles": [{"label": ROLE_LABEL.get(k, k), "count": v} for k, v in sorted(overview.get("role_counts", {}).items(), key=lambda kv: -kv[1])],
        },
        "dictionary": _dictionary(columns),
        "quality": {
            "score": health["score"], "grade": GRADE_LABEL.get(health["grade"], health["grade"]),
            "before_version": before["version"] if before else None,
            "comparison": _comparison(before_o, before_h, overview, health),
            "deductions": [{"reason": d["reason"], "points": fmt_num(d["points"])} for d in health.get("deductions", [])],
            "issues": [_issue(i) for i in issues[:MAX_ISSUES]],
            "issues_omitted": max(0, len(issues) - MAX_ISSUES),
            "reviewed": [{"title": i["title"], "column": i.get("column"), "stage": i.get("stage", "primary")} for i in reviewed],
            "stages": health.get("stages"),
            "notes": health.get("notes", []),
        },
        "cleaning": {
            "steps": [
                {"version": s["version"], "description": s["description"], "rows": fmt_num(s["rows"]), "columns": fmt_num(s["columns"]),
                 "cells_changed": fmt_num(s.get("cells_changed")) if s.get("cells_changed") is not None else "—",
                 "rows_removed": fmt_num(s.get("rows_removed")) if s.get("rows_removed") is not None else "—",
                 "warnings": s.get("warnings") or [], "is_current": s.get("is_current", False),
                 "estimate": _estimate_kind(s["operation"])}
                for s in steps
            ],
            "count": sum(1 for s in steps if s["operation"].get("type") != "upload"),
            "pruned_note": pruned_note,
        },
        "profiles": [_profile(d) for d in details],
        "profiles_omitted": [c["name"] for c in columns if c["name"] not in profiled],
        "relationships": {
            "pairs": [{"a": p["a"], "b": p["b"], "r": fmt_r(p["r"]), "strength": p["strength"],
                       "n": fmt_num(p.get("n")) if p.get("n") is not None else "—", "small_sample": bool(p.get("small_sample"))}
                      for p in pairs],
            "note": correlations.get("note") or ("Pairs are listed strongest first. Correlation does not imply causation."
                                                if pairs else "No numeric pairs to compare."),
            "method": correlations.get("method", "pearson"),
        },
        "recipe": recipe,
        "methodology": METHODOLOGY,
    }
    return report
