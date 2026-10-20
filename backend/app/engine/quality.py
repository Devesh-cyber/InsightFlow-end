"""
Data-quality issues: the evidence behind the Health page and Cleaning recommendations.

Every issue says:
- what is wrong and how bad it is (severity + counts + examples)
- the OPTIONS for handling it, each a ready-to-run cleaning operation
- which option we recommend and WHY, or that we recommend none (the user decides)

Nothing here changes data. The health score is a transparent sum of named
deductions, so a learner can see exactly why a dataset scored 72.
"""

import polars as pl

from app.engine.matching import with_matches
from app.engine.infer import DATE_ISO_RE, FLOAT_RE, text_column_suggestions
from app.engine.profile import column_role, iqr_bounds, is_numeric, is_temporal, pct
from app.engine.rules import EMAIL_RE
from app.engine.validity import find_relationships, find_validity_issues

SEVERITY_ORDER = {"critical": 0, "warning": 1, "info": 2}


def _option(label: str, op_type: str, params: dict, recommended: bool = False, note: str | None = None) -> dict:
    return {"label": label, "operation": {"type": op_type, "params": params}, "recommended": recommended, "note": note}


def _missing_severity(percent: float) -> str:
    if percent >= 30:
        return "critical"
    if percent >= 5:
        return "warning"
    return "info"


def _record_kind(series: pl.Series) -> str | None:
    """'email' / 'date' / 'identifier' when most values follow that pattern (so filling would invent data)."""
    values = series.drop_nulls()
    if series.dtype != pl.String or len(values) == 0:
        return None
    text = values.str.strip_chars()
    if float(text.str.contains(EMAIL_RE).mean()) >= 0.8:
        return "email"
    if float(text.str.contains(DATE_ISO_RE).mean()) >= 0.8:
        return "date"
    if len(values) >= 20 and values.n_unique() >= 0.9 * len(values) and float(text.str.contains(r"\s").mean()) <= 0.5:
        return "identifier"
    return None


def _missing_issue(df: pl.DataFrame, name: str, role: str) -> dict | None:
    series = df.get_column(name)
    missing = series.null_count()
    if missing == 0 or missing == df.height:
        return None
    percent = pct(missing, df.height)
    options: list[dict] = []
    reason = ""

    if is_numeric(series.dtype):
        values = series.drop_nulls().cast(pl.Float64)
        skew = float(values.skew()) if len(values) > 2 and values.n_unique() > 1 else 0.0
        skewed = abs(skew) > 1
        high = percent >= 70
        options += [
            # Estimates are never "recommended": they invent values. The reason says which one fits better.
            _option("Fill with median", "fill_missing", {"columns": [name], "strategy": "median"},
                    note=f"An estimate. Robust to outliers; skewness is {skew:.2f}."),
            _option("Fill with mean", "fill_missing", {"columns": [name], "strategy": "mean"},
                    note="An estimate. Good when values are roughly symmetric."),
        ]
        if not skewed:
            options.reverse()  # list the better fit first
        reason = (
            "Too many values are missing to estimate reliably; consider dropping the column or keeping the gaps."
            if high else
            f"If you fill, {'the median' if skewed else 'the mean'} fits best because the distribution is "
            f"{'skewed' if skewed else 'roughly symmetric'} (skewness {skew:.2f}). Filled values are estimates and the report marks them."
        )
    elif series.dtype == pl.String or series.dtype == pl.Boolean:
        categorical = role in ("categorical", "boolean")
        kind = _record_kind(series)
        text_values = series.drop_nulls().cast(pl.String).str.strip_chars() if series.dtype == pl.String else None
        numberish = (text_values is not None and len(text_values) > 0
                     and float(text_values.str.contains(FLOAT_RE).mean()) >= 0.8)
        if numberish:
            reason = ("This column is mostly numbers stored as text. Convert it to numbers first (see the other issue), "
                      "then decide how to handle the gaps.")
        elif kind:
            # Emails, dates and IDs describe ONE record: no value from other rows is a sensible estimate.
            reason = (f"Values in this column look like {kind}s, which belong to one record each, so they can't be estimated. "
                      "Leave the gaps (they stay visible as missing), remove those rows, or set a value yourself with a custom rule.")
        else:
            repeats = series.drop_nulls().n_unique() < len(series.drop_nulls())
            if repeats:
                options.append(_option("Fill with most common value", "fill_missing", {"columns": [name], "strategy": "mode"},
                                       note="An estimate. Reasonable for a few gaps in a category column."))
            if series.dtype == pl.String:
                options.append(
                    _option("Fill with 'Unknown'", "fill_missing", {"columns": [name], "strategy": "constant", "value": "Unknown"},
                            note="Keeps the rows; 'Unknown' becomes its own category.")
                )
            reason = (
                "The column is a category with few gaps, so the most common value is a reasonable fill if you need one "
                "(it is still a guess, so the report marks it)."
                if categorical and percent < 30 and repeats else
                "Many values are missing; 'Unknown' keeps them visible as their own category instead of guessing."
                if categorical and percent < 70 else
                "There is no safe estimate for free text; leave the gaps or fill with an explicit 'Unknown' so they stay visible."
            )
    elif is_temporal(series.dtype):
        options.append(_option("Fill with previous value", "fill_missing", {"columns": [name], "strategy": "forward"},
                               note="Only sensible if rows are in time order."))
        reason = "Dates rarely have a safe automatic fill; review before filling."

    options += [
        _option("Drop rows missing this value", "drop_missing_rows", {"how": "any", "columns": [name]},
                note=f"Removes {missing} row(s) ({percent}%)."),
        _option("Drop the column", "drop_columns", {"columns": [name]},
                note="Only if the column isn't needed."),
    ]
    return {
        "id": f"missing_values:{name}",
        "category": "missing_values",
        "severity": _missing_severity(percent),
        "column": name,
        "title": f"{missing} missing value(s) in '{name}'",
        "message": f"{percent}% of '{name}' is empty. {reason}",
        "evidence": {"missing": missing, "rows": df.height, "pct": percent},
        "options": options,
    }


