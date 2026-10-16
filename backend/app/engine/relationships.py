"""
Relationships between two columns, chosen by column types, explained in words.

numeric  x numeric     -> Pearson r (linear) + Spearman rho (monotonic), fit line, scatter sample
category x category    -> Cramér's V + chi-square test (p-value), contingency table
numeric  x category    -> correlation ratio eta (+ eta²), per-group statistics
time     x numeric     -> trend over time (aggregated), Spearman vs time order

Booleans count as categories. Identifiers and free text are refused with a
reason, because any "relationship" with them would be meaningless.
"""

import math

import polars as pl

from app.core.errors import UnprocessableError
from app.engine.jsonsafe import rnd, to_json
from app.engine.profile import column_role, is_numeric, is_temporal, timeline
from app.engine.stats import association_strength, chi2_sf, correlation_strength

MAX_CATEGORIES = 30  # categories beyond this are grouped into "Other"
SCATTER_POINTS = 2000
MIN_PAIRS = 3
CORRELATION_MAX_COLUMNS = 30
SMALL_SAMPLE = 30  # below this many paired rows, a correlation is too unstable to trust


def sample_note(n: int) -> str | None:
    if n >= SMALL_SAMPLE:
        return None
    return (
        f"Based on only {n} row(s) with both values. With this few points the number swings a lot "
        "from chance alone, so treat it as a hint, not evidence."
    )


def _kind(df: pl.DataFrame, name: str) -> str:
    series = df.get_column(name)
    non_null = series.len() - series.null_count()
    role = column_role(series.dtype, df.height, non_null, series.drop_nulls().n_unique())
    if role in ("empty", "constant"):
        raise UnprocessableError(f"'{name}' is {role}, so it can't relate to anything.", code="unsupported_relationship")
    if is_numeric(series.dtype) and role != "identifier":
        return "numeric"
    if is_temporal(series.dtype):
        return "temporal"
    if role in ("categorical", "boolean"):
        return "categorical"
    raise UnprocessableError(
        f"'{name}' looks like {'an identifier' if role == 'identifier' else 'free text'} "
        "(almost every value is different), so relationships with it aren't meaningful.",
        code="unsupported_relationship",
    )


def _limit_categories(series: pl.Series, limit: int = MAX_CATEGORIES) -> pl.Series:
    as_text = series.cast(pl.String)
    top = set(as_text.drop_nulls().value_counts(sort=True).head(limit)[as_text.name].to_list())
    return pl.select(
        pl.when(as_text.is_null()).then(None).when(as_text.is_in(list(top))).then(as_text).otherwise(pl.lit("Other")).alias(series.name)
    ).to_series()


def analyze(df: pl.DataFrame, a: str, b: str) -> dict:
    for name in (a, b):
        if name not in df.columns:
            raise UnprocessableError(f"Column '{name}' does not exist.", code="column_not_found")
    if a == b:
        raise UnprocessableError("Pick two different columns.", code="invalid_parameter")

    kinds = {a: _kind(df, a), b: _kind(df, b)}
    pair = sorted(kinds.values())
    if pair == ["numeric", "numeric"]:
        return _numeric_numeric(df, a, b)
    if pair == ["categorical", "categorical"]:
        return _categorical_categorical(df, a, b)
    if pair == ["categorical", "numeric"]:
        numeric, category = (a, b) if kinds[a] == "numeric" else (b, a)
        return _numeric_categorical(df, numeric, category)
    if pair == ["numeric", "temporal"]:
        numeric, time = (a, b) if kinds[a] == "numeric" else (b, a)
        return _temporal_numeric(df, time, numeric)
    raise UnprocessableError(
        f"There's no standard relationship measure for {kinds[a]} vs {kinds[b]} columns.", code="unsupported_relationship"
    )


def _pairs(df: pl.DataFrame, a: str, b: str) -> pl.DataFrame:
    pairs = df.select(a, b).drop_nulls()
    if pairs.height < MIN_PAIRS:
        raise UnprocessableError("Too few rows have both values to measure a relationship.", code="not_enough_data")
    return pairs


