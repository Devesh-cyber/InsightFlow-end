"""
Chart DATA (not images). The frontend draws; the backend aggregates and caps
sizes so a chart of a million rows is still a few kilobytes.

options(x, y)  -> which chart types make sense for those columns, best first
data(type, ...) -> compact payload for that chart type
"""

import polars as pl

from app.core.errors import UnprocessableError
from app.engine.jsonsafe import rnd, to_json
from app.engine.profile import column_role, histogram, is_numeric, is_temporal, timeline

MAX_BARS = 20
MAX_POINTS = 5000
MAX_BOX_GROUPS = 15
MAX_OUTLIERS_PER_BOX = 100
HEATMAP_MAX = 15
AGGREGATIONS = ("count", "sum", "mean", "median", "min", "max")

CHARTS = {
    "histogram": "Distribution of a numeric column",
    "box": "Spread, median and outliers",
    "bar": "Counts (or an aggregate) per category",
    "line": "A value over time",
    "scatter": "Two numeric columns against each other",
    "heatmap": "How two categories co-occur",
}


def _kind(df: pl.DataFrame, name: str | None) -> str | None:
    if name is None:
        return None
    if name not in df.columns:
        raise UnprocessableError(f"Column '{name}' does not exist.", code="column_not_found")
    series = df.get_column(name)
    non_null = series.len() - series.null_count()
    role = column_role(series.dtype, df.height, non_null, series.drop_nulls().n_unique())
    if role == "empty":
        return "empty"
    if is_temporal(series.dtype):
        return "temporal"
    if is_numeric(series.dtype) and role not in ("identifier", "constant"):
        return "numeric"
    if role in ("categorical", "boolean", "constant"):
        return "categorical"
    return "other"  # identifiers / free text


def options(df: pl.DataFrame, x: str, y: str | None = None) -> dict:
    kx, ky = _kind(df, x), _kind(df, y)
    if ky is None:
        available = {
            "numeric": ["histogram", "box"],
            "categorical": ["bar"],
            "temporal": ["line"],
            "other": ["bar"],
        }.get(kx, [])
    else:
        pair = {kx, ky}
        if pair == {"numeric"}:
            available = ["scatter"]
        elif pair == {"numeric", "categorical"}:
            available = ["box", "bar"]
        elif pair == {"categorical"}:
            available = ["heatmap"]
        elif pair == {"temporal", "numeric"}:
            available = ["line"]
        else:
            available = []
    reason = None if available else "No standard chart fits these column types."
    return {
        "x": x, "y": y, "x_kind": kx, "y_kind": ky,
        "charts": [{"type": t, "description": CHARTS[t], "recommended": i == 0} for i, t in enumerate(available)],
        "reason": reason,
    }


def data(df: pl.DataFrame, chart: str, x: str, y: str | None = None, agg: str = "count", bins: int | None = None) -> dict:
    allowed = [c["type"] for c in options(df, x, y)["charts"]]
    if chart not in allowed:
        raise UnprocessableError(
            f"A {chart} chart doesn't fit these columns.", code="unsupported_chart", details={"allowed": allowed}
        )
    if agg not in AGGREGATIONS:
        raise UnprocessableError(f"agg must be one of {', '.join(AGGREGATIONS)}.", code="invalid_parameter")

    builder = {"histogram": _histogram, "box": _box, "bar": _bar, "line": _line, "scatter": _scatter, "heatmap": _heatmap}[chart]
    payload = builder(df, x, y, agg, bins)
    return {"type": chart, "x": x, "y": y, **payload}


def _agg_expr(column: str | None, agg: str) -> pl.Expr:
    if agg == "count" or column is None:
        return pl.len().alias("value")
    return getattr(pl.col(column).cast(pl.Float64), agg)().alias("value")


def _histogram(df, x, y, agg, bins):
    return {"bins": histogram(df.get_column(x), bins)}


def _box_stats(values: pl.Series) -> dict | None:
    values = values.drop_nulls().cast(pl.Float64)
    if len(values) == 0:
        return None
    q1, median, q3 = (float(values.quantile(q, interpolation="linear")) for q in (0.25, 0.5, 0.75))
    iqr = q3 - q1
    low_fence, high_fence = q1 - 1.5 * iqr, q3 + 1.5 * iqr
    inside = values.filter((values >= low_fence) & (values <= high_fence))
    outliers = values.filter((values < low_fence) | (values > high_fence))
    return {
        "n": len(values),
        "min": rnd(values.min()),
        "q1": rnd(q1),
        "median": rnd(median),
        "q3": rnd(q3),
        "max": rnd(values.max()),
        "whisker_low": rnd(inside.min() if len(inside) else q1),
        "whisker_high": rnd(inside.max() if len(inside) else q3),
        "outlier_count": len(outliers),
        "outliers": [rnd(v) for v in outliers.sort().head(MAX_OUTLIERS_PER_BOX).to_list()],
    }


