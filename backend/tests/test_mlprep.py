"""
ML-ready dataset engine: the user's per-column rules.

The numbers are checked against scikit-learn (installed for tests only), so a user who
later rebuilds the same plan with scikit-learn gets the same values.
"""

import random
from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest
from sklearn.impute import SimpleImputer
from sklearn.preprocessing import KBinsDiscretizer, MinMaxScaler, OrdinalEncoder, RobustScaler, StandardScaler

from app.core.errors import UnprocessableError
from app.engine.mlprep import MLPlan, run_plan, split_mask, suggest


def data(n=200, seed=1):
    rnd = random.Random(seed)
    return pl.DataFrame({
        "id": [f"R{i:04d}" for i in range(n)],
        "age": [None if i % 17 == 0 else float(rnd.randint(18, 80)) for i in range(n)],
        "income": [round(rnd.lognormvariate(10, 0.8), 2) for _ in range(n)],
        "city": [rnd.choice(["Mumbai", "Pune", "Delhi", "Goa"]) if i % 23 else None for i in range(n)],
        "member": [rnd.random() < 0.3 for _ in range(n)],
        "joined": [date(2023, 1, 1) + timedelta(days=rnd.randint(0, 700)) for _ in range(n)],
        "units": [rnd.randint(1, 9) for _ in range(n)],
        "price": [round(rnd.uniform(10, 500), 2) for _ in range(n)],
        "churn": [rnd.choice(["yes", "no", "no"]) for _ in range(n)],
    })


def plan(**columns):
    return MLPlan.model_validate({"columns": columns})


def col(result, name):
    return np.array(result["df"].get_column(name).to_list(), dtype=float)


# ------------------------------------------------------------------ matches scikit-learn
@pytest.mark.parametrize("method,scaler", [("standard", StandardScaler()), ("minmax", MinMaxScaler()), ("robust", RobustScaler())])
def test_scaling_matches_sklearn(method, scaler):
    df = data()
    r = run_plan(df, plan(price={"steps": [{"kind": "scale", "method": method}]}))
    expected = scaler.fit_transform(df.select("price").to_numpy()).ravel()
    assert np.allclose(col(r, "price"), expected)


@pytest.mark.parametrize("strategy", ["mean", "median"])
def test_fill_matches_simple_imputer(strategy):
    df = data()
    r = run_plan(df, plan(age={"steps": [{"kind": "fill_missing", "strategy": strategy, "add_indicator": True}]}))
    expected = SimpleImputer(strategy=strategy).fit_transform(np.array(df["age"].to_list(), dtype=float).reshape(-1, 1)).ravel()
    assert np.allclose(col(r, "age"), expected)
    assert r["df"]["age_was_missing"].sum() == df["age"].null_count()
    assert r["df"].columns.index("age_was_missing") == r["df"].columns.index("age") + 1


def test_mode_fill_matches_most_frequent():
    df = data()
    r = run_plan(df, plan(city={"steps": [{"kind": "fill_missing", "strategy": "mode"}]}))
    imputer = SimpleImputer(strategy="most_frequent", missing_values=None)
    expected = imputer.fit_transform(np.array(df["city"].to_list(), dtype=object).reshape(-1, 1)).ravel()
    assert r["df"]["city"].to_list() == list(expected)


def test_onehot_and_ordinal_match_sklearn():
    df = data().filter(pl.col("city").is_not_null())
    r = run_plan(df, plan(city={"steps": [{"kind": "encode", "method": "onehot"}]}))
    for cat in ["Delhi", "Goa", "Mumbai", "Pune"]:
        assert r["df"][f"city_{cat}"].to_list() == [int(v == cat) for v in df["city"].to_list()]
    assert "city" not in r["df"].columns
    r2 = run_plan(df, plan(city={"steps": [{"kind": "encode", "method": "ordinal"}]}))
    expected = OrdinalEncoder().fit_transform(df.select("city").to_numpy()).ravel()
    assert np.allclose(col(r2, "city"), expected)


