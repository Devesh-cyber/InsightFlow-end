"""
ML-ready dataset: the user's own per-column rules, applied in Polars.

The user decides everything. For every column: a role (use / drop / target) and an
ordered list of steps. Suggestions exist (see `suggest`) but are only applied when
the user picks them.

Steps (per column, in order):
  fill_missing  mean | median | mode | constant, optional "was missing" 0/1 column
  clip          percentile (e.g. 1st-99th) | IQR fences | fixed values
  transform     log(1+x) | ln | log10 | sqrt | square
  scale         standard (z-score) | min-max (0-1) | robust (median / IQR)
  encode        one-hot | frequency | ordinal       (text / category columns)
  bin           quantile | equal width              -> bin number 0..k-1
  date_parts    year, month, day, weekday, dayofyear, is_weekend, hour (replaces the date)
  to_number     true/false -> 1/0

New features: name = formula of numeric columns (+ - × ÷), computed before the steps.

Steps that LEARN from the data (fill values, clip limits, scaling, encodings, bin edges)
learn only from the training rows when a train/test split is on, so nothing about the
test rows leaks into training. Everything learned is returned so it can be written into
plan.json and reused on new data.
"""

import math
import random
import re
from typing import Annotated, Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.core.errors import UnprocessableError
from app.engine.infer import dtype_label
from app.engine.profile import column_role, is_numeric, is_temporal
from app.engine.rules import CALC_DECIMALS, EMAIL_RE, FormulaTerm, RuleError, _check_formula, formula_expr

MAX_STEPS = 10
MAX_FEATURES = 20
MAX_ONEHOT = 50
STRATIFY_MAX_CLASSES = 50


class MLPlanError(UnprocessableError):
    code = "invalid_ml_plan"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class FillMissing(_Strict):
    kind: Literal["fill_missing"]
    strategy: Literal["mean", "median", "mode", "constant"] = "median"
    value: Any = None
    add_indicator: bool = False

    @model_validator(mode="after")
    def _value(self):
        if self.strategy == "constant" and (self.value is None or str(self.value) == ""):
            raise ValueError("'constant' needs a value")
        return self


class Clip(_Strict):
    kind: Literal["clip"]
    method: Literal["percentile", "iqr", "values"] = "percentile"
    lower: float | None = None   # percentile (0-50) or a value
    upper: float | None = None   # percentile (50-100) or a value
    factor: float = Field(default=1.5, gt=0, le=10)

    @model_validator(mode="after")
    def _bounds(self):
        if self.method == "percentile":
            lo, hi = self.lower if self.lower is not None else 1.0, self.upper if self.upper is not None else 99.0
            if not (0 <= lo < hi <= 100):
                raise ValueError("percentiles must satisfy 0 ≤ lower < upper ≤ 100")
            self.lower, self.upper = lo, hi
        if self.method == "values":
            if self.lower is None and self.upper is None:
                raise ValueError("give a lower and/or an upper value")
            if self.lower is not None and self.upper is not None and self.lower > self.upper:
                raise ValueError("lower is larger than upper")
        return self


class Transform(_Strict):
    kind: Literal["transform"]
    method: Literal["log1p", "log", "log10", "sqrt", "square"] = "log1p"


class Scale(_Strict):
    kind: Literal["scale"]
    method: Literal["standard", "minmax", "robust"] = "standard"


class Encode(_Strict):
    kind: Literal["encode"]
    method: Literal["onehot", "frequency", "ordinal"] = "onehot"
    max_categories: int = Field(default=20, ge=1, le=MAX_ONEHOT)
    drop_first: bool = False
    order: list[str] | None = Field(default=None, max_length=500)


class Bin(_Strict):
    kind: Literal["bin"]
    method: Literal["quantile", "equal_width"] = "quantile"
    bins: int = Field(default=5, ge=2, le=50)


DATE_PARTS = ("year", "month", "day", "weekday", "dayofyear", "is_weekend", "hour")


class DateParts(_Strict):
    kind: Literal["date_parts"]
    parts: list[Literal["year", "month", "day", "weekday", "dayofyear", "is_weekend", "hour"]] = Field(
        default_factory=lambda: ["year", "month", "weekday"], min_length=1)


class ToNumber(_Strict):
    kind: Literal["to_number"]


