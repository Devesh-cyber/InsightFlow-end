"""Profiling, quality, relationships, charts and stats: engine-level tests."""

import datetime
import math

import polars as pl
import pytest

from app.core.errors import UnprocessableError
from app.engine import charts, profile, quality, relationships
from app.engine.stats import chi2_sf


@pytest.fixture
def df():
    n = 60
    return pl.DataFrame({
        "id": list(range(1, n + 1)),
        "age": [20 + (i % 40) for i in range(n)],
        "income": [1000.0 + 50 * (20 + (i % 40)) + (5000.0 if i == 7 else 0.0) for i in range(n)],
        "city": [["Mumbai", "Pune", "Delhi"][i % 3] for i in range(n)],
        "member": [i % 2 == 0 for i in range(n)],
        "joined": [datetime.date(2024, 1, 1) + datetime.timedelta(days=i * 3) for i in range(n)],
        "notes": [f"free text note {i}" for i in range(n)],
        "constant": ["same"] * n,
        "empty": pl.Series([None] * n, dtype=pl.String),
        "score": [None if i % 10 == 0 else float(i) for i in range(n)],
    })


# ------------------------------------------------------------------ stats
@pytest.mark.parametrize(
    "x,k,expected",  # reference values from scipy.stats.chi2.sf
    [(3.841458820694124, 1, 0.05), (10, 5, 0.07523524614651217), (0.5, 2, 0.7788007830714049),
     (100, 50, 3.454931382984871e-05), (250, 4, 6.509689997375721e-53), (30, 30, 0.4656537089440098)],
)
def test_chi2_matches_scipy(x, k, expected):
    assert math.isclose(chi2_sf(x, k), expected, rel_tol=1e-9)


# ---------------------------------------------------------------- profile
def test_roles(df):
    roles = {c["name"]: c["role"] for c in profile.columns_profile(df)}
    assert roles == {
        "id": "identifier", "age": "numeric", "income": "numeric", "city": "categorical", "member": "boolean",
        "joined": "temporal", "notes": "identifier", "constant": "constant", "empty": "empty", "score": "numeric",
    }


def test_overview_counts(df):
    o = profile.overview(df.vstack(df.head(2)))
    assert o["rows"] == 62 and o["duplicate_rows"] == 2
    assert o["missing_cells"] == 62 + 7  # empty column + score gaps
    assert o["preview"]["rows"][0][5] == "2024-01-01"  # JSON-safe date
    assert set(o["dtype_counts"]) >= {"integer", "float", "text", "boolean", "date"}


def test_column_detail_numeric(df):
    d = profile.column_detail(df, "income")
    assert d["numeric"]["outliers"]["count"] == 1
    assert sum(b["count"] for b in d["histogram"]) == 60
    assert d["numeric"]["quantiles"]["p50"] == d["numeric"]["median"]


def test_column_detail_text_temporal_boolean(df):
    assert profile.column_detail(df, "city")["top_values"]["items"][0]["count"] == 20
    t = profile.column_detail(df, "joined")["temporal"]
    assert t["min"] == "2024-01-01" and sum(p["count"] for p in t["timeline"]) == 60
    assert profile.column_detail(df, "member")["boolean"] == {"true": 30, "false": 30}


def test_histogram_constant_and_nan():
    assert profile.histogram(pl.Series([5, 5, 5])) == [{"start": 5.0, "end": 5.0, "count": 3}]
    assert sum(b["count"] for b in profile.histogram(pl.Series([1.0, float("nan"), 2.0, None, float("inf")]))) == 2


# ---------------------------------------------------------------- quality
def test_health_issues_and_score(df):
    data = df.vstack(df.head(3))
    report = quality.health_report(data)
    ids = {i["id"] for i in report["issues"]}
    assert {"duplicates", "empty_column:empty", "constant_column:constant", "missing_values:score", "outliers:income"} <= ids
    assert report["score"] < 100
    assert report["score"] == round(100 - sum(d["points"] for d in report["deductions"]), 1)
    # outliers are reported but never cost points
    assert all("utlier" not in d["reason"] for d in report["deductions"])


def test_missing_value_options_recommend_by_skew():
    skewed = pl.DataFrame({"v": [1.0, 1, 1, 2, 2, 3, 100, 200, None, None] * 3})
    issue = next(i for i in quality.find_issues(skewed) if i["category"] == "missing_values")
    recommended = [o for o in issue["options"] if o["recommended"]]
    assert recommended[0]["operation"] == {"type": "fill_missing", "params": {"columns": ["v"], "strategy": "median"}}