def test_quantile_bins_match_kbins():
    df = data()
    r = run_plan(df, plan(income={"steps": [{"kind": "bin", "method": "quantile", "bins": 4}]}))
    kb = KBinsDiscretizer(n_bins=4, encode="ordinal", strategy="quantile", quantile_method="linear")
    expected = kb.fit_transform(df.select("income").to_numpy()).ravel()
    assert np.allclose(col(r, "income"), expected)


def test_percentile_clip_matches_numpy():
    df = data()
    r = run_plan(df, plan(income={"steps": [{"kind": "clip", "method": "percentile", "lower": 5, "upper": 95}]}))
    x = df["income"].to_numpy()
    assert np.allclose(col(r, "income"), np.clip(x, np.percentile(x, 5), np.percentile(x, 95)))


# ------------------------------------------------------------------ split: learn from train only
def test_split_learns_from_train_rows_only():
    df = data(300)
    p = MLPlan.model_validate({"columns": {"price": {"steps": [{"kind": "scale", "method": "standard"}]},
                                           "churn": {"role": "target"}},
                               "split": {"enabled": True, "test_size": 0.25, "seed": 7}})
    r = run_plan(df, p)
    train = r["is_train"].to_numpy()
    scaler = StandardScaler().fit(df.select("price").to_numpy()[train])
    assert np.allclose(col(r, "price"), scaler.transform(df.select("price").to_numpy()).ravel())
    assert r["df"].columns[-1] == "churn"  # target last
    assert 0.2 < (~train).mean() < 0.3 and r["split"] == "stratified by 'churn'"
    shares = [df.filter(pl.Series(m))["churn"].eq("yes").mean() for m in (train, ~train)]
    assert abs(shares[0] - shares[1]) < 0.05


def test_split_is_reproducible():
    df = data()
    p = MLPlan.model_validate({"split": {"enabled": True, "seed": 3}})
    a, _ = split_mask(df, p.split, None)
    b, _ = split_mask(df, p.split, None)
    c, _ = split_mask(df, MLPlan.model_validate({"split": {"enabled": True, "seed": 4}}).split, None)
    assert a.to_list() == b.to_list() and a.to_list() != c.to_list()


def test_unseen_category_in_test_rows_gets_zeros():
    df = pl.DataFrame({"c": ["a"] * 10 + ["b"] * 10 + ["z"], "y": list(range(21))})
    mask = pl.Series([True] * 20 + [False])
    from app.engine.mlprep import Encode, _apply_step
    outs, info = _apply_step(df["c"], df["c"].filter(mask), "c", Encode(kind="encode", method="onehot"), [])
    assert info["categories"] == ["a", "b"] and [o.name for o in outs] == ["c_a", "c_b", "c_other"]
    assert [o[20] for o in outs] == [0, 0, 1]


# ------------------------------------------------------------------ roles, features, chaining, dates
def test_roles_features_dates_and_order():
    df = data()
    p = MLPlan.model_validate({
        "columns": {"id": {"role": "drop"}, "joined": {"steps": [{"kind": "date_parts", "parts": ["year", "month", "weekday", "is_weekend"]}]},
                    "member": {"steps": [{"kind": "to_number"}]}, "churn": {"role": "target", "steps": [{"kind": "encode", "method": "ordinal", "order": ["no", "yes"]}]},
                    "revenue": {"steps": [{"kind": "transform", "method": "log1p"}]}},
        "features": [{"name": "revenue", "formula": [{"column": "units"}, "*", {"column": "price"}]}]})
    r = run_plan(df, p)
    cols = r["df"].columns
    assert "id" not in cols and "joined" not in cols and cols[-1] == "churn"
    assert {"joined_year", "joined_month", "joined_weekday", "joined_is_weekend"} <= set(cols)
    assert r["df"]["member"].dtype == pl.Int8 and set(r["df"]["churn"].to_list()) == {0, 1}
    expected = np.log1p(df["units"].to_numpy() * df["price"].to_numpy())
    assert np.allclose(col(r, "revenue"), expected)
    first = df["joined"][0]
    assert r["df"]["joined_weekday"][0] == first.weekday()  # Monday = 0, like Python/pandas
    assert r["sources"]["joined_month"] == "joined"