Step = Annotated[FillMissing | Clip | Transform | Scale | Encode | Bin | DateParts | ToNumber, Field(discriminator="kind")]


class ColumnPlan(_Strict):
    role: Literal["use", "drop", "target"] = "use"
    steps: list[Step] = Field(default_factory=list, max_length=MAX_STEPS)


class Feature(_Strict):
    name: str = Field(min_length=1, max_length=64)
    formula: list[FormulaTerm]

    @field_validator("formula")
    @classmethod
    def _formula(cls, terms):
        return _check_formula(terms)

    @field_validator("name")
    @classmethod
    def _name(cls, v):
        v = v.strip()
        if not v:
            raise ValueError("a feature needs a name")
        return v


class Split(_Strict):
    enabled: bool = False
    test_size: float = Field(default=0.2, ge=0.05, le=0.5)
    seed: int = Field(default=42, ge=0, le=2**31 - 1)
    stratify: bool = True


class MLPlan(_Strict):
    columns: dict[str, ColumnPlan] = Field(default_factory=dict)
    features: list[Feature] = Field(default_factory=list, max_length=MAX_FEATURES)
    split: Split = Field(default_factory=Split)

    @model_validator(mode="after")
    def _one_target(self):
        targets = [c for c, p in self.columns.items() if p.role == "target"]
        if len(targets) > 1:
            raise ValueError(f"only one target column is allowed (got {', '.join(targets)})")
        names = [f.name for f in self.features]
        if len(names) != len(set(names)):
            raise ValueError("feature names must be unique")
        return self

    @property
    def target(self) -> str | None:
        return next((c for c, p in self.columns.items() if p.role == "target"), None)


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _err(column: str, message: str) -> MLPlanError:
    return MLPlanError(f"'{column}': {message}")


def _need_numeric(s: pl.Series, column: str, step: str) -> None:
    if not is_numeric(s.dtype):
        raise _err(column, f"'{step}' needs numbers but the column is {dtype_label(s.dtype)}. "
                           "Encode it or convert its type first.")


def _quantile(values: pl.Series, q: float) -> float:
    """Linear interpolation, the same as numpy/pandas/scikit-learn defaults."""
    return float(values.quantile(q, interpolation="linear"))


def _safe_name(text: str) -> str:
    cleaned = re.sub(r"\s+", "_", str(text).strip())
    return cleaned or "blank"


def _mode(values: pl.Series) -> Any:
    counts = values.drop_nulls().value_counts()
    if counts.height == 0:
        return None
    name = counts.columns[0]
    counts = counts.with_columns(pl.col(name).cast(pl.String).alias("__key"))
    best = counts.sort(["count", "__key"], descending=[True, False]).row(0, named=True)  # ties -> smallest
    return best[name]


def _f(x: float | None) -> float | None:
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else round(float(x), 12)


