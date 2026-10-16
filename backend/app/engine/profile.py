"""
Profiling: overview, per-column profiles, single-column detail, row pages.

Everything here is a pure function of a DataFrame. Results are plain dicts
that are already JSON-safe, so they can be cached and returned as-is.
"""

import math

import polars as pl

from app.engine.infer import dtype_label
from app.engine.jsonsafe import rnd, to_json

PREVIEW_ROWS = 10
TOP_VALUES = 20
SAMPLE_VALUES = 10
CATEGORICAL_MAX_UNIQUE = 50
IDENTIFIER_MIN_ROWS = 20
QUANTILES = {"p1": 0.01, "p5": 0.05, "p25": 0.25, "p50": 0.5, "p75": 0.75, "p95": 0.95, "p99": 0.99}


def pct(part: int | float, whole: int | float) -> float:
    return round(100.0 * part / whole, 2) if whole else 0.0


def is_numeric(dtype: pl.DataType) -> bool:
    return dtype.is_numeric() and dtype != pl.Boolean


def is_temporal(dtype: pl.DataType) -> bool:
    return dtype == pl.Date or isinstance(dtype, pl.Datetime)


def column_role(dtype: pl.DataType, rows: int, non_null: int, unique: int) -> str:
    """A plain-language role that helps learners pick the right analysis."""
    if non_null == 0:
        return "empty"
    if unique == 1:
        return "constant"
    if dtype == pl.Boolean:
        return "boolean"
    if is_temporal(dtype):
        return "temporal"
    label = dtype_label(dtype)
    if rows >= IDENTIFIER_MIN_ROWS and unique == non_null and label in ("integer", "text"):
        return "identifier"
    if is_numeric(dtype):
        return "numeric"
    if unique <= CATEGORICAL_MAX_UNIQUE and unique <= 0.5 * non_null:
        return "categorical"
    return "free_text"


def _column_basics(df: pl.DataFrame) -> list[dict]:
    rows = df.height
    if df.width == 0:
        return []
    nulls = df.select(pl.all().null_count()).row(0)
    uniques = df.select(pl.all().drop_nulls().n_unique()).row(0)
    out = []
    for name, dtype, null_count, unique in zip(df.columns, df.dtypes, nulls, uniques):
        non_null = rows - null_count
        out.append(
            {
                "name": name,
                "dtype": dtype_label(dtype),
                "role": column_role(dtype, rows, non_null, unique),
                "null_count": int(null_count),
                "null_pct": pct(null_count, rows),
                "unique_count": int(unique),
                "unique_pct": pct(unique, non_null),
            }
        )
    return out


def rows_payload(df: pl.DataFrame) -> dict:
    return {
        "columns": [{"name": n, "dtype": dtype_label(t)} for n, t in zip(df.columns, df.dtypes)],
        "rows": [to_json(list(row)) for row in df.iter_rows()],
    }


# ---------------------------------------------------------------------------
def overview(df: pl.DataFrame) -> dict:
    rows, cols = df.height, df.width
    basics = _column_basics(df)
    cells = rows * cols
    missing = sum(c["null_count"] for c in basics)
    # Rows that are an exact copy of an earlier row (exactly what "drop duplicates" removes).
    duplicates = rows - df.unique(maintain_order=False).height if rows else 0

    dtype_counts: dict[str, int] = {}
    role_counts: dict[str, int] = {}
    for c in basics:
        dtype_counts[c["dtype"]] = dtype_counts.get(c["dtype"], 0) + 1
        role_counts[c["role"]] = role_counts.get(c["role"], 0) + 1

    return {
        "rows": rows,
        "columns": cols,
        "cells": cells,
        "missing_cells": missing,
        "missing_pct": pct(missing, cells),
        "completeness_pct": round(100.0 - pct(missing, cells), 2),
        "duplicate_rows": duplicates,
        "duplicate_pct": pct(duplicates, rows),
        "memory_bytes": int(df.estimated_size()),
        "dtype_counts": dtype_counts,
        "role_counts": role_counts,
        "preview": rows_payload(df.head(PREVIEW_ROWS)),
    }


def columns_profile(df: pl.DataFrame) -> list[dict]:
    basics = _column_basics(df)
    for info in basics:
        series = df.get_column(info["name"])
        values = series.drop_nulls()
        info["sample_values"] = to_json(values.unique(maintain_order=True).head(3).to_list())
        if is_numeric(series.dtype) and len(values):
            info["min"], info["max"], info["mean"] = rnd(values.min()), rnd(values.max()), rnd(values.mean())
        elif is_temporal(series.dtype) and len(values):
            info["min"], info["max"] = to_json(values.min()), to_json(values.max())
    return basics


# ---------------------------------------------------------------------------
def histogram(values: pl.Series, bins: int | None = None) -> list[dict]:
    """Equal-width bins over finite values. Bin count: Freedman-Diaconis, clamped to 5..50."""
    values = values.drop_nulls().cast(pl.Float64)
    values = values.filter(values.is_finite())
    n = len(values)
    if n == 0:
        return []
    low, high = float(values.min()), float(values.max())
    if low == high:
        return [{"start": rnd(low), "end": rnd(high), "count": n}]
    if bins is None:
        iqr = float(values.quantile(0.75) - values.quantile(0.25))
        width = 2 * iqr / (n ** (1 / 3)) if iqr > 0 else 0
        bins = int(math.ceil((high - low) / width)) if width > 0 else 10
    bins = max(5, min(50, bins))
    width = (high - low) / bins
    index = ((values - low) / width).floor().cast(pl.Int64).clip(0, bins - 1)
    counts = dict(index.value_counts().iter_rows())
    return [
        {"start": rnd(low + i * width), "end": rnd(low + (i + 1) * width), "count": int(counts.get(i, 0))}
        for i in range(bins)
    ]


