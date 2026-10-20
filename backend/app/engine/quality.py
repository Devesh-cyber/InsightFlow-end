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
from app.engine.validity import find_validity_issues

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
            bounds = iqr_bounds(df.get_column(name))
            if bounds:
                _, _, lower, upper = bounds
                values = df.get_column(name).drop_nulls().cast(pl.Float64)
                count = int(((values < lower) | (values > upper)).sum())
                if count:
                    issues.append({
                        "id": f"outliers:{name}", "category": "outliers", "severity": "info", "column": name,
                        "title": f"{count} possible outlier(s) in '{name}'",
                        "message": f"{count} value(s) fall outside {lower:.4g} to {upper:.4g} (1.5 x IQR rule). "
                                   "Outliers are often real; check them before changing anything.",
                        "evidence": {"count": count, "pct": pct(count, len(values)), "lower_bound": lower, "upper_bound": upper,
                                     "examples": sorted(values.filter((values < lower) | (values > upper)).unique().head(5).to_list())},
                        "options": [
                            _option("Cap to the bounds", "handle_outliers", {"column": name, "method": "iqr", "factor": 1.5, "action": "cap"},
                                    note="Replaces them with the limit: an estimate, not a correction."),
                            _option("Remove those rows", "handle_outliers", {"column": name, "method": "iqr", "factor": 1.5, "action": "remove"},
                                    note=f"Removes {count} row(s)."),
                        ],
                    })

    issues.extend(find_validity_issues(df))
    issues.sort(key=lambda i: (SEVERITY_ORDER[i["severity"]], i["category"], i["column"] or ""))
    with_matches(issues)  # every issue can show the rows it is about
    return issues


def _label_for(suggestion: dict) -> str:
    return {
        "convert_to_number": "Convert to numbers (bad values become missing)",
        "parse_formatted_number": "Strip formatting and convert to numbers",
        "convert_to_date": "Convert to dates",
        "convert_to_boolean": "Convert to true/false",
        "trim_whitespace": "Trim spaces",
    }.get(suggestion["kind"], "Apply")


def health_report(df: pl.DataFrame, issues: list[dict] | None = None) -> dict:
    issues = find_issues(df) if issues is None else issues
    rows, cols = df.height, df.width
    cells = rows * cols or 1
    missing = int(sum(df.select(pl.all().null_count()).row(0))) if cols else 0
    by_category: dict[str, int] = {}
    for issue in issues:
        by_category[issue["category"]] = by_category.get(issue["category"], 0) + 1

    duplicates = next((i["evidence"]["duplicates"] for i in issues if i["category"] == "duplicates"), 0)
    deductions = [
        ("Missing values", min(30.0, 0.5 * pct(missing, cells))),
        ("Duplicate rows", min(15.0, 0.5 * pct(duplicates, rows))),
        ("Empty columns", min(15.0, 5.0 * by_category.get("empty_column", 0))),
        ("Constant columns", min(10.0, 2.0 * by_category.get("constant_column", 0))),
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

    return {
        "score": score,
        "grade": grade,
        "deductions": deductions,
        "summary": {
            "critical": sum(1 for i in issues if i["severity"] == "critical"),
            "warning": sum(1 for i in issues if i["severity"] == "warning"),
            "info": sum(1 for i in issues if i["severity"] == "info"),
            "by_category": by_category,
        },
        "issues": issues,
        "notes": ["Outliers are reported but don't lower the score: they are often genuine values."],
    }