# ---------------------------------------------------------------------------
# one step: (series, fit rows) -> list of output series (+ what was learned, warnings)
# ---------------------------------------------------------------------------
def _apply_step(s: pl.Series, fit: pl.Series, column: str, step, warnings: list[str]) -> tuple[list[pl.Series], dict]:
    name = s.name
    kind = step.kind

    if kind == "fill_missing":
        out: list[pl.Series] = []
        if step.add_indicator:
            out.append(s.is_null().cast(pl.Int8).alias(f"{name}_was_missing"))
        present = fit.drop_nulls()
        if step.strategy in ("mean", "median"):
            _need_numeric(s, column, f"fill with {step.strategy}")
            if present.len() == 0:
                raise _err(column, "no values to compute a fill value from")
            value = float(present.mean() if step.strategy == "mean" else present.median())
            filled = s.cast(pl.Float64).fill_null(value)
        elif step.strategy == "mode":
            value = _mode(fit)
            if value is None:
                raise _err(column, "no values to compute the most common value from")
            filled = s.fill_null(value)
        else:
            value = step.value
            if is_numeric(s.dtype):
                try:
                    value = float(value)
                except (TypeError, ValueError) as exc:
                    raise _err(column, f"'{step.value}' isn't a number") from exc
                filled = s.cast(pl.Float64).fill_null(value)
            else:
                filled = s.cast(pl.String).fill_null(str(value))
        return [filled.alias(name), *out], {"kind": kind, "strategy": step.strategy, "value": value if not isinstance(value, float) else _f(value),
                                            "add_indicator": step.add_indicator}

    if kind == "clip":
        _need_numeric(s, column, "clip")
        present = fit.drop_nulls().cast(pl.Float64)
        if step.method == "values":
            lo, hi = step.lower, step.upper
        else:
            if present.len() == 0:
                raise _err(column, "no values to compute clip limits from")
            if step.method == "percentile":
                lo, hi = _quantile(present, step.lower / 100), _quantile(present, step.upper / 100)
            else:
                q1, q3 = _quantile(present, 0.25), _quantile(present, 0.75)
                lo, hi = q1 - step.factor * (q3 - q1), q3 + step.factor * (q3 - q1)
        x = s.cast(pl.Float64)
        clipped = x.clip(lo, hi)
        n = (int((x < lo).sum()) if lo is not None else 0) + (int((x > hi).sum()) if hi is not None else 0)
        if n:
            warnings.append(f"'{column}': {n} value(s) clipped to {lo if lo is None else round(lo, 6)} … {hi if hi is None else round(hi, 6)}.")
        return [clipped.alias(name)], {"kind": kind, "method": step.method, "lower": _f(lo), "upper": _f(hi)}

    if kind == "transform":
        _need_numeric(s, column, "transform")
        x = s.cast(pl.Float64)
        bound, inclusive = {"log1p": (-1.0, False), "log": (0.0, False), "log10": (0.0, False), "sqrt": (0.0, True),
                            "square": (None, True)}[step.method]
        if bound is None:
            valid = x.is_not_null()
        else:
            valid = (x >= bound) if inclusive else (x > bound)
        fn = {"log1p": x.log1p(), "log": x.log(), "log10": x.log(10), "sqrt": x.sqrt(), "square": x * x}[step.method]
        out = pl.select(pl.when(valid.fill_null(False)).then(fn).otherwise(None)).to_series().round(CALC_DECIMALS)
        bad = int((x.is_not_null() & ~valid.fill_null(True)).sum())
        if bad:
            warnings.append(f"'{column}': {bad} value(s) are outside the range of {step.method} and became missing.")
        return [out.alias(name)], {"kind": kind, "method": step.method}

    if kind == "scale":
        _need_numeric(s, column, "scale")
        present = fit.drop_nulls().cast(pl.Float64)
        if present.len() == 0:
            raise _err(column, "no values to learn the scale from")
        x = s.cast(pl.Float64)
        if step.method == "standard":
            center, spread = float(present.mean()), float(present.std(ddof=0) or 0.0)
        elif step.method == "minmax":
            center, spread = float(present.min()), float(present.max() - present.min())
        else:
            center = float(present.median())
            spread = _quantile(present, 0.75) - _quantile(present, 0.25)
        if not spread:
            warnings.append(f"'{column}': the values don't vary, so scaling leaves them at 0.")
            spread = 1.0
        return [((x - center) / spread).alias(name)], {"kind": kind, "method": step.method, "center": _f(center), "spread": _f(spread)}

    if kind == "encode":
        if s.dtype.is_float():
            raise _err(column, "encoding is for categories; this column holds decimal numbers (bin it first or leave it as a number)")
        text = s.cast(pl.String)
        fit_text = fit.cast(pl.String).drop_nulls()
        counts = fit_text.value_counts()
        key = counts.columns[0]
        counts = counts.sort(["count", key], descending=[True, False])
        if step.method == "onehot":
            cats = counts[key].head(step.max_categories).to_list()
            if not cats:
                raise _err(column, "no categories to encode")
            others = text.is_not_null() & ~text.is_in(cats)
            outs = []
            used = set()
            for i, cat in enumerate(cats):
                col_name = f"{name}_{_safe_name(cat)}"
                while col_name in used:
                    col_name += "_"
                used.add(col_name)
                if step.drop_first and i == 0:
                    continue
                outs.append((text == cat).fill_null(False).cast(pl.Int8).alias(col_name))
            if int(others.sum()) or counts.height > len(cats):
                outs.append(others.cast(pl.Int8).alias(f"{name}_other"))
            missing = int(text.is_null().sum())
            if missing:
                warnings.append(f"'{column}': {missing} empty value(s) get 0 in every one-hot column (fill them first to keep them).")
            return outs, {"kind": kind, "method": "onehot", "categories": cats, "drop_first": step.drop_first,
                          "other": f"{name}_other" in [o.name for o in outs]}
        if step.method == "frequency":
            total = int(counts["count"].sum()) or 1
            mapping = {row[key]: row["count"] / total for row in counts.iter_rows(named=True)}
            out = text.replace_strict(mapping, default=0.0, return_dtype=pl.Float64)
            out = pl.select(pl.when(text.is_null()).then(None).otherwise(out)).to_series()
            return [out.alias(name)], {"kind": kind, "method": "frequency", "mapping": {k: _f(v) for k, v in mapping.items()}}
        order = [str(v) for v in step.order] if step.order else sorted(counts[key].to_list())
        mapping = {v: i for i, v in enumerate(order)}
        out = text.replace_strict(mapping, default=None, return_dtype=pl.Int64)
        unseen = int((text.is_not_null() & out.is_null()).sum())
        if unseen:
            warnings.append(f"'{column}': {unseen} value(s) aren't in the order list and became missing.")
        return [out.alias(name)], {"kind": kind, "method": "ordinal", "order": order}

    if kind == "bin":
        _need_numeric(s, column, "bin")
        present = fit.drop_nulls().cast(pl.Float64)
        if present.len() == 0:
            raise _err(column, "no values to compute bin edges from")
        if step.method == "quantile":
            edges = [_quantile(present, i / step.bins) for i in range(1, step.bins)]
        else:
            lo, hi = float(present.min()), float(present.max())
            edges = [lo + (hi - lo) * i / step.bins for i in range(1, step.bins)]
        edges = sorted(set(edges))
        x = s.cast(pl.Float64)
        index = pl.select(pl.sum_horizontal([(pl.lit(x) >= e).cast(pl.Int64) for e in edges]) if edges else pl.lit(0)).to_series()
        out = pl.select(pl.when(pl.lit(x).is_null()).then(None).otherwise(pl.lit(index))).to_series()
        if len(edges) < step.bins - 1:
            warnings.append(f"'{column}': many equal values, so only {len(edges) + 1} distinct bins could be made.")
        return [out.cast(pl.Int64).alias(name)], {"kind": kind, "method": step.method, "edges": [_f(e) for e in edges]}

    if kind == "date_parts":
        if not is_temporal(s.dtype):
            raise _err(column, "date parts need a date column (convert it with 'Change column type' first)")
        outs = []
        for part in step.parts:
            if part == "hour" and s.dtype == pl.Date:
                continue
            expr = {
                "year": s.dt.year(), "month": s.dt.month(), "day": s.dt.day(),
                "weekday": s.dt.weekday() - 1,              # Monday = 0, like pandas
                "dayofyear": s.dt.ordinal_day(),
                "is_weekend": (s.dt.weekday() >= 6).cast(pl.Int8),
                "hour": s.dt.hour() if s.dtype != pl.Date else None,
            }[part]
            outs.append(expr.cast(pl.Int64).alias(f"{name}_{part}"))
        return outs, {"kind": kind, "parts": list(step.parts)}

    if kind == "to_number":
        if s.dtype == pl.Boolean:
            return [s.cast(pl.Int8).alias(name)], {"kind": kind}
        if is_numeric(s.dtype):
            return [s], {"kind": kind}
        raise _err(column, "to_number turns true/false into 1/0; for text categories use 'encode'")

    raise _err(column, f"unknown step '{kind}'")


