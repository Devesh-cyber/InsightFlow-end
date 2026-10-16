"""
Cleaning operations.

Each operation = a strict parameter model + a pure function df -> df.
`run_operation` wraps every operation the same way to produce the EVIDENCE:
rows removed, cells changed per column, type changes, and before/after
samples of the actual rows affected. Preview and apply use the exact same
code path, so what you preview is exactly what gets applied.

A hidden row id column tracks rows through the operation so we can tell
"changed" from "removed". Operations never see or touch it.
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

from app.core.errors import UnprocessableError
from app.engine.infer import FLOAT_RE, dtype_label
from app.engine.jsonsafe import to_json
from app.engine.profile import iqr_bounds, is_numeric, is_temporal

ROW_ID = "__insightflow_row_id__"
SAMPLE_ROWS = 20
SAMPLE_MAX_COLUMNS = 12
DEFAULT_TRUE = {"true", "yes", "y", "1", "t"}
DEFAULT_FALSE = {"false", "no", "n", "0", "f"}


class OperationError(UnprocessableError):
    code = "invalid_operation"


def _missing_columns(df: pl.DataFrame, columns: list[str]) -> None:
    unknown = [c for c in columns if c not in df.columns or c == ROW_ID]
    if unknown:
        raise UnprocessableError(
            f"Column(s) not found: {', '.join(repr(c) for c in unknown)}.", code="column_not_found",
            details={"columns": unknown},
        )


def _data_columns(df: pl.DataFrame) -> list[str]:
    return [c for c in df.columns if c != ROW_ID]


class Params(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Parameter models
# ---------------------------------------------------------------------------
class DropColumns(Params):
    columns: list[str] = Field(min_length=1)


class RenameColumns(Params):
    mapping: dict[str, str] = Field(min_length=1)

    @field_validator("mapping")
    @classmethod
    def _clean(cls, mapping: dict[str, str]) -> dict[str, str]:
        cleaned = {old: new.strip() for old, new in mapping.items()}
        if any(not new for new in cleaned.values()):
            raise ValueError("new names cannot be empty")
        if len(set(cleaned.values())) != len(cleaned):
            raise ValueError("new names must be unique")
        return cleaned


class DropDuplicates(Params):
    columns: list[str] | None = None
    keep: Literal["first", "last"] = "first"


class NoParams(Params):
    pass


class DropMissingRows(Params):
    how: Literal["any", "all"] = "any"
    columns: list[str] | None = None


class FillMissing(Params):
    columns: list[str] = Field(min_length=1)
    strategy: Literal["mean", "median", "mode", "constant", "forward", "backward"]
    value: Any = None


class ConvertType(Params):
    column: str
    to: Literal["integer", "float", "boolean", "date", "datetime", "text"]
    treat_as_missing: list[str] = Field(default_factory=list)
    format: str | None = None
    format_options: list[str] | None = None
    true_values: list[str] | None = None
    false_values: list[str] | None = None
    on_error: Literal["null", "abort"] = "null"


class ParseFormattedNumber(Params):
    column: str
    is_percent: bool = False


class MarkMissing(Params):
    columns: list[str] = Field(min_length=1)
    values: list[str] = Field(min_length=1)


class TrimWhitespace(Params):
    columns: list[str] = Field(min_length=1)


class ChangeCase(Params):
    columns: list[str] = Field(min_length=1)
    case: Literal["lower", "upper", "title"]


class ReplaceValues(Params):
    column: str
    find: str = Field(min_length=1)
    replace: str
    match: Literal["exact", "contains"] = "exact"


class RoundNumbers(Params):
    columns: list[str] = Field(min_length=1)
    decimals: int = Field(ge=0, le=10)


class HandleOutliers(Params):
    column: str
    method: Literal["iqr", "zscore"] = "iqr"
    factor: float | None = Field(default=None, gt=0, le=10)
    action: Literal["cap", "remove"]


# ---------------------------------------------------------------------------
# Implementations: (df with ROW_ID, params) -> (df, warnings)
# ---------------------------------------------------------------------------
def _drop_columns(df, p: DropColumns):
    _missing_columns(df, p.columns)
    if len(set(p.columns)) >= len(_data_columns(df)):
        raise OperationError("You can't drop every column.")
    return df.drop(p.columns), []


def _rename_columns(df, p: RenameColumns):
    _missing_columns(df, list(p.mapping))
    remaining = set(_data_columns(df)) - set(p.mapping)
    clashes = [new for new in p.mapping.values() if new in remaining or new == ROW_ID]
    if clashes:
        raise OperationError(f"Name(s) already in use: {', '.join(clashes)}.")
    return df.rename(p.mapping), []


def _drop_duplicates(df, p: DropDuplicates):
    subset = p.columns or _data_columns(df)
    _missing_columns(df, subset)
    return df.unique(subset=subset, keep=p.keep, maintain_order=True), []


def _drop_empty_columns(df, p: NoParams):
    empty = [c for c in _data_columns(df) if df.get_column(c).null_count() == df.height]
    if len(empty) == len(_data_columns(df)):
        raise OperationError("Every column is empty; nothing would be left.")
    return df.drop(empty), [] if empty else ["No empty columns were found."]


def _drop_constant_columns(df, p: NoParams):
    constant = [c for c in _data_columns(df) if df.get_column(c).drop_nulls().n_unique() == 1]
    if constant and len(constant) == len(_data_columns(df)):
        raise OperationError("Every column is constant; nothing would be left.")
    return df.drop(constant), [] if constant else ["No constant columns were found."]


def _drop_missing_rows(df, p: DropMissingRows):
    subset = p.columns or _data_columns(df)
    _missing_columns(df, subset)
    nulls = [pl.col(c).is_null() for c in subset]
    condition = pl.any_horizontal(nulls) if p.how == "any" else pl.all_horizontal(nulls)
    return df.filter(~condition), []


def _cast_constant(series: pl.Series, value: Any) -> Any:
    if value is None:
        raise OperationError("A fill value is required for the 'constant' strategy.")
    dtype = series.dtype
    try:
        if dtype == pl.Boolean:
            text = str(value).strip().lower()
            if text in DEFAULT_TRUE:
                return True
            if text in DEFAULT_FALSE:
                return False
            raise ValueError
        if dtype.is_integer():
            number = float(value)
            if not number.is_integer():
                raise ValueError
            return int(number)
        if dtype.is_float():
            return float(value)
        if dtype == pl.Date:
            return pl.Series([str(value)]).str.to_date(strict=True)[0]
        if isinstance(dtype, pl.Datetime):
            return pl.Series([str(value)]).str.to_datetime(strict=True)[0]
        return str(value)
    except (ValueError, TypeError, pl.exceptions.PolarsError) as exc:
        raise OperationError(f"{value!r} isn't a valid {dtype_label(dtype)} value for '{series.name}'.") from exc


def _fill_missing(df, p: FillMissing):
    _missing_columns(df, p.columns)
    warnings, exprs = [], []
    for name in p.columns:
        series = df.get_column(name)
        if series.null_count() == 0:
            warnings.append(f"'{name}' has no missing values.")
            continue
        col = pl.col(name)
        if p.strategy in ("mean", "median"):
            if not is_numeric(series.dtype):
                raise OperationError(f"{p.strategy.title()} only works on numeric columns; '{name}' is {dtype_label(series.dtype)}.")
            fill = getattr(series.drop_nulls().cast(pl.Float64), p.strategy)()
            if fill is None:
                raise OperationError(f"'{name}' has no values to compute a {p.strategy} from.")
            if series.dtype.is_integer() and float(fill).is_integer():
                exprs.append(col.fill_null(int(fill)))
            elif series.dtype.is_integer():
                warnings.append(f"'{name}' becomes decimal: the {p.strategy} ({fill:.4g}) isn't a whole number.")
                exprs.append(col.cast(pl.Float64).fill_null(fill))
            else:
                exprs.append(col.fill_null(fill))
        elif p.strategy == "mode":
            modes = series.drop_nulls().mode().sort()
            if len(modes) == 0:
                raise OperationError(f"'{name}' has no values to take the most common from.")
            if len(modes) > 1:
                warnings.append(f"'{name}' has several equally common values; used {to_json(modes[0])!r}.")
            exprs.append(col.fill_null(modes[0]))
        elif p.strategy == "constant":
            exprs.append(col.fill_null(_cast_constant(series, p.value)))
        elif p.strategy == "forward":
            exprs.append(col.forward_fill())
        else:
            exprs.append(col.backward_fill())
    return (df.with_columns(exprs) if exprs else df), warnings


def _convert_type(df, p: ConvertType):
    _missing_columns(df, [p.column])
    series = df.get_column(p.column)
    original_nulls = series.is_null()
    text = series.cast(pl.String).str.strip_chars()
    if p.treat_as_missing:
        text = pl.select(pl.when(text.is_in(p.treat_as_missing)).then(None).otherwise(text).alias(p.column)).to_series()

    target = p.to
    if target == "text":
        if is_temporal(series.dtype):
            converted = series.dt.to_string("%Y-%m-%d" if series.dtype == pl.Date else "%Y-%m-%d %H:%M:%S")
        else:
            converted = series.cast(pl.String)
    elif target in ("integer", "float"):
        cleaned = text if series.dtype == pl.String else series.cast(pl.String)
        numbers = pl.select(pl.when(cleaned.str.contains(FLOAT_RE)).then(cleaned).otherwise(None)).to_series().cast(pl.Float64, strict=False)
        if target == "integer":
            whole = numbers.is_null() | (numbers == numbers.floor())
            numbers = pl.select(pl.when(whole).then(numbers).otherwise(None)).to_series()
            converted = numbers.cast(pl.Int64, strict=False)
        else:
            converted = numbers
    elif target == "boolean":
        trues = {v.lower() for v in (p.true_values or DEFAULT_TRUE)}
        falses = {v.lower() for v in (p.false_values or DEFAULT_FALSE)}
        lowered = text.str.to_lowercase()
        converted = pl.select(
            pl.when(lowered.is_in(list(trues))).then(True).when(lowered.is_in(list(falses))).then(False).otherwise(None)
        ).to_series()
    else:  # date / datetime
        formats = [p.format] if p.format else (p.format_options or [None])
        best = None
        for fmt in formats:
            try:
                candidate = (
                    text.str.to_date(fmt, strict=False) if target == "date" else text.str.to_datetime(fmt, strict=False)
                )
            except pl.exceptions.PolarsError:
                continue
            if best is None or candidate.null_count() < best.null_count():
                best = candidate
        if best is None:
            raise OperationError("None of the date formats could be applied.")
        converted = best

    converted = converted.alias(p.column)
    marked = text.is_null() & ~original_nulls
    failed = converted.is_null() & ~original_nulls & ~marked
    failures = int(failed.sum())
    warnings = []
    if failures:
        examples = series.filter(failed).cast(pl.String).value_counts(sort=True).head(5)
        listed = ", ".join(f"{v!r} ({c})" for v, c in examples.iter_rows())
        if p.on_error == "abort":
            raise OperationError(
                f"{failures} value(s) in '{p.column}' can't be converted to {target}: {listed}.",
                details={"failures": failures},
            )
        warnings.append(f"{failures} value(s) couldn't be converted and became missing: {listed}.")
    if int(marked.sum()):
        warnings.append(f"{int(marked.sum())} value(s) listed as missing markers became missing.")
    return df.with_columns(converted), warnings


def _parse_formatted_number(df, p: ParseFormattedNumber):
    _missing_columns(df, [p.column])
    series = df.get_column(p.column)
    if series.dtype != pl.String:
        raise OperationError(f"'{p.column}' is already {dtype_label(series.dtype)}.")
    text = series.str.strip_chars()
    is_pct = text.str.ends_with("%")
    cleaned = text.str.replace_all(r"[₹$€£,%\s]", "")
    numbers = pl.select(pl.when(cleaned.str.contains(FLOAT_RE)).then(cleaned).otherwise(None)).to_series().cast(pl.Float64, strict=False)
    if p.is_percent:
        numbers = pl.select(pl.when(is_pct).then(numbers / 100).otherwise(numbers)).to_series()
    failed = numbers.is_null() & series.is_not_null()
    warnings = []
    if int(failed.sum()):
        warnings.append(f"{int(failed.sum())} value(s) couldn't be parsed and became missing.")
    non_null = numbers.drop_nulls()
    if len(non_null) and bool((non_null == non_null.floor()).all()) and not p.is_percent:
        numbers = numbers.cast(pl.Int64)
    return df.with_columns(numbers.alias(p.column)), warnings


def _mark_missing(df, p: MarkMissing):
    _missing_columns(df, p.columns)
    exprs = []
    for name in p.columns:
        dtype = df.get_column(name).dtype
        if dtype == pl.String:
            exprs.append(pl.when(pl.col(name).str.strip_chars().is_in(p.values)).then(None).otherwise(pl.col(name)).alias(name))
        elif is_numeric(dtype):
            numbers = [float(v) for v in p.values if _is_number(v)]
            exprs.append(pl.when(pl.col(name).cast(pl.Float64).is_in(numbers)).then(None).otherwise(pl.col(name)).alias(name))
        else:
            exprs.append(pl.when(pl.col(name).cast(pl.String).is_in(p.values)).then(None).otherwise(pl.col(name)).alias(name))
    return df.with_columns(exprs), []


def _is_number(value: str) -> bool:
    try:
        float(value)
        return True
    except ValueError:
        return False


def _text_columns(df, columns: list[str], what: str) -> tuple[list[str], list[str]]:
    _missing_columns(df, columns)
    text = [c for c in columns if df.get_column(c).dtype == pl.String]
    skipped = [c for c in columns if c not in text]
    if not text:
        raise OperationError(f"{what} only applies to text columns.")
    return text, [f"Skipped non-text column(s): {', '.join(skipped)}."] if skipped else []


def _trim_whitespace(df, p: TrimWhitespace):
    text, warnings = _text_columns(df, p.columns, "Trimming spaces")
    return df.with_columns([pl.col(c).str.strip_chars() for c in text]), warnings


def _change_case(df, p: ChangeCase):
    text, warnings = _text_columns(df, p.columns, "Changing case")
    method = {"lower": "to_lowercase", "upper": "to_uppercase", "title": "to_titlecase"}[p.case]
    return df.with_columns([getattr(pl.col(c).str, method)() for c in text]), warnings


def _replace_values(df, p: ReplaceValues):
    text, _ = _text_columns(df, [p.column], "Find and replace")
    col = pl.col(p.column)
    if p.match == "exact":
        expr = pl.when(col == p.find).then(pl.lit(p.replace)).otherwise(col).alias(p.column)
    else:
        expr = col.str.replace_all(p.find, p.replace, literal=True)
    return df.with_columns(expr), []


def _round_numbers(df, p: RoundNumbers):
    _missing_columns(df, p.columns)
    floats = [c for c in p.columns if df.get_column(c).dtype.is_float()]
    skipped = [c for c in p.columns if c not in floats]
    if not floats:
        raise OperationError("Rounding only applies to decimal (float) columns.")
    warnings = [f"Skipped non-decimal column(s): {', '.join(skipped)}."] if skipped else []
    return df.with_columns([pl.col(c).round(p.decimals) for c in floats]), warnings


def _handle_outliers(df, p: HandleOutliers):
    _missing_columns(df, [p.column])
    series = df.get_column(p.column)
    if not is_numeric(series.dtype):
        raise OperationError(f"Outlier handling needs a numeric column; '{p.column}' is {dtype_label(series.dtype)}.")
    if p.method == "iqr":
        bounds = iqr_bounds(series, p.factor or 1.5)
        if bounds is None:
            raise OperationError("Not enough values to compute outlier bounds.")
        lower, upper = bounds[2], bounds[3]
    else:
        values = series.drop_nulls().cast(pl.Float64)
        mean, std = values.mean(), values.std()
        if not std:
            raise OperationError("The column doesn't vary, so there are no outliers.")
        factor = p.factor or 3.0
        lower, upper = mean - factor * std, mean + factor * std
    col = pl.col(p.column)
    if p.action == "remove":
        return df.filter(col.is_null() | col.is_between(lower, upper)), [f"Bounds used: {lower:.4g} to {upper:.4g}."]
    clipped = col.cast(pl.Float64).clip(lower, upper)
    if series.dtype.is_integer():
        clipped = col.clip(int(-(-lower // 1)), int(upper // 1))  # stay integer: ceil(lower), floor(upper)
    return df.with_columns(clipped.alias(p.column)), [f"Bounds used: {lower:.4g} to {upper:.4g}."]


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class OperationSpec:
    params: type[Params]
    apply: Callable
    describe: Callable[[Params], str]


def _cols(columns: list[str] | None) -> str:
    if not columns:
        return "all columns"
    return ", ".join(f"'{c}'" for c in columns)


OPERATIONS: dict[str, OperationSpec] = {
    "drop_columns": OperationSpec(DropColumns, _drop_columns, lambda p: f"Drop {_cols(p.columns)}"),
    "rename_columns": OperationSpec(RenameColumns, _rename_columns,
                                    lambda p: "Rename " + ", ".join(f"'{a}' → '{b}'" for a, b in p.mapping.items())),
    "drop_duplicates": OperationSpec(DropDuplicates, _drop_duplicates,
                                     lambda p: f"Remove duplicate rows (compared on {_cols(p.columns)}, keep {p.keep})"),
    "drop_empty_columns": OperationSpec(NoParams, _drop_empty_columns, lambda p: "Drop completely empty columns"),
    "drop_constant_columns": OperationSpec(NoParams, _drop_constant_columns, lambda p: "Drop columns with a single value"),
    "drop_missing_rows": OperationSpec(DropMissingRows, _drop_missing_rows,
                                       lambda p: f"Drop rows where {'any' if p.how == 'any' else 'all'} of {_cols(p.columns)} {'is' if p.how == 'any' else 'are'} missing"),
    "fill_missing": OperationSpec(FillMissing, _fill_missing,
                                  lambda p: f"Fill missing values in {_cols(p.columns)} with "
                                  + {"mean": "the mean", "median": "the median", "mode": "the most common value",
                                     "constant": repr(p.value), "forward": "the previous value", "backward": "the next value"}[p.strategy]),
    "convert_type": OperationSpec(ConvertType, _convert_type, lambda p: f"Convert '{p.column}' to {p.to}"),
    "parse_formatted_number": OperationSpec(ParseFormattedNumber, _parse_formatted_number,
                                            lambda p: f"Strip formatting from '{p.column}' and convert to numbers"),
    "mark_missing": OperationSpec(MarkMissing, _mark_missing,
                                  lambda p: f"Treat {', '.join(repr(v) for v in p.values)} as missing in {_cols(p.columns)}"),
    "trim_whitespace": OperationSpec(TrimWhitespace, _trim_whitespace, lambda p: f"Trim spaces in {_cols(p.columns)}"),
    "change_case": OperationSpec(ChangeCase, _change_case, lambda p: f"Change {_cols(p.columns)} to {p.case} case"),
    "replace_values": OperationSpec(ReplaceValues, _replace_values,
                                    lambda p: f"Replace {p.find!r} with {p.replace!r} in '{p.column}' ({p.match} match)"),
    "round_numbers": OperationSpec(RoundNumbers, _round_numbers, lambda p: f"Round {_cols(p.columns)} to {p.decimals} decimal(s)"),
    "handle_outliers": OperationSpec(HandleOutliers, _handle_outliers,
                                     lambda p: f"{'Cap' if p.action == 'cap' else 'Remove'} outliers in '{p.column}' ({p.method})"),
}


def validate(op_type: str, params: dict | None) -> Params:
    spec = OPERATIONS.get(op_type)
    if spec is None:
        raise UnprocessableError(
            f"Unknown operation '{op_type}'.", code="unknown_operation", details={"available": sorted(OPERATIONS)}
        )
    try:
        return spec.params.model_validate(params or {})
    except ValidationError as exc:
        problems = [{"field": ".".join(str(p) for p in e["loc"]), "issue": e["msg"]} for e in exc.errors()]
        raise UnprocessableError("Invalid operation parameters.", code="invalid_parameters", details={"fields": problems}) from exc


# ---------------------------------------------------------------------------
# Running an operation and measuring its effect
# ---------------------------------------------------------------------------
@dataclass
class OperationResult:
    df: pl.DataFrame
    operation: dict
    description: str
    effect: dict
    warnings: list[str]
    samples: dict = field(default_factory=dict)


def run_operation(df: pl.DataFrame, op_type: str, params: dict | None, *, with_samples: bool = True) -> OperationResult:
    validated = validate(op_type, params)
    spec = OPERATIONS[op_type]
    before = df.with_row_index(ROW_ID)
    after, warnings = spec.apply(before, validated)

    before_cols = _data_columns(before)
    after_cols = _data_columns(after)
    renamed = validated.mapping if op_type == "rename_columns" else {}
    removed_cols = [c for c in before_cols if c not in after_cols and c not in renamed]
    added_cols = [c for c in after_cols if c not in before_cols and c not in renamed.values()]

    kept_ids = after.get_column(ROW_ID)
    removed_mask = ~before.get_column(ROW_ID).is_in(kept_ids.implode())
    rows_removed = int(removed_mask.sum())

    # Cell-level changes on rows and columns that survive (by row id, after renames).
    changed_per_column: dict[str, int] = {}
    dtype_changes: dict[str, dict] = {}
    changed_mask = None
    joined = None
    common = [c for c in before_cols if (renamed.get(c, c)) in after_cols]
    if common and after.height:
        left = before.filter(~removed_mask).select([ROW_ID] + common)
        right = after.select([ROW_ID] + [renamed.get(c, c) for c in common]).rename(
            {renamed.get(c, c): f"{c}__after" for c in common}
        )
        joined = left.join(right, on=ROW_ID, how="inner", maintain_order="left")
        for c in common:
            b, a = joined.get_column(c), joined.get_column(f"{c}__after")
            if b.dtype != a.dtype:
                if dtype_label(b.dtype) != dtype_label(a.dtype):
                    dtype_changes[renamed.get(c, c)] = {"from": dtype_label(b.dtype), "to": dtype_label(a.dtype)}
                if is_numeric(b.dtype) and is_numeric(a.dtype):
                    diff = b.cast(pl.Float64).ne_missing(a.cast(pl.Float64))
                else:
                    diff = b.cast(pl.String).ne_missing(_as_comparable(a, b))
            else:
                diff = b.ne_missing(a)
            count = int(diff.sum())
            if count:
                changed_per_column[renamed.get(c, c)] = count
                changed_mask = diff if changed_mask is None else (changed_mask | diff)

    effect = {
        "rows_before": before.height,
        "rows_after": after.height,
        "rows_removed": rows_removed,
        "columns_before": len(before_cols),
        "columns_after": len(after_cols),
        "columns_removed": removed_cols,
        "columns_added": added_cols,
        "columns_renamed": renamed,
        "cells_changed": sum(changed_per_column.values()),
        "changed_per_column": changed_per_column,
        "dtype_changes": dtype_changes,
        "no_change": not (rows_removed or removed_cols or added_cols or renamed or changed_per_column),
    }

    samples = {}
    if with_samples:
        if rows_removed:
            removed_rows = before.filter(removed_mask).head(SAMPLE_ROWS)
            cols = before_cols[:SAMPLE_MAX_COLUMNS]
            samples["removed_rows"] = {
                "columns": cols,
                "rows": [{"row": int(r[ROW_ID]) + 1, "values": to_json([r[c] for c in cols])} for r in removed_rows.iter_rows(named=True)],
            }
        if changed_mask is not None and joined is not None:
            changed_cols = [c for c in common if renamed.get(c, c) in changed_per_column][:SAMPLE_MAX_COLUMNS]
            rows = joined.filter(changed_mask).head(SAMPLE_ROWS)
            samples["changed_rows"] = {
                "columns": [renamed.get(c, c) for c in changed_cols],
                "rows": [
                    {
                        "row": int(r[ROW_ID]) + 1,
                        "before": to_json([r[c] for c in changed_cols]),
                        "after": to_json([r[f"{c}__after"] for c in changed_cols]),
                    }
                    for r in rows.iter_rows(named=True)
                ],
            }
    if effect["no_change"] and not warnings:
        warnings.append("This operation wouldn't change anything.")

    return OperationResult(
        df=after.drop(ROW_ID),
        operation={"type": op_type, "params": validated.model_dump(mode="json", exclude_defaults=False)},
        description=spec.describe(validated),
        effect=effect,
        warnings=warnings,
        samples=samples,
    )


def _as_comparable(after: pl.Series, before: pl.Series) -> pl.Series:
    """Compare across a type change by value, so '25' -> 25 doesn't count as a change but '?' -> null does."""
    if after.dtype.is_float() and before.dtype == pl.String:
        as_text = after.cast(pl.String)
        stripped_before = before.str.strip_chars()
        # 25.0 vs "25": treat numerically equal as unchanged
        same_number = stripped_before.cast(pl.Float64, strict=False) == after
        return pl.select(pl.when(same_number).then(before).otherwise(as_text)).to_series()
    if after.dtype.is_integer() and before.dtype == pl.String:
        same = before.str.strip_chars().cast(pl.Int64, strict=False) == after
        return pl.select(pl.when(same).then(before).otherwise(after.cast(pl.String))).to_series()
    if after.dtype == pl.Boolean and before.dtype == pl.String:
        return after.cast(pl.String)
    if is_temporal(after.dtype) and before.dtype == pl.String:
        same = before.str.strip_chars() == after.cast(pl.String)
        return pl.select(pl.when(same).then(before).otherwise(after.cast(pl.String))).to_series()
    return after.cast(pl.String)


def column_info(df: pl.DataFrame) -> list[dict]:
    nulls = df.select(pl.all().null_count()).row(0) if df.width else []
    return [
        {"name": n, "dtype": dtype_label(t), "null_count": int(c), "converted_from_text": False, "missing_markers": {}}
        for n, t, c in zip(df.columns, df.dtypes, nulls)
    ]