def iqr_bounds(values: pl.Series, factor: float = 1.5) -> tuple[float, float, float, float] | None:
    values = values.drop_nulls().cast(pl.Float64)
    if len(values) < 4:
        return None
    q1, q3 = float(values.quantile(0.25, interpolation="linear")), float(values.quantile(0.75, interpolation="linear"))
    iqr = q3 - q1
    return q1, q3, q1 - factor * iqr, q3 + factor * iqr


def top_values(values: pl.Series, total: int, limit: int = TOP_VALUES) -> dict:
    counts = values.value_counts(sort=True, name="count")
    shown = counts.head(limit)
    items = [{"value": to_json(v), "count": int(c), "pct": pct(c, total)} for v, c in shown.iter_rows()]
    other = int(counts["count"].sum() - shown["count"].sum()) if counts.height > limit else 0
    return {"items": items, "other_count": other, "distinct": counts.height}


def _time_period(span_days: float) -> str:
    if span_days <= 62:
        return "1d"
    if span_days <= 730:
        return "1w" if span_days <= 180 else "1mo"
    if span_days <= 3650:
        return "1mo"
    return "1y"


def timeline(values: pl.Series, period: str | None = None) -> tuple[str, list[dict]]:
    values = values.drop_nulls()
    if len(values) == 0:
        return "1d", []
    span = (values.max() - values.min())
    span_days = span.total_seconds() / 86400 if hasattr(span, "total_seconds") else float(span)
    period = period or _time_period(span_days)
    frame = pl.DataFrame({"t": values}).group_by(pl.col("t").dt.truncate(period).alias("period")).len().sort("period")
    return period, [{"period": to_json(p), "count": int(c)} for p, c in frame.iter_rows()]


def column_detail(df: pl.DataFrame, name: str) -> dict:
    series = df.get_column(name)
    rows = df.height
    values = series.drop_nulls()
    non_null = len(values)
    unique = int(values.n_unique())
    detail = {
        "name": name,
        "dtype": dtype_label(series.dtype),
        "role": column_role(series.dtype, rows, non_null, unique),
        "rows": rows,
        "null_count": rows - non_null,
        "null_pct": pct(rows - non_null, rows),
        "unique_count": unique,
        "unique_pct": pct(unique, non_null),
        "sample_values": to_json(values.unique(maintain_order=True).head(SAMPLE_VALUES).to_list()),
    }
    if non_null == 0:
        return detail

    dtype = series.dtype
    if is_numeric(dtype):
        floats = values.cast(pl.Float64)
        stats = {
            "min": rnd(values.min()),
            "max": rnd(values.max()),
            "mean": rnd(floats.mean()),
            "median": rnd(floats.median()),
            "std": rnd(floats.std()) if non_null > 1 else None,
            "skew": rnd(floats.skew(), 4) if non_null > 2 else None,
            "kurtosis": rnd(floats.kurtosis(), 4) if non_null > 3 else None,
            "sum": rnd(floats.sum()),
            "zeros": int((values == 0).sum()),
            "negatives": int((values < 0).sum()),
            "quantiles": {k: rnd(floats.quantile(q, interpolation="linear")) for k, q in QUANTILES.items()},
        }
        bounds = iqr_bounds(values)
        if bounds:
            q1, q3, lower, upper = bounds
            outliers = int(((floats < lower) | (floats > upper)).sum())
            stats["outliers"] = {
                "method": "iqr",
                "factor": 1.5,
                "lower_bound": rnd(lower),
                "upper_bound": rnd(upper),
                "count": outliers,
                "pct": pct(outliers, non_null),
            }
        detail["numeric"] = stats
        detail["histogram"] = histogram(values)
        if unique <= TOP_VALUES:
            detail["top_values"] = top_values(values, rows)
    elif is_temporal(dtype):
        period, points = timeline(values)
        detail["temporal"] = {
            "min": to_json(values.min()),
            "max": to_json(values.max()),
            "span_days": rnd((values.max() - values.min()).total_seconds() / 86400, 2),
            "period": period,
            "timeline": points,
            "weekday_counts": {
                day: int(count)
                for day, count in pl.DataFrame({"d": values.dt.weekday()})
                .group_by("d").len().sort("d").iter_rows()
            },
        }
    elif dtype == pl.Boolean:
        detail["boolean"] = {"true": int(values.sum()), "false": int(non_null - values.sum())}
        detail["top_values"] = top_values(values, rows)
    else:
        lengths = values.cast(pl.String).str.len_chars()
        detail["text"] = {
            "min_length": int(lengths.min()),
            "mean_length": rnd(lengths.mean(), 2),
            "max_length": int(lengths.max()),
            "padded_with_spaces": int((values.str.len_chars() != values.str.strip_chars().str.len_chars()).sum()),
        }
        detail["top_values"] = top_values(values, rows)
    return detail