def test_categorical_missing_recommends_mode():
    data = pl.DataFrame({"c": ["a", "b", "a", None] * 10})
    issue = next(i for i in quality.find_issues(data) if i["category"] == "missing_values")
    assert next(o for o in issue["options"] if o["recommended"])["operation"]["params"]["strategy"] == "mode"


def test_text_column_type_issue():
    data = pl.DataFrame({"age": ["25", "31", "?", "40", "28"] * 5})
    issue = next(i for i in quality.find_issues(data) if i["category"] == "type_mismatch")
    assert issue["options"][0]["operation"]["type"] == "convert_type"


def test_clean_data_scores_100():
    assert quality.health_report(pl.DataFrame({"a": [1, 2, 3], "b": ["x", "y", "z"]}))["score"] == 100


# ----------------------------------------------------------- relationships
def test_numeric_numeric(df):
    r = relationships.analyze(df, "age", "income")
    # one outlier drags Pearson down; rank-based Spearman barely notices it
    assert r["type"] == "numeric_numeric" and 0.5 < r["pearson"] < 0.7 and r["spearman"] > 0.9
    assert "Spearman is notably stronger" in r["interpretation"]
    assert len(r["scatter"]["points"]) == 60 and r["fit"]["slope"] > 0


def test_categorical_categorical_has_p_value(df):
    r = relationships.analyze(df, "city", "member")
    assert r["type"] == "categorical_categorical"
    assert 0 <= r["chi_square"]["p_value"] <= 1
    assert sum(map(sum, r["contingency"]["counts"])) == 60


def test_numeric_categorical(df):
    r = relationships.analyze(df, "income", "city")
    assert r["type"] == "numeric_categorical" and len(r["groups"]) == 3 and 0 <= r["eta"] <= 1


def test_temporal_numeric(df):
    r = relationships.analyze(df, "joined", "age")
    assert r["type"] == "temporal_numeric" and r["trend"]


@pytest.mark.parametrize("a,b", [("id", "age"), ("notes", "city"), ("constant", "age"), ("empty", "age"), ("age", "age")])
def test_meaningless_pairs_refused(df, a, b):
    with pytest.raises(UnprocessableError):
        relationships.analyze(df, a, b)


def test_correlation_matrix(df):
    m = relationships.correlation_matrix(df)
    assert m["columns"] == ["age", "income", "score"]
    assert m["matrix"][0][0] == 1.0 and m["matrix"][0][1] == m["matrix"][1][0]
    assert m["top_pairs"][0]["a"] == "age"


# ------------------------------------------------------------------ charts
def test_chart_options(df):
    assert [c["type"] for c in charts.options(df, "income")["charts"]] == ["histogram", "box"]
    assert [c["type"] for c in charts.options(df, "income", "city")["charts"]] == ["box", "bar"]
    assert [c["type"] for c in charts.options(df, "city", "member")["charts"]] == ["heatmap"]
    assert [c["type"] for c in charts.options(df, "joined", "income")["charts"]] == ["line"]
    assert charts.options(df, "notes", "id")["charts"] == []


@pytest.mark.parametrize(
    "chart,x,y,agg",
    [("histogram", "income", None, "count"), ("box", "income", "city", "count"), ("bar", "city", None, "count"),
     ("bar", "city", "income", "mean"), ("line", "joined", "income", "sum"), ("scatter", "age", "income", "count"),
     ("heatmap", "city", "member", "count")],
)
def test_chart_data(df, chart, x, y, agg):
    payload = charts.data(df, chart, x, y, agg)
    assert payload["type"] == chart


def test_bar_groups_long_tail_into_other():
    data = pl.DataFrame({"c": [f"cat{i}" for i in range(40)] * 2})
    bars = charts.data(data, "bar", "c")["bars"]
    assert len(bars) == 21 and bars[-1]["label"].startswith("Other")
    assert sum(b["value"] for b in bars) == 80


def test_wrong_chart_refused(df):
    with pytest.raises(UnprocessableError):
        charts.data(df, "scatter", "city", "member")


def test_small_sample_correlation_is_flagged():
    small = pl.DataFrame({"a": [1.0, 2.0, 3.0, 4.0], "b": [2.0, 4.1, 5.9, 8.2]})
    result = relationships.analyze(small, "a", "b")
    assert result["small_sample"] is True
    assert "only 4 row" in result["warning"]
    matrix = relationships.correlation_matrix(small)
    assert matrix["top_pairs"][0]["n"] == 4 and matrix["top_pairs"][0]["small_sample"] is True


def test_large_sample_correlation_has_no_warning():
    big = pl.DataFrame({"a": [float(i) for i in range(50)], "b": [float(i * 2 + (i % 3)) for i in range(50)]})
    result = relationships.analyze(big, "a", "b")
    assert result["small_sample"] is False and result["warning"] is None
