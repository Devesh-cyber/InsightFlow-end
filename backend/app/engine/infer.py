"""
Type inference: turn an all-text table into a typed one, evidence first.

Two kinds of outcome per column:

1. APPLIED automatically: only conversions that lose nothing.
   - Standard missing markers ("NA", "N/A", "null", "NaN", "None", "#N/A",
     blank / whitespace-only) become real missing values. Counts are reported.
   - A column becomes integer / float / boolean / date / datetime only if
     EVERY non-missing value converts cleanly.
   - Numbers with leading zeros ("00123") and very long digit strings stay
     text: converting them would destroy information (zip codes, IDs, phones).

2. SUGGESTED, never applied silently: anything that requires a judgement.
   - "age" is 96% numbers but has "?" and "-"   -> convert, treating those as missing?
   - "price" looks like "₹1,20,000"             -> strip formatting and convert?
   - "joined" looks like "05/04/2024"           -> day-first or month-first?
   - "active" is yes/no                          -> convert to true/false?
   - values have stray spaces                    -> trim them?
   Each suggestion carries the evidence (counts + examples) and the exact
   cleaning operation that would perform it, so the frontend can show a
   preview and the user decides. The operations run in Phase 4.
"""

import re
from dataclasses import dataclass, field

import polars as pl

STANDARD_MISSING = ["", "NA", "N/A", "n/a", "NULL", "null", "NaN", "nan", "None", "#N/A"]

MISSING_MARKER_RE = r"^\s*(?:" + "|".join(re.escape(m) for m in STANDARD_MISSING if m) + r")?\s*$"

INT_RE = r"^[+-]?\d+$"
FLOAT_RE = r"^[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?$"
LEADING_ZERO_RE = r"^[+-]?0\d"
MAX_INT_DIGITS = 18  # beyond this, int64 overflows and values are usually identifiers
DATE_ISO_RE = r"^\d{4}-\d{2}-\d{2}$"
DATETIME_ISO_RE = r"^\d{4}-\d{2}-\d{2}[ T]\d{2}:\d{2}(?::\d{2}(?:\.\d{1,9})?)?$"
DATETIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M:%S%.f",
    "%Y-%m-%dT%H:%M:%S%.f",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%dT%H:%M",
]
DMY_RE = r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{4})$"
FORMATTED_NUMBER_RE = r"^[+-]?\s?[₹$€£]?\s?(?:\d{1,3}(?:,\d{2,3})+|\d+)(?:\.\d+)?\s?%?$"
BOOLEAN_TEXT = {"true", "false"}
YES_NO_PAIRS = [({"yes"}, {"no"}), ({"y"}, {"n"})]

MOSTLY_NUMERIC_RATIO = 0.8
PLACEHOLDER_NUMERIC_RATIO = 0.5
PLACEHOLDER_RE = r"^[^\w\s]{1,3}$"  # "?", "-", "--", ".", "*"
MAX_FAILING_DISTINCT = 20
PATTERN_RATIO = 0.95
EXAMPLES = 5

DTYPE_LABELS = {
    pl.Int64: "integer",
    pl.Float64: "float",
    pl.Boolean: "boolean",
    pl.Date: "date",
    pl.String: "text",
}


def dtype_label(dtype: pl.DataType) -> str:
    if isinstance(dtype, pl.Datetime):
        return "datetime"
    for polars_type, label in DTYPE_LABELS.items():
        if dtype == polars_type:
            return label
    if dtype.is_integer():
        return "integer"
    if dtype.is_float():
        return "float"
    return "text"


@dataclass
class InferenceResult:
    df: pl.DataFrame
    columns: list[dict]
    suggestions: list[dict] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def infer_types(raw: pl.DataFrame) -> InferenceResult:
    converted: dict[str, pl.Series] = {}
    columns: list[dict] = []
    suggestions: list[dict] = []
    warnings: list[str] = []

    for name in raw.columns:
        series, report, column_suggestions, column_warnings = _infer_column(raw.get_column(name))
        converted[name] = series
        columns.append(report)
        suggestions.extend(column_suggestions)
        warnings.extend(column_warnings)

    df = pl.DataFrame([converted.pop(name) for name in raw.columns])
    return InferenceResult(df=df, columns=columns, suggestions=suggestions, warnings=warnings)