def _numeric_numeric(df: pl.DataFrame, a: str, b: str) -> dict:
    pairs = _pairs(df, a, b).with_columns(pl.col(a).cast(pl.Float64), pl.col(b).cast(pl.Float64))
    pairs = pairs.filter(pl.col(a).is_finite() & pl.col(b).is_finite())
    if pairs.height < MIN_PAIRS or pairs[a].n_unique() < 2 or pairs[b].n_unique() < 2:
        raise UnprocessableError("One of the columns doesn't vary enough to measure a relationship.", code="not_enough_data")

    stats = pairs.select(
        pl.corr(a, b).alias("pearson"),
        pl.corr(a, b, method="spearman").alias("spearman"),
        pl.cov(a, b).alias("cov"),
        pl.col(a).var().alias("var_a"),
        pl.col(a).mean().alias("mean_a"),
        pl.col(b).mean().alias("mean_b"),
    ).row(0, named=True)
    pearson, spearman = stats["pearson"], stats["spearman"]
    slope = stats["cov"] / stats["var_a"] if stats["var_a"] else None
    intercept = stats["mean_b"] - slope * stats["mean_a"] if slope is not None else None

    sample = pairs if pairs.height <= SCATTER_POINTS else pairs.sample(SCATTER_POINTS, seed=0)
    direction = "positive" if pearson > 0 else "negative"
    gap_note = (
        " Spearman is notably stronger than Pearson, which suggests a monotonic but non-linear pattern (or outliers)."
        if abs(spearman) - abs(pearson) > 0.15 else ""
    )
    return {
        "type": "numeric_numeric",
        "columns": [a, b],
        "n": pairs.height,
        "small_sample": pairs.height < SMALL_SAMPLE,
        "warning": sample_note(pairs.height),
        "pearson": rnd(pearson, 4),
        "spearman": rnd(spearman, 4),
        "r_squared": rnd(pearson**2, 4),
        "strength": correlation_strength(pearson),
        "direction": direction,
        "fit": {"slope": rnd(slope), "intercept": rnd(intercept)} if slope is not None else None,
        "scatter": {"points": [[rnd(x), rnd(y)] for x, y in sample.iter_rows()], "sampled": pairs.height > SCATTER_POINTS},
        "interpretation": (
            (
                f"A {correlation_strength(pearson)} {direction} linear relationship (r = {pearson:.2f}): "
                f"{pearson**2:.0%} of the variation in '{b}' is linearly associated with '{a}'.{gap_note} "
                "Correlation does not imply causation."
            )
            if abs(pearson) >= 0.2
            else f"No meaningful linear relationship (r = {pearson:.2f}).{gap_note}"
        ),
    }


def _categorical_categorical(df: pl.DataFrame, a: str, b: str) -> dict:
    pairs = _pairs(df, a, b)
    pairs = pl.DataFrame([_limit_categories(pairs[a]), _limit_categories(pairs[b])])
    table = pairs.group_by(a, b).len()
    rows_labels = table.group_by(a).agg(pl.col("len").sum()).sort("len", descending=True)[a].to_list()
    col_labels = table.group_by(b).agg(pl.col("len").sum()).sort("len", descending=True)[b].to_list()
    lookup = {(x, y): n for x, y, n in table.iter_rows()}
    matrix = [[int(lookup.get((x, y), 0)) for y in col_labels] for x in rows_labels]

    n = pairs.height
    row_totals = [sum(r) for r in matrix]
    col_totals = [sum(c) for c in zip(*matrix)]
    chi2 = 0.0
    low_expected = 0
    for i, row in enumerate(matrix):
        for j, observed in enumerate(row):
            expected = row_totals[i] * col_totals[j] / n
            if expected < 5:
                low_expected += 1
            chi2 += (observed - expected) ** 2 / expected
    r, k = len(rows_labels), len(col_labels)
    dof = (r - 1) * (k - 1)
    cramers_v = math.sqrt(chi2 / (n * min(r - 1, k - 1))) if min(r, k) > 1 else 0.0
    p_value = chi2_sf(chi2, dof) if dof > 0 else None

    warnings = []
    if low_expected and low_expected / (r * k) > 0.2:
        warnings.append("More than 20% of cells have an expected count below 5, so the p-value is unreliable.")
    significant = p_value is not None and p_value < 0.05
    return {
        "type": "categorical_categorical",
        "columns": [a, b],
        "n": n,
        "cramers_v": rnd(cramers_v, 4),
        "strength": association_strength(cramers_v),
        "chi_square": {"statistic": rnd(chi2, 4), "dof": dof, "p_value": rnd(p_value, 6)},
        "contingency": {"row_labels": to_json(rows_labels), "column_labels": to_json(col_labels), "counts": matrix},
        "warnings": warnings,
        "interpretation": (
            (f"A {association_strength(cramers_v)} association (Cramér's V = {cramers_v:.2f}). "
             if cramers_v >= 0.1 else f"No meaningful association (Cramér's V = {cramers_v:.2f}). ")
            + (f"The chi-square test says it's unlikely to be chance (p = {p_value:.3g})." if significant
               else "The chi-square test can't rule out chance (p ≥ 0.05)." if p_value is not None else "")
        ),
    }