def test_no_steps_after_onehot_and_clear_type_errors():
    df = data()
    with pytest.raises(UnprocessableError, match="can't come after"):
        run_plan(df, plan(city={"steps": [{"kind": "encode", "method": "onehot"}, {"kind": "scale"}]}))
    with pytest.raises(UnprocessableError, match="needs numbers"):
        run_plan(df, plan(city={"steps": [{"kind": "scale"}]}))
    with pytest.raises(UnprocessableError, match="date parts need a date"):
        run_plan(df, plan(city={"steps": [{"kind": "date_parts"}]}))
    with pytest.raises(Exception, match="only one target"):
        plan(age={"role": "target"}, churn={"role": "target"})


def test_empty_target_rows_are_left_out_and_warnings_flag_text_and_gaps():
    df = data()
    r = run_plan(df, plan(city={"role": "target"}))
    assert r["df"].height == df.height - df["city"].null_count()
    assert any("empty target" in w for w in r["warnings"])
    assert any("Still text" in w and "churn" in w for w in r["warnings"])
    assert any("Still has empty cells" in w and "age" in w for w in r["warnings"])


def test_learned_values_are_reported():
    df = data()
    r = run_plan(df, plan(price={"steps": [{"kind": "scale", "method": "standard"}]}))
    info = r["learned"]["price"][0]
    assert info["kind"] == "scale" and abs(info["center"] - df["price"].mean()) < 1e-9


def test_suggestions_are_hints_by_column_type():
    df = data()
    rel = [{"target": "revenue", "a": "units", "b": "price", "op": "*"}]
    s = suggest(df.with_columns((pl.col("units") * pl.col("price")).alias("revenue")), rel)
    assert s["id"]["suggested_role"] == "drop"
    assert s["revenue"]["suggested_role"] == "drop" and "redundant" in s["revenue"]["role_reason"]
    assert s["city"]["steps"][0]["kind"] == "fill_missing" and s["city"]["steps"][-1]["method"] == "onehot"
    assert s["income"]["steps"][0] == {"kind": "transform", "method": "log1p"}
    assert s["joined"]["steps"][0]["kind"] == "date_parts" and s["member"]["steps"] == [{"kind": "to_number"}]
    MLPlan.model_validate({"columns": {k: {"role": v["suggested_role"], "steps": v["steps"]} for k, v in s.items()}})


def test_flag_column_survives_onehot_and_split_explains_no_stratify():
    df = data()
    r = run_plan(df, plan(city={"steps": [{"kind": "fill_missing", "strategy": "mode", "add_indicator": True},
                                          {"kind": "encode", "method": "onehot"}]}))
    assert "city_was_missing" in r["df"].columns and r["df"]["city_was_missing"].sum() == df["city"].null_count()
    odd = df.with_columns(pl.when(pl.int_range(pl.len()) == 0).then(pl.lit("maybe")).otherwise(pl.col("churn")).alias("churn"))
    p = MLPlan.model_validate({"columns": {"churn": {"role": "target"}}, "split": {"enabled": True}})
    _, how = split_mask(odd, p.split, "churn")
    assert how.startswith("random (not stratified") and "'maybe'" in how


# ------------------------------------------------------------------ features: chaining + if/then/else
def titanic():
    return pl.DataFrame({
        "SibSp": [1, 0, 0, 3, 0, 1], "Parch": [0, 0, 2, 1, 0, 1],
        "Fare": [70.0, 8.0, 30.0, 50.0, None, 20.0], "Cabin": ["C85", None, "B2", None, None, "E1"],
        "Age": [22.0, 38.0, None, 4.0, 35.0, 10.0],
    })