# ---------------------------------------------------------------------------
def _infer_column(series: pl.Series) -> tuple[pl.Series, dict, list[dict], list[str]]:
    name = series.name

    # 1) Standard missing markers -> null, with counts per marker.
    #    (Only rebuild the column if it actually contains markers: saves a full copy.)
    is_marker = series.str.contains(MISSING_MARKER_RE).fill_null(False)  # regex: no string copy
    marker_counts: dict[str, int] = {}
    if is_marker.any():
        counts = series.filter(is_marker).str.strip_chars().value_counts(sort=True)
        for value, count in counts.iter_rows():
            marker_counts["(blank)" if value == "" else value] = int(count)
        series = pl.select(pl.when(is_marker).then(None).otherwise(series).alias(name)).to_series()
    del is_marker

    values = series.drop_nulls() if series.null_count() else series
    trimmed = values.str.strip_chars()
    total = len(values)

    report = {
        "name": name,
        "dtype": "text",
        "null_count": int(series.null_count()),
        "converted_from_text": False,
        "missing_markers": marker_counts,
    }

    if total == 0:
        report["dtype"] = "text"
        return series, report, [], [f"Column '{name}' has no values."]

    # Cheap gate: a sample tells us if a column could be anything but free text.
    # Full-column scans below only run when the sample says they might succeed.
    # (Gating only skips work; every conversion is still verified on ALL values.)
    if _sample_is_plain_text(trimmed):
        return series, report, _whitespace_suggestion(name, values, total), []

    typed = _try_lossless_conversion(series, trimmed, total)
    if typed is not None:
        report["dtype"] = dtype_label(typed.dtype)
        report["converted_from_text"] = True
        return typed, report, [], []

    suggestions, warnings = _suggest_for_text(name, values, trimmed, total)
    return series, report, suggestions, warnings


SAMPLE_SIZE = 2000
_STRUCTURED_SAMPLE_RE = (
    r"^(?:" + FLOAT_RE[1:-1] + r"|\d{4}-\d{2}-\d{2}.*|\d{1,2}[/.-]\d{1,2}[/.-]\d{4}"
    r"|[+-]?\s?[₹$€£]?\s?[\d,]+(?:\.\d+)?\s?%?|true|false|yes|no|y|n|[^\w\s]{1,3})$"
)


def _sample_is_plain_text(trimmed: pl.Series) -> bool:
    """True if fewer than half of a sample look like numbers/dates/booleans/placeholders."""
    sample = trimmed.head(SAMPLE_SIZE)
    structured = sample.str.to_lowercase().str.contains(_STRUCTURED_SAMPLE_RE).sum()
    return structured < 0.5 * len(sample)


def _whitespace_suggestion(name: str, values: pl.Series, total: int) -> list[dict]:
    padded = values.filter(values.str.len_chars() != values.str.strip_chars().str.len_chars())
    if not len(padded):
        return []
    return [
        {
            "id": f"trim_whitespace:{name}",
            "column": name,
            "kind": "trim_whitespace",
            "message": f"{len(padded)} value(s) in '{name}' have extra spaces at the start or end.",
            "evidence": {"affected": len(padded), "total": total, "examples": _examples(padded)},
            "operation": {"type": "trim_whitespace", "params": {"columns": [name]}},
        }
    ]


def _all_match(trimmed: pl.Series, pattern: str) -> bool:
    return bool(trimmed.str.contains(pattern).all())


def _try_lossless_conversion(series: pl.Series, trimmed: pl.Series, total: int) -> pl.Series | None:
    name = series.name
    stripped_full = series.str.strip_chars()
    null_before = series.null_count()

    def unchanged_nulls(candidate: pl.Series) -> bool:
        return candidate.null_count() == null_before

    lowered = set(trimmed.str.to_lowercase().unique().to_list())
    if lowered and lowered <= BOOLEAN_TEXT:
        return (stripped_full.str.to_lowercase() == "true").alias(name)

    if _all_match(trimmed, INT_RE):
        if trimmed.str.contains(LEADING_ZERO_RE).any():
            return None  # IDs / zip codes: keep text (reported in _suggest_for_text)
        if bool((trimmed.str.replace(r"^[+-]", "").str.len_chars() > MAX_INT_DIGITS).any()):
            return None
        candidate = stripped_full.cast(pl.Int64, strict=False)
        return candidate if unchanged_nulls(candidate) else None

    if _all_match(trimmed, FLOAT_RE):
        candidate = stripped_full.cast(pl.Float64, strict=False)
        return candidate if unchanged_nulls(candidate) else None

    if _all_match(trimmed, DATE_ISO_RE):
        candidate = stripped_full.str.to_date("%Y-%m-%d", strict=False)
        return candidate if unchanged_nulls(candidate) else None

    if _all_match(trimmed, DATETIME_ISO_RE):
        for fmt in DATETIME_FORMATS:
            candidate = stripped_full.str.to_datetime(fmt, strict=False)
            if unchanged_nulls(candidate):
                # Excel dates arrive as "2024-01-05 00:00:00": store them as dates.
                if _all_midnight(candidate):
                    return candidate.dt.date().alias(name)
                return candidate
    return None