EXPANDING = {"date_parts"}


def _expands(step) -> bool:
    return step.kind in EXPANDING or (step.kind == "encode" and step.method == "onehot")


# ---------------------------------------------------------------------------
# split
# ---------------------------------------------------------------------------
def split_mask(df: pl.DataFrame, split: Split, target: str | None) -> tuple[pl.Series, str]:
    """True = train row. Deterministic for a given seed."""
    n = df.height
    rnd = random.Random(split.seed)
    is_train = [True] * n
    how = "random"
    groups: list[list[int]] = [list(range(n))]
    if split.stratify and target and target in df.columns:
        labels = df.get_column(target)
        classes = labels.n_unique()
        smallest = int(labels.value_counts()["count"].min())
        if classes > STRATIFY_MAX_CLASSES:
            how = f"random (not stratified: '{target}' has {classes} different values, too many to keep in proportion)"
        elif smallest < 2:
            rare = labels.value_counts().filter(pl.col("count") < 2)[target].to_list()[:3]
            how = (f"random (not stratified: {', '.join(repr(v) for v in rare)} occur(s) only once in '{target}'; "
                   "clean or merge rare values to stratify)")
        if 1 < classes <= STRATIFY_MAX_CLASSES and smallest >= 2:
            buckets: dict[Any, list[int]] = {}
            for i, value in enumerate(labels.to_list()):
                buckets.setdefault(value, []).append(i)
            groups = [buckets[k] for k in sorted(buckets, key=lambda v: (v is None, str(v)))]
            how = f"stratified by '{target}'"
    for group in groups:
        idx = group[:]
        rnd.shuffle(idx)
        n_test = max(1, round(len(idx) * split.test_size)) if len(idx) > 1 else 0
        for i in idx[:n_test]:
            is_train[i] = False
    return pl.Series("is_train", is_train), how