def _numeric_categorical(df: pl.DataFrame, numeric: str, category: str) -> dict:
    pairs = _pairs(df, numeric, category).with_columns(pl.col(numeric).cast(pl.Float64))
    pairs = pairs.with_columns(_limit_categories(pairs[category]))
    groups = (
        pairs.group_by(category)
        .agg(
            pl.len().alias("count"),
            pl.col(numeric).mean().alias("mean"),
            pl.col(numeric).median().alias("median"),
            pl.col(numeric).std().alias("std"),
            pl.col(numeric).min().alias("min"),
            pl.col(numeric).max().alias("max"),
            pl.col(numeric).quantile(0.25).alias("q1"),
            pl.col(numeric).quantile(0.75).alias("q3"),
        )
        .sort("count", descending=True)
    )
    grand_mean = pairs[numeric].mean()
    total_ss = float(((pairs[numeric] - grand_mean) ** 2).sum())
    between_ss = float(sum(c * (m - grand_mean) ** 2 for c, m in zip(groups["count"], groups["mean"])))
    eta_sq = between_ss / total_ss if total_ss > 0 else 0.0
    eta = math.sqrt(eta_sq)
    return {
        "type": "numeric_categorical",
        "columns": [numeric, category],
        "numeric_column": numeric,
        "category_column": category,
        "n": pairs.height,
        "eta": rnd(eta, 4),
        "eta_squared": rnd(eta_sq, 4),
        "strength": association_strength(eta),
        "groups": [{k: rnd(v) for k, v in row.items()} for row in groups.iter_rows(named=True)],
        "interpretation": (
            f"'{category}' explains about {eta_sq:.0%} of the variation in '{numeric}' "
            + (f"(η = {eta:.2f}, a {association_strength(eta)} association)." if eta >= 0.1
               else f"(η = {eta:.2f}): no meaningful association.")
        ),
    }


def _temporal_numeric(df: pl.DataFrame, time: str, numeric: str) -> dict:
    pairs = _pairs(df, time, numeric)
    period, _ = timeline(pairs[time])
    trend = (
        pairs.group_by(pl.col(time).dt.truncate(period).alias("period"))
        .agg(pl.col(numeric).mean().alias("mean"), pl.col(numeric).sum().alias("sum"), pl.len().alias("count"))
        .sort("period")
    )
    ranked = pairs.select(pl.col(time).rank().alias("t"), pl.col(numeric).cast(pl.Float64).alias("v"))
    rho = ranked.select(pl.corr("t", "v", method="spearman")).item() if ranked["v"].n_unique() > 1 else 0.0
    trend_word = "upward" if rho > 0.2 else "downward" if rho < -0.2 else "no clear"
    return {
        "type": "temporal_numeric",
        "columns": [time, numeric],
        "n": pairs.height,
        "period": period,
        "spearman_vs_time": rnd(rho, 4),
        "trend": [{"period": to_json(p), "mean": rnd(m), "sum": rnd(s), "count": int(c)} for p, m, s, c in trend.iter_rows()],
        "interpretation": f"'{numeric}' shows {trend_word} trend over time (Spearman ρ vs time = {rho:.2f}).",
    }


def correlation_matrix(df: pl.DataFrame, method: str = "pearson") -> dict:
    if method not in ("pearson", "spearman"):
        raise UnprocessableError("method must be 'pearson' or 'spearman'.", code="invalid_parameter")
    candidates = []
    for name, dtype in zip(df.columns, df.dtypes):
        if not is_numeric(dtype):
            continue
        series = df.get_column(name)
        non_null = series.len() - series.null_count()
        if column_role(dtype, df.height, non_null, series.drop_nulls().n_unique()) == "numeric":
            candidates.append(name)
    truncated = len(candidates) > CORRELATION_MAX_COLUMNS
    columns = candidates[:CORRELATION_MAX_COLUMNS]
    if len(columns) < 2:
        return {"method": method, "columns": columns, "matrix": [], "top_pairs": [], "truncated": False,
                "note": "Need at least two numeric columns."}

    frame = df.select([pl.col(c).cast(pl.Float64) for c in columns])
    exprs = [
        pl.corr(columns[i], columns[j], method=method).alias(f"{i}|{j}")
        for i in range(len(columns)) for j in range(i + 1, len(columns))
    ]
    values = frame.select(exprs).row(0, named=True)
    counts = frame.select([
        (pl.col(columns[i]).is_not_null() & pl.col(columns[j]).is_not_null()).sum().alias(f"{i}|{j}")
        for i in range(len(columns)) for j in range(i + 1, len(columns))
    ]).row(0, named=True)
    matrix = [[1.0 if i == j else None for j in range(len(columns))] for i in range(len(columns))]
    pairs = []
    for key, value in values.items():
        i, j = map(int, key.split("|"))
        value = rnd(value, 4)
        matrix[i][j] = matrix[j][i] = value
        if value is not None:
            n = int(counts[key])
            pairs.append({"a": columns[i], "b": columns[j], "r": value, "strength": correlation_strength(value),
                          "n": n, "small_sample": n < SMALL_SAMPLE})
    pairs.sort(key=lambda p: abs(p["r"]), reverse=True)
    return {"method": method, "columns": columns, "matrix": matrix, "top_pairs": pairs[:10], "truncated": truncated}