def text_column_suggestions(series: pl.Series) -> list[dict]:
    """Suggestions for an existing text column (used by health/recommendations after upload)."""
    values = series.drop_nulls()
    if len(values) == 0:
        return []
    trimmed = values.str.strip_chars()
    if _sample_is_plain_text(trimmed):
        return _whitespace_suggestion(series.name, values, len(values))
    suggestions, _ = _suggest_for_text(series.name, values, trimmed, len(values))
    return suggestions


def _all_midnight(candidate: pl.Series) -> bool:
    values = candidate.drop_nulls()
    return bool(
        ((values.dt.hour() == 0) & (values.dt.minute() == 0) & (values.dt.second() == 0) & (values.dt.nanosecond() == 0)).all()
    )


# ---------------------------------------------------------------------------
# Suggestions (never applied automatically)
# ---------------------------------------------------------------------------
def _examples(values: pl.Series, limit: int = EXAMPLES) -> list[dict]:
    counts = values.value_counts(sort=True).head(limit)
    return [{"value": value, "count": int(count)} for value, count in counts.iter_rows()]


def _suggest_for_text(name: str, values: pl.Series, trimmed: pl.Series, total: int) -> tuple[list[dict], list[str]]:
    suggestions: list[dict] = []
    warnings: list[str] = []

    is_int = trimmed.str.contains(INT_RE)
    if bool(is_int.all()):
        if bool(trimmed.str.contains(LEADING_ZERO_RE).any()):
            warnings.append(
                f"Column '{name}' looks like codes or IDs with leading zeros (e.g. "
                f"'{trimmed.filter(trimmed.str.contains(LEADING_ZERO_RE))[0]}'); kept as text so zeros aren't lost."
            )
        else:
            warnings.append(f"Column '{name}' has numbers longer than {MAX_INT_DIGITS} digits; kept as text.")
        return suggestions, warnings

    numeric_suggestion = _suggest_mostly_numeric(name, trimmed, total)
    if numeric_suggestion:
        suggestions.append(numeric_suggestion)
    else:
        for builder in (_suggest_formatted_numbers, _suggest_mostly_iso_dates, _suggest_day_month_dates, _suggest_yes_no):
            suggestion = builder(name, trimmed, total)
            if suggestion:
                suggestions.append(suggestion)
                break

    suggestions.extend(_whitespace_suggestion(name, values, total))
    return suggestions, warnings


def _suggest_mostly_numeric(name: str, trimmed: pl.Series, total: int) -> dict | None:
    is_number = trimmed.str.contains(FLOAT_RE)
    convertible = int(is_number.sum())
    failing = trimmed.filter(~is_number)
    failing_counts = failing.value_counts(sort=True)
    if convertible == 0 or failing_counts.height > MAX_FAILING_DISTINCT:
        return None
    # Symbols like "?", "-", "--", "." are obviously placeholders, so a lower
    # share of real numbers is still convincing. Words ("unknown") need 80%.
    only_placeholders = bool(failing.str.contains(PLACEHOLDER_RE).all())
    required = PLACEHOLDER_NUMERIC_RATIO if only_placeholders else MOSTLY_NUMERIC_RATIO
    if convertible / total < required:
        return None

    numbers = trimmed.filter(is_number)
    if bool(numbers.str.contains(LEADING_ZERO_RE).any()) and bool(numbers.str.contains(INT_RE).all()):
        return None  # mostly IDs with leading zeros; converting would lose them
    target = "integer" if bool(numbers.str.contains(INT_RE).all()) else "float"
    bad_values = [value for value, _ in failing_counts.iter_rows()]
    return {
        "id": f"convert_to_number:{name}",
        "column": name,
        "kind": "convert_to_number",
        "message": (
            f"'{name}' is {convertible / total:.0%} numbers. "
            f"{len(failing)} value(s) like {', '.join(repr(v) for v in bad_values[:3])} prevent it from being numeric. "
            "Convert it, treating those values as missing?"
        ),
        "evidence": {
            "convertible": convertible,
            "failing": len(failing),
            "total": total,
            "failing_values": _examples(failing, MAX_FAILING_DISTINCT),
        },
        "operation": {
            "type": "convert_type",
            "params": {"column": name, "to": target, "treat_as_missing": bad_values},
        },
    }