# ---------------------------------------------------------------------------
# the whole plan
# ---------------------------------------------------------------------------
def run_plan(df: pl.DataFrame, plan: MLPlan) -> dict:
    warnings: list[str] = []
    data = df

    # 1. new features (formulas on the cleaned columns)
    for feature in plan.features:
        if feature.name in data.columns:
            raise MLPlanError(f"Feature '{feature.name}': a column with that name already exists.")
        try:
            data = data.with_columns(formula_expr(data, feature.formula).cast(pl.Float64).round(CALC_DECIMALS).alias(feature.name))
        except RuleError as exc:
            raise MLPlanError(f"Feature '{feature.name}': {exc.message}") from exc

    for missing in [c for c in plan.columns if c not in data.columns]:
        warnings.append(f"'{missing}' is no longer in the data, so its rules were skipped.")

    # 2. target: rows without a target can't be used for training
    target = plan.target if plan.target in data.columns else None
    if target:
        empty = int(data.get_column(target).is_null().sum())
        if empty:
            data = data.filter(pl.col(target).is_not_null())
            warnings.append(f"{empty} row(s) with an empty target '{target}' were left out.")

    # 3. which rows the learning steps learn from
    if plan.split.enabled:
        if data.height < 4:
            raise MLPlanError("Too few rows to split into train and test.")
        is_train, how = split_mask(data, plan.split, target)
    else:
        is_train, how = pl.Series("is_train", [True] * data.height), "none"

    # 4. column by column, in the user's order
    outputs: list[pl.Series] = []
    sources: dict[str, str] = {}
    learned: dict[str, list[dict]] = {}
    target_outputs: list[pl.Series] = []
    for column in data.columns:
        cplan = plan.columns.get(column, ColumnPlan())
        if cplan.role == "drop":
            continue
        current = [data.get_column(column)]
        steps_learned = []
        expanded = False
        for step in cplan.steps:
            if expanded:
                raise _err(column, f"'{step.kind}' can't come after a step that splits the column into several columns "
                                   "(one-hot or date parts). Put it before.")
            main, *extra = current
            produced, info = _apply_step(main, main.filter(is_train), column, step, warnings)
            # one-hot / date parts replace the column by several; earlier extras (a 'was missing' flag) stay
            current = [*produced, *extra] if _expands(step) else [produced[0], *extra, *produced[1:]]
            expanded = _expands(step)
            steps_learned.append(info)
        if steps_learned:
            learned[column] = steps_learned
        for series in current:
            sources[series.name] = column
        (target_outputs if cplan.role == "target" else outputs).extend(current)

    columns = outputs + target_outputs
    names = [s.name for s in columns]
    dupes = sorted({n for n in names if names.count(n) > 1})
    if dupes:
        raise MLPlanError(f"Two output columns would be called {', '.join(repr(d) for d in dupes)}. Rename a feature or drop a column.")
    if not columns:
        raise MLPlanError("Every column is dropped; keep at least one.")
    result = pl.DataFrame(columns)

    leftovers = [s.name for s in outputs if s.dtype in (pl.String, pl.Categorical)]
    if leftovers:
        warnings.append(f"Still text (most models need numbers): {', '.join(leftovers)}. Encode or drop them.")
    nulls = [s.name for s in columns if s.null_count()]
    if nulls:
        warnings.append(f"Still has empty cells: {', '.join(nulls[:8])}{' …' if len(nulls) > 8 else ''}. Many models can't handle them; add 'fill missing'.")

    return {"df": result, "is_train": is_train, "split": how, "learned": learned, "sources": sources, "warnings": warnings,
            "target": target}