def test_a_feature_can_use_an_earlier_feature():
    p = MLPlan.model_validate({"features": [
        {"name": "family_size", "formula": [{"column": "SibSp"}, "+", {"column": "Parch"}, "+", {"number": 1}]},
        {"name": "fare_per_person", "formula": [{"column": "Fare"}, "/", {"column": "family_size"}]},
    ]})
    out = run_plan(titanic(), p)["df"]
    assert out["family_size"].to_list() == [2.0, 1.0, 3.0, 5.0, 1.0, 3.0]
    assert out["fare_per_person"].to_list()[:4] == [35.0, 8.0, 10.0, 10.0] and out["fare_per_person"][4] is None


def test_if_then_else_features_including_your_example():
    p = MLPlan.model_validate({"features": [
        {"name": "family_size", "formula": [{"column": "SibSp"}, "+", {"column": "Parch"}]},
        {"name": "has_family", "kind": "condition", "conditions": [{"column": "family_size", "op": "gt", "value": 0}]},
        {"name": "has_cabin", "kind": "condition", "conditions": [{"column": "Cabin", "op": "not_empty"}]},
        {"name": "is_child", "kind": "condition", "conditions": [{"column": "Age", "op": "lt", "value": 13}]},
        {"name": "child_kept_empty", "kind": "condition", "conditions": [{"column": "Age", "op": "lt", "value": 13}], "empty_stays_empty": True},
        {"name": "score", "kind": "condition", "combine": "or", "then_value": 2.5, "else_value": -1,
         "conditions": [{"column": "Fare", "op": "ge", "value": 50}, {"column": "Age", "op": "lt", "value": 5}]},
    ]})
    out = run_plan(titanic(), p)["df"]
    assert out["has_family"].to_list() == [1, 0, 1, 1, 0, 1] and out["has_family"].dtype == pl.Int64
    assert out["has_cabin"].to_list() == [1, 0, 1, 0, 0, 1]
    assert out["is_child"].to_list() == [0, 0, 0, 1, 0, 1]          # empty Age -> else (0)
    assert out["child_kept_empty"].to_list() == [0, 0, None, 1, 0, 1]  # empty Age stays empty when asked
    assert out["score"].to_list() == [2.5, -1.0, -1.0, 2.5, -1.0, -1.0] and out["score"].dtype == pl.Float64


def test_feature_order_and_missing_inputs_are_explained():
    later = MLPlan.model_validate({"features": [
        {"name": "b", "formula": [{"column": "a"}, "*", {"number": 2}]},
        {"name": "a", "formula": [{"column": "SibSp"}]},
    ]})
    with pytest.raises(UnprocessableError, match="created after it"):
        run_plan(titanic(), later)
    gone = MLPlan.model_validate({"features": [{"name": "x", "kind": "condition", "conditions": [{"column": "nope", "op": "gt", "value": 0}]}]})
    with pytest.raises(UnprocessableError, match="doesn't exist"):
        run_plan(titanic(), gone)
    with pytest.raises(Exception, match="needs at least one condition"):
        MLPlan.model_validate({"features": [{"name": "y", "kind": "condition"}]})
    with pytest.raises(Exception, match="needs a formula"):
        MLPlan.model_validate({"features": [{"name": "z"}]})


def test_condition_features_can_get_steps_and_go_to_the_split():
    p = MLPlan.model_validate({
        "features": [{"name": "family_size", "formula": [{"column": "SibSp"}, "+", {"column": "Parch"}]},
                     {"name": "alone", "kind": "condition", "conditions": [{"column": "family_size", "op": "eq", "value": 0}]}],
        "columns": {"family_size": {"steps": [{"kind": "scale", "method": "minmax"}]}, "Cabin": {"role": "drop"}},
    })
    r = run_plan(titanic(), p)
    assert r["df"]["alone"].to_list() == [0, 1, 0, 0, 1, 0]
    assert r["df"]["family_size"].max() == 1.0 and "Cabin" not in r["df"].columns