def find_issues(df: pl.DataFrame) -> list[dict]:
    issues: list[dict] = []
    rows = df.height
    if rows == 0:
        return issues

    nulls = dict(zip(df.columns, df.select(pl.all().null_count()).row(0)))
    uniques = dict(zip(df.columns, df.select(pl.all().drop_nulls().n_unique()).row(0)))

    # --- whole-table: duplicate rows
    duplicates = rows - df.unique(maintain_order=False).height
    if duplicates:
        issues.append({
            "id": "duplicates",
            "category": "duplicates",
            "severity": "warning",
            "column": None,
            "title": f"{duplicates} duplicate row(s)",
            "message": f"{duplicates} row(s) ({pct(duplicates, rows)}%) are exact copies of an earlier row. "
                       "Remove them unless repeated records are genuinely expected.",
            "evidence": {"duplicates": duplicates, "rows": rows, "pct": pct(duplicates, rows)},
            "options": [_option("Remove exact duplicates (keep first)", "drop_duplicates", {"columns": None, "keep": "first"}, recommended=True)],
        })

    relations = {r["target"]: r for r in find_relationships(df)}
    for name, dtype in zip(df.columns, df.dtypes):
        non_null = rows - nulls[name]
        role = column_role(dtype, rows, non_null, uniques[name])

        if role == "empty":
            issues.append({
                "id": f"empty_column:{name}", "category": "empty_column", "severity": "warning", "column": name,
                "title": f"'{name}' is completely empty",
                "message": "The column has no values at all and adds nothing to analysis.",
                "evidence": {"rows": rows},
                "options": [_option("Drop the column", "drop_columns", {"columns": [name]}, recommended=True)],
            })
            continue
        if role == "constant":
            value = df.get_column(name).drop_nulls()[0]
            issues.append({
                "id": f"constant_column:{name}", "category": "constant_column", "severity": "info", "column": name,
                "title": f"'{name}' has only one value",
                "message": f"Every non-empty value is {value!r}. It can't explain any variation.",
                "evidence": {"value": str(value), "non_null": non_null},
                "options": [_option("Drop the column", "drop_columns", {"columns": [name]}, recommended=True)],
            })

        missing_issue = _missing_issue(df, name, role)
        if missing_issue:
            issues.append(missing_issue)

        if dtype == pl.String:
            for suggestion in text_column_suggestions(df.get_column(name)):
                category = "whitespace" if suggestion["kind"] == "trim_whitespace" else "type_mismatch"
                issues.append({
                    "id": suggestion["id"],
                    "category": category,
                    "severity": "info" if category == "whitespace" else "warning",
                    "column": name,
                    "title": suggestion["message"].split(".")[0] + ".",
                    "message": suggestion["message"],
                    "evidence": suggestion["evidence"],
                    "options": [_option(_label_for(suggestion), suggestion["operation"]["type"], suggestion["operation"]["params"], recommended=True)],
                })

        if is_numeric(dtype) and role == "numeric":
            values_all = df.get_column(name).drop_nulls().cast(pl.Float64)
            if len(values_all) >= 20 and values_all.n_unique() > 2:
                # Measure skew without far-out values: one wrong entry (10,000 among 1-7) is an outlier, not skew.
                q1, q3 = values_all.quantile(0.25), values_all.quantile(0.75)
                spread = (q3 - q1) if q1 is not None and q3 is not None else 0
                core = values_all.filter(values_all.is_between(q1 - 3 * spread, q3 + 3 * spread)) if spread else values_all
                skew = float(core.skew() or 0.0) if len(core) >= 20 and core.n_unique() > 2 else 0.0
                if abs(skew) > 1:
                    side = "right" if skew > 0 else "left"
                    tail = "large" if skew > 0 else "small"
                    issues.append({
                        "id": f"skewed:{name}", "category": "skewed", "severity": "info", "column": name,
                        "title": f"'{name}' is strongly {side}-skewed (skewness {skew:.2f})",
                        "message": f"Most values sit together with a long tail of {tail} ones. That's common in real data "
                                   "(prices, amounts, counts) and nothing to fix while cleaning. Tree models don't mind it; "
                                   "linear models often do better after a log transform, which belongs to the ML-ready step.",
                        "evidence": {"skewness": round(skew, 3), "n": len(values_all)},
                        "options": [],
                    })
            bounds = iqr_bounds(df.get_column(name))
            if bounds:
                _, _, lower, upper = bounds
                values = df.get_column(name).drop_nulls().cast(pl.Float64)
                count = int(((values < lower) | (values > upper)).sum())
                derived = relations.get(name)
                feeds = [r for r in relations.values() if name in (r["a"], r["b"])]
                if count and derived:
                    # A calculated column: its big values come from its inputs. Capping it would break the formula.
                    formula = f"{derived['a']} {'×' if derived['op'] == '*' else '+'} {derived['b']}"
                    issues.append({
                        "id": f"outliers:{name}", "category": "outliers", "severity": "info", "column": name,
                        "title": f"{count} large or small value(s) in '{name}'",
                        "message": f"'{name}' is calculated as {formula}, so unusual values here come from those columns. "
                                   f"Check {derived['a']} and {derived['b']} in these rows instead; capping '{name}' would "
                                   "break the formula.",
                        "evidence": {"count": count, "pct": pct(count, len(values)), "lower_bound": lower, "upper_bound": upper,
                                     "derived_from": formula},
                        "options": [],
                    })
                elif count:
                    breaks = "".join(f" Capping also breaks {r['target']} = {r['a']} {'×' if r['op'] == '*' else '+'} {r['b']} "
                                     "in those rows." for r in feeds)
                    issues.append({
                        "id": f"outliers:{name}", "category": "outliers", "severity": "info", "column": name,
                        "title": f"{count} possible outlier(s) in '{name}'",
                        "message": f"{count} value(s) fall outside {lower:.4g} to {upper:.4g} (1.5 x IQR rule). "
                                   "Outliers are often real; check them before changing anything." + breaks,
                        "evidence": {"count": count, "pct": pct(count, len(values)), "lower_bound": lower, "upper_bound": upper,
                                     "examples": sorted(values.filter((values < lower) | (values > upper)).unique().head(5).to_list())},
                        "options": [
                            _option("Cap to the bounds", "handle_outliers", {"column": name, "method": "iqr", "factor": 1.5, "action": "cap"},
                                    note="Replaces them with the limit: an estimate, not a correction."
                                         + (" Breaks the formula above." if feeds else "")),
                            _option("Remove those rows", "handle_outliers", {"column": name, "method": "iqr", "factor": 1.5, "action": "remove"},
                                    note=f"Removes {count} row(s)."),
                        ],
                    })

    issues.extend(find_validity_issues(df))
    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], i["category"], i["column"] or ""))
    with_matches(issues)  # every issue can show the rows it is about
    return tag_stages(issues)