# ---------------------------------------------------------------------------
# suggestions (hints only; nothing is applied unless the user picks it)
# ---------------------------------------------------------------------------
def suggest(df: pl.DataFrame, relationships: list[dict] | None = None) -> dict[str, dict]:
    derived = {r["target"]: r for r in relationships or []}
    rows = df.height
    out: dict[str, dict] = {}
    for name, dtype in zip(df.columns, df.dtypes):
        s = df.get_column(name)
        non_null = rows - s.null_count()
        uniques = s.drop_nulls().n_unique()
        role = column_role(dtype, rows, non_null, uniques)
        steps: list[dict] = []
        reasons: list[str] = []
        suggested_role, role_reason = "use", None

        if role in ("empty", "constant"):
            suggested_role, role_reason = "drop", "no information: the column is empty or has one value"
        elif role == "identifier":
            suggested_role, role_reason = "drop", "an ID: different for every row, so it can't generalise"
        elif dtype == pl.String and float(s.drop_nulls().str.contains(EMAIL_RE).mean() or 0) > 0.8:
            suggested_role, role_reason = "drop", "emails identify people; they don't help a model generalise"
        elif role == "free_text":
            suggested_role, role_reason = "drop", "free text: almost every value is different"
        elif name in derived:
            r = derived[name]
            suggested_role = "drop"
            role_reason = f"redundant: {name} = {r['a']} {'×' if r['op'] == '*' else '+'} {r['b']}; keep either it or its parts"

        if suggested_role == "use":
            if s.null_count():
                if is_numeric(dtype):
                    steps.append({"kind": "fill_missing", "strategy": "median", "add_indicator": True})
                    reasons.append("has gaps: fill with the median and keep a 'was missing' flag")
                elif dtype in (pl.String, pl.Boolean):
                    steps.append({"kind": "fill_missing", "strategy": "mode", "add_indicator": True})
                    reasons.append("has gaps: fill with the most common value and keep a 'was missing' flag")
            if is_temporal(dtype):
                steps.append({"kind": "date_parts", "parts": ["year", "month", "weekday"]})
                reasons.append("dates: models use parts like month and weekday")
            elif dtype == pl.Boolean:
                steps.append({"kind": "to_number"})
                reasons.append("true/false → 1/0")
            elif dtype == pl.String:
                if uniques <= 15:
                    steps.append({"kind": "encode", "method": "onehot", "max_categories": 15})
                    reasons.append(f"category with {uniques} values: one column per value")
                else:
                    steps.append({"kind": "encode", "method": "frequency"})
                    reasons.append(f"category with {uniques} values: too many for one-hot, use how often each occurs")
            elif is_numeric(dtype) and role == "numeric":
                values = s.drop_nulls().cast(pl.Float64)
                skew = 0.0
                if values.len() >= 20 and values.n_unique() > 2:
                    q1, q3 = _quantile(values, 0.25), _quantile(values, 0.75)
                    core = values.filter(values.is_between(q1 - 3 * (q3 - q1), q3 + 3 * (q3 - q1))) if q3 > q1 else values
                    skew = float(core.skew() or 0.0) if core.len() >= 20 else 0.0
                if skew > 1 and float(values.min()) >= 0:
                    steps.append({"kind": "transform", "method": "log1p"})
                    reasons.append(f"right-skewed (skewness {skew:.2f}): log(1 + x) evens it out")
                steps.append({"kind": "scale", "method": "standard"})
                reasons.append("scale to mean 0 / std 1: needed for linear and distance models, harmless for trees")
            elif is_numeric(dtype) and role == "categorical":
                steps.append({"kind": "encode", "method": "onehot", "max_categories": 15})
                reasons.append(f"whole numbers with only {uniques} values: likely a category code")

        out[name] = {
            "dtype": dtype_label(dtype),
            "role": role,
            "suggested_role": suggested_role,
            "role_reason": role_reason,
            "steps": steps,
            "reason": "; ".join(reasons) or None,
            "missing": s.null_count(),
            "unique": uniques,
        }
    return out