def _box(df, x, y, agg, bins):
    if y is None:
        return {"groups": [{"label": x, **(_box_stats(df.get_column(x)) or {})}]}
    numeric, category = (x, y) if _kind(df, x) == "numeric" else (y, x)
    frame = df.select(numeric, pl.col(category).cast(pl.String)).drop_nulls()
    top = frame[category].value_counts(sort=True).head(MAX_BOX_GROUPS)[category].to_list()
    groups = []
    for label in top:
        stats = _box_stats(frame.filter(pl.col(category) == label)[numeric])
        if stats:
            groups.append({"label": label, **stats})
    return {"value_column": numeric, "group_column": category, "groups": groups,
            "truncated": frame[category].n_unique() > MAX_BOX_GROUPS}


def _bar(df, x, y, agg, bins):
    if y is None:
        category, value_col, agg = x, None, "count"
    else:
        category, value_col = (y, x) if _kind(df, x) == "numeric" else (x, y)
    frame = df.select([category] + ([value_col] if value_col else []))
    frame = frame.with_columns(pl.col(category).cast(pl.String).fill_null("(missing)"))
    grouped = frame.group_by(category).agg(_agg_expr(value_col, agg), pl.len().alias("rows")).sort(
        "value" if agg != "count" else "rows", descending=True
    )
    shown = grouped.head(MAX_BARS)
    other = grouped.slice(MAX_BARS)
    bars = [{"label": label, "value": rnd(value), "rows": int(rows)} for label, value, rows in shown.iter_rows()]
    if other.height and agg in ("count", "sum"):
        bars.append({"label": f"Other ({other.height} categories)", "value": rnd(other["value"].sum()), "rows": int(other["rows"].sum())})
    return {"category_column": category, "value_column": value_col, "agg": agg, "bars": bars, "categories_total": grouped.height}


def _line(df, x, y, agg, bins):
    time, value_col = (x, y) if _kind(df, x) == "temporal" else (y, x)
    frame = df.select([time] + ([value_col] if value_col else [])).drop_nulls()
    period, _ = timeline(frame[time])
    effective = agg if value_col else "count"
    if value_col and agg == "count":
        effective = "mean"  # a count over time of a numeric column is rarely what's wanted
    series = (
        frame.group_by(pl.col(time).dt.truncate(period).alias("period"))
        .agg(_agg_expr(value_col, effective))
        .sort("period")
    )
    return {"time_column": time, "value_column": value_col, "agg": effective, "period": period,
            "points": [{"t": to_json(t), "value": rnd(v)} for t, v in series.iter_rows()]}


def _scatter(df, x, y, agg, bins):
    frame = df.select(pl.col(x).cast(pl.Float64), pl.col(y).cast(pl.Float64)).drop_nulls()
    frame = frame.filter(pl.col(x).is_finite() & pl.col(y).is_finite())
    total = frame.height
    if total > MAX_POINTS:
        frame = frame.sample(MAX_POINTS, seed=0)
    return {"points": [[rnd(a), rnd(b)] for a, b in frame.iter_rows()], "total_points": total, "sampled": total > MAX_POINTS}


def _heatmap(df, x, y, agg, bins):
    frame = df.select(pl.col(x).cast(pl.String), pl.col(y).cast(pl.String)).drop_nulls()
    xs = frame[x].value_counts(sort=True).head(HEATMAP_MAX)[x].to_list()
    ys = frame[y].value_counts(sort=True).head(HEATMAP_MAX)[y].to_list()
    counts = frame.filter(pl.col(x).is_in(xs) & pl.col(y).is_in(ys)).group_by(x, y).len()
    lookup = {(a, b): n for a, b, n in counts.iter_rows()}
    return {"x_labels": xs, "y_labels": ys, "counts": [[int(lookup.get((a, b), 0)) for a in xs] for b in ys],
            "truncated": frame[x].n_unique() > HEATMAP_MAX or frame[y].n_unique() > HEATMAP_MAX}