def _suggest_formatted_numbers(name: str, trimmed: pl.Series, total: int) -> dict | None:
    matches = trimmed.str.contains(FORMATTED_NUMBER_RE)
    if int(matches.sum()) / total < PATTERN_RATIO:
        return None
    has_formatting = trimmed.str.contains(r"[₹$€£,%]")
    if not bool(has_formatting.any()):
        return None
    sample = trimmed.filter(has_formatting).unique(maintain_order=True).head(EXAMPLES)
    cleaned = sample.str.replace_all(r"[₹$€£,%\s]", "")
    is_percent = bool(trimmed.str.ends_with("%").any())
    return {
        "id": f"parse_formatted_number:{name}",
        "column": name,
        "kind": "parse_formatted_number",
        "message": f"'{name}' looks like formatted numbers (currency symbols, thousands separators or %). Strip the formatting and convert to numbers?",
        "evidence": {
            "matching": int(matches.sum()),
            "total": total,
            "examples": [{"before": b, "after": a} for b, a in zip(sample.to_list(), cleaned.to_list())],
        },
        "operation": {
            "type": "parse_formatted_number",
            "params": {"column": name, "is_percent": is_percent},
        },
    }


def _suggest_mostly_iso_dates(name: str, trimmed: pl.Series, total: int) -> dict | None:
    """Mostly YYYY-MM-DD, but some values aren't valid dates (e.g. '2025-15-40'), so ingest kept it as text."""
    looks = trimmed.str.contains(DATE_ISO_RE)
    parsed = trimmed.str.to_date("%Y-%m-%d", strict=False)
    valid = int(parsed.is_not_null().sum())
    if int(looks.sum()) / total < MOSTLY_NUMERIC_RATIO or valid / total < MOSTLY_NUMERIC_RATIO or valid == total:
        return None
    failing = trimmed.filter(parsed.is_null())
    examples = _examples(failing)
    shown = ", ".join(repr(e["value"]) for e in examples[:3])
    return {
        "id": f"convert_to_date:{name}",
        "column": name,
        "kind": "convert_to_date",
        "message": f"'{name}' is {valid / total:.0%} dates (YYYY-MM-DD). {total - valid} value(s) like {shown} aren't valid dates. "
                   "Convert it, treating those values as missing?",
        "evidence": {"convertible": valid, "failing": total - valid, "total": total, "failing_values": examples},
        "operation": {"type": "convert_type", "params": {"column": name, "to": "date", "format": "%Y-%m-%d"}},
    }


def _suggest_day_month_dates(name: str, trimmed: pl.Series, total: int) -> dict | None:
    matches = trimmed.str.contains(DMY_RE)
    if int(matches.sum()) / total < PATTERN_RATIO:
        return None
    parts = trimmed.filter(matches).str.extract_groups(DMY_RE)
    first = parts.struct.field("1").cast(pl.Int32)
    second = parts.struct.field("2").cast(pl.Int32)
    separator = trimmed.filter(matches).str.extract(r"^\d{1,2}([/.-])", 1)[0]
    day_first_fmt = f"%d{separator}%m{separator}%Y"
    month_first_fmt = f"%m{separator}%d{separator}%Y"

    day_first_possible = not bool((second > 12).any())
    month_first_possible = not bool((first > 12).any())
    if day_first_possible and month_first_possible:
        options, verdict = [day_first_fmt, month_first_fmt], "ambiguous: no day is above 12, so either order fits"
    elif day_first_possible:
        options, verdict = [day_first_fmt], "day-first (some first numbers are above 12)"
    elif month_first_possible:
        options, verdict = [month_first_fmt], "month-first (some second numbers are above 12)"
    else:
        return None  # inconsistent; leave it to the user

    return {
        "id": f"convert_to_date:{name}",
        "column": name,
        "kind": "convert_to_date",
        "message": f"'{name}' looks like dates ({verdict}). Convert to a date column?",
        "evidence": {"matching": int(matches.sum()), "total": total, "examples": _examples(trimmed.filter(matches))},
        "operation": {"type": "convert_type", "params": {"column": name, "to": "date", "format_options": options}},
    }


def _suggest_yes_no(name: str, trimmed: pl.Series, total: int) -> dict | None:
    lowered = set(trimmed.str.to_lowercase().unique().to_list())
    for true_set, false_set in YES_NO_PAIRS:
        if lowered and lowered <= (true_set | false_set) and len(lowered) == 2:
            return {
                "id": f"convert_to_boolean:{name}",
                "column": name,
                "kind": "convert_to_boolean",
                "message": f"'{name}' only contains {sorted(lowered)}. Convert to true/false?",
                "evidence": {"total": total, "examples": _examples(trimmed)},
                "operation": {
                    "type": "convert_type",
                    "params": {"column": name, "to": "boolean", "true_values": sorted(true_set), "false_values": sorted(false_set)},
                },
            }
    return None