def _label_for(suggestion: dict) -> str:
    return {
        "convert_to_number": "Convert to numbers (bad values become missing)",
        "parse_formatted_number": "Strip formatting and convert to numbers",
        "convert_to_date": "Convert to dates",
        "convert_to_boolean": "Convert to true/false",
        "trim_whitespace": "Trim spaces",
    }.get(suggestion["kind"], "Apply")


SECONDARY = {"outliers", "skewed", "constant_column"}  # review while exploring, after primary cleaning
STAGE_NOTE = {
    "primary": "Fix these first: wrong types, spellings, duplicates, invalid or impossible values, gaps.",
    "secondary": "Look at these while exploring, after primary cleaning. They are often real values, so check before changing.",
}


def stage_of(issue: dict) -> str:
    return "secondary" if issue["category"] in SECONDARY else "primary"


def tag_stages(issues: list[dict]) -> list[dict]:
    for issue in issues:
        issue["stage"] = stage_of(issue)
    return issues


def score_issues(issues: list[dict], rows: int, cols: int) -> dict:
    """Score from the OPEN primary issues only. Secondary items and issues marked 'looks fine' don't count."""
    counted = [i for i in issues if i.get("stage", stage_of(i)) == "primary" and not i.get("reviewed")]
    cells = rows * cols or 1
    by_category: dict[str, int] = {}
    for issue in counted:
        by_category[issue["category"]] = by_category.get(issue["category"], 0) + 1
    missing = sum(int(i["evidence"].get("missing", 0)) for i in counted if i["category"] == "missing_values")
    duplicates = next((i["evidence"]["duplicates"] for i in counted if i["category"] == "duplicates"), 0)
    deductions = [
        ("Missing values", min(30.0, 0.5 * pct(missing, cells))),
        ("Duplicate rows", min(15.0, 0.5 * pct(duplicates, rows or 1))),
        ("Empty columns", min(15.0, 5.0 * by_category.get("empty_column", 0))),
        ("Columns needing a type fix", min(15.0, 3.0 * by_category.get("type_mismatch", 0))),
        ("Stray whitespace", min(5.0, 1.0 * by_category.get("whitespace", 0))),
        ("Repeated IDs", min(10.0, 3.0 * by_category.get("duplicate_id", 0))),
        ("Values that look wrong", min(20.0, 2.0 * by_category.get("invalid_values", 0))),
        ("Columns that don't add up", min(20.0, 5.0 * by_category.get("broken_relationship", 0))),
        ("Inconsistent spellings", min(5.0, 1.0 * by_category.get("inconsistent_spelling", 0))),
        ("Your checks failing", min(30.0, 5.0 * by_category.get("check_failed", 0))),
    ]
    deductions = [{"reason": reason, "points": round(points, 1)} for reason, points in deductions if points > 0]
    score = round(max(0.0, 100.0 - sum(d["points"] for d in deductions)), 1)
    grade = "excellent" if score >= 95 else "good" if score >= 80 else "fair" if score >= 60 else "poor"
    open_issues = [i for i in issues if not i.get("reviewed")]
    all_categories: dict[str, int] = {}
    for issue in open_issues:
        all_categories[issue["category"]] = all_categories.get(issue["category"], 0) + 1
    stages = {}
    for stage in ("primary", "secondary"):
        items = [i for i in open_issues if i.get("stage", stage_of(i)) == stage]
        stages[stage] = {
            "open": len(items),
            "reviewed": sum(1 for i in issues if i.get("reviewed") and i.get("stage", stage_of(i)) == stage),
            "critical": sum(1 for i in items if i["severity"] == "critical"),
            "warning": sum(1 for i in items if i["severity"] == "warning"),
            "info": sum(1 for i in items if i["severity"] == "info"),
            "note": STAGE_NOTE[stage],
        }
    return {
        "score": score,
        "grade": grade,
        "deductions": deductions,
        "summary": {
            "critical": sum(1 for i in open_issues if i["severity"] == "critical"),
            "warning": sum(1 for i in open_issues if i["severity"] == "warning"),
            "info": sum(1 for i in open_issues if i["severity"] == "info"),
            "by_category": all_categories,
        },
        "stages": stages,
    }


def health_report(df: pl.DataFrame, issues: list[dict] | None = None) -> dict:
    issues = tag_stages(find_issues(df) if issues is None else issues)
    return {
        **score_issues(issues, df.height, df.width),
        "issues": issues,
        "metrics": {"rows": df.height, "columns": df.width},
        "notes": ["The score counts primary cleaning only. Outliers, skew and other secondary items don't lower it: "
                  "they are often genuine values."],
    }
