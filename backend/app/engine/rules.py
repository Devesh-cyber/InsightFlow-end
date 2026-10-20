"""
Custom rules: "if <conditions> then <action>", defined by the user.

One module owns the meaning of every operator, so the same rule behaves
identically in three places:
  - the `apply_rule` cleaning step (engine/operations.py)
  - saved checks that are re-evaluated on every version (services/checks.py)
  - the pandas recipe (engine/recipe.py uses the *_code functions below)

Semantics worth knowing (also shown in the UI):
  - A missing value never matches a comparison (age < 0 is false when age is
    missing). Use "is empty" to target missing values.
  - Formulas only allow + - * / between numeric columns and numbers, with the
    usual precedence. Division by zero gives a missing value, never infinity.
"""

import datetime
import math
from typing import Any, Literal

import polars as pl
from pydantic import BaseModel, ConfigDict, Field, model_validator

from app.core.errors import UnprocessableError
from app.engine.infer import FLOAT_RE, dtype_label
from app.engine.profile import is_numeric, is_temporal

MAX_CONDITIONS = 10
MAX_TERMS = 19  # 10 operands, 9 operators
CALC_DECIMALS = 10  # calculated decimals are rounded to this, in the engine and the recipe
EMAIL_RE = r"^[^@\s]+@[^@\s]+\.[^@\s]+$"
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d-%m-%Y", "%Y/%m/%d", "%d.%m.%Y"]

NUMERIC_COMPARE = {"lt", "le", "gt", "ge", "between", "not_between"}
TEXT_OPS = {"contains", "not_contains", "starts_with", "ends_with", "in", "not_in"}
ValueFree = {"is_empty", "not_empty", "duplicate", "invalid_number", "invalid_email", "extra_spaces"}

Operator = Literal[
    "eq", "ne", "lt", "le", "gt", "ge", "between", "not_between",
    "contains", "not_contains", "starts_with", "ends_with", "in", "not_in",
    "is_empty", "not_empty", "duplicate", "extra_spaces",
    "invalid_number", "invalid_date", "invalid_email",
    "differs_from",
]

OP_WORDS = {
    "eq": "is", "ne": "is not", "lt": "<", "le": "≤", "gt": ">", "ge": "≥",
    "between": "is between", "not_between": "is not between",
    "contains": "contains", "not_contains": "does not contain", "starts_with": "starts with", "ends_with": "ends with",
    "in": "is one of", "not_in": "is not one of", "is_empty": "is empty", "not_empty": "is not empty",
    "duplicate": "is repeated", "extra_spaces": "has spaces at the start or end", "invalid_number": "is not a valid number", "invalid_date": "is not a valid date",
    "invalid_email": "is not a valid email", "differs_from": "differs from",
}


class RuleError(UnprocessableError):
    code = "invalid_rule"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Operand(_Strict):
    column: str | None = None
    number: float | None = None

    @model_validator(mode="after")
    def _one(self):
        if (self.column is None) == (self.number is None):
            raise ValueError("an operand is either a column or a number")
        if self.number is not None and not math.isfinite(self.number):
            raise ValueError("numbers must be finite")
        return self


FormulaTerm = Operand | Literal["+", "-", "*", "/"]


def _check_formula(terms: list) -> list:
    if not terms or len(terms) % 2 == 0 or len(terms) > MAX_TERMS:
        raise ValueError("a formula alternates value, operator, value, … (1 to 10 values)")
    for i, term in enumerate(terms):
        if (i % 2 == 0) != isinstance(term, Operand):
            raise ValueError("a formula alternates value, operator, value, …")
    return terms


class Condition(_Strict):
    column: str = Field(min_length=1, max_length=255)
    op: Operator
    value: Any = None
    value2: Any = None
    ignore_case: bool = False
    formula: list[FormulaTerm] | None = None  # differs_from
    tolerance: float = Field(default=0.01, ge=0)

    @model_validator(mode="after")
    def _shape(self):
        if self.op == "differs_from":
            if not self.formula:
                raise ValueError("'differs from' needs a formula")
            _check_formula(self.formula)
        elif self.op in ("between", "not_between"):
            if self.value is None or self.value2 is None:
                raise ValueError("'between' needs two values")
        elif self.op in ("in", "not_in"):
            if not isinstance(self.value, list) or not self.value or len(self.value) > 500:
                raise ValueError("'is one of' needs a list of 1 to 500 values")
        elif self.op == "invalid_date":
            if self.value is not None and self.value not in DATE_FORMATS:
                raise ValueError(f"date format must be one of {DATE_FORMATS}")
        elif self.op not in ValueFree and self.value is None:
            raise ValueError(f"'{OP_WORDS[self.op]}' needs a value")
        return self


class ConditionGroup(_Strict):
    conditions: list[Condition] = Field(default_factory=list, max_length=MAX_CONDITIONS)
    combine: Literal["and", "or"] = "and"


# ---------------------------------------------------------------------------
# Formulas
# ---------------------------------------------------------------------------
def _operand_expr(df: pl.DataFrame, term: Operand) -> pl.Expr:
    if term.column is None:
        return pl.lit(term.number, dtype=pl.Float64)
    _require(df, [term.column])
    if not is_numeric(df.get_column(term.column).dtype):
        raise RuleError(f"'{term.column}' is {dtype_label(df.get_column(term.column).dtype)}; formulas need number columns.")
    return pl.col(term.column)


def formula_expr(df: pl.DataFrame, terms: list) -> pl.Expr:
    """Standard precedence: * and / first, then + and -, left to right."""
    _check_formula(terms)
    # First pass: fold * and / into products.
    sums: list = [_operand_expr(df, terms[0])]
    ops: list[str] = []
    for i in range(1, len(terms), 2):
        op, right = terms[i], _operand_expr(df, terms[i + 1])
        if op in ("*", "/"):
            left = sums.pop()
            if op == "*":
                sums.append(left * right)
            else:
                sums.append(pl.when(right == 0).then(None).otherwise(left / right))
        else:
            ops.append(op)
            sums.append(right)
    result = sums[0]
    for op, part in zip(ops, sums[1:]):
        result = result + part if op == "+" else result - part
    return result


def formula_text(terms: list) -> str:
    out = []
    for i, t in enumerate(terms):
        if i % 2:
            out.append({"*": "×", "/": "÷"}.get(t, t))
        else:
            out.append(t.column if t.column is not None else _num(t.number))
    return " ".join(out)


def _num(v: float) -> str:
    return str(int(v)) if float(v).is_integer() else repr(v)


# ---------------------------------------------------------------------------
# Conditions -> boolean mask
# ---------------------------------------------------------------------------
def _require(df: pl.DataFrame, columns: list[str]) -> None:
    missing = [c for c in columns if c not in df.columns]
    if missing:
        raise UnprocessableError(
            f"Column(s) not found: {', '.join(repr(c) for c in missing)}.", code="column_not_found", details={"columns": missing}
        )


def _literal_for(dtype: pl.DataType, value: Any, column: str) -> Any:
    """Turn a user value into something comparable with the column."""
    if is_numeric(dtype):
        try:
            number = float(value)
        except (TypeError, ValueError):
            raise RuleError(f"'{column}' holds numbers, so {value!r} must be a number.") from None
        if not math.isfinite(number):
            raise RuleError("Numbers must be finite.")
        return number
    if dtype == pl.Date:
        try:
            return datetime.date.fromisoformat(str(value))
        except ValueError:
            raise RuleError(f"'{column}' holds dates; write {value!r} as YYYY-MM-DD.") from None
    if isinstance(dtype, pl.Datetime):
        try:
            return datetime.datetime.fromisoformat(str(value))
        except ValueError:
            raise RuleError(f"'{column}' holds date-times; write {value!r} as YYYY-MM-DD HH:MM.") from None
    if dtype == pl.Boolean:
        text = str(value).strip().lower()
        if text in ("true", "yes", "1"):
            return True
        if text in ("false", "no", "0"):
            return False
        raise RuleError(f"'{column}' holds true/false values.")
    return str(value)


def _text(col: pl.Expr, dtype: pl.DataType, ignore_case: bool) -> pl.Expr:
    text = col if dtype == pl.String else col.cast(pl.String)
    return text.str.to_lowercase() if ignore_case else text


def _condition_expr(df: pl.DataFrame, c: Condition) -> pl.Expr:
    _require(df, [c.column])
    dtype = df.get_column(c.column).dtype
    col = pl.col(c.column)

    if c.op == "is_empty":
        expr = col.is_null()
        if dtype == pl.String:
            expr = expr | (col.str.strip_chars() == "")
        return expr
    if c.op == "not_empty":
        expr = col.is_not_null()
        if dtype == pl.String:
            expr = expr & (col.str.strip_chars() != "")
        return expr
    if c.op == "duplicate":
        key = _text(col, dtype, c.ignore_case) if dtype == pl.String else col
        return col.is_not_null() & key.is_duplicated()
    if c.op == "extra_spaces":
        if dtype != pl.String:
            return pl.lit(False)
        return (col != col.str.strip_chars()).fill_null(False)
    if c.op == "invalid_number":
        if is_numeric(dtype):
            return pl.lit(False)
        text = col.cast(pl.String).str.strip_chars()
        return text.is_not_null() & (text != "") & ~text.str.contains(FLOAT_RE)
    if c.op == "invalid_date":
        if is_temporal(dtype):
            return pl.lit(False)
        text = col.cast(pl.String).str.strip_chars()
        parsed = text.str.to_date(c.value or DATE_FORMATS[0], strict=False)
        return text.is_not_null() & (text != "") & parsed.is_null()
    if c.op == "invalid_email":
        text = col.cast(pl.String).str.strip_chars()
        return text.is_not_null() & (text != "") & ~text.str.contains(EMAIL_RE)
    if c.op == "differs_from":
        if not is_numeric(dtype):
            raise RuleError(f"'{c.column}' must hold numbers to compare with a formula.")
        return ((col - formula_expr(df, c.formula)).abs() > c.tolerance).fill_null(False)

    if c.op in NUMERIC_COMPARE:
        if not (is_numeric(dtype) or is_temporal(dtype)):
            raise RuleError(
                f"'{c.column}' is {dtype_label(dtype)}. Comparing with <, > or 'between' needs a number or date column "
                "(use 'Change column type' first)."
            )
        a = _literal_for(dtype, c.value, c.column)
        if c.op in ("between", "not_between"):
            b = _literal_for(dtype, c.value2, c.column)
            lo, hi = (a, b) if a <= b else (b, a)
            inside = col.is_between(lo, hi)
            return (inside if c.op == "between" else ~inside).fill_null(False)
        compare = {"lt": col < a, "le": col <= a, "gt": col > a, "ge": col >= a}[c.op]
        return compare.fill_null(False)

    if c.op in ("eq", "ne"):
        if dtype == pl.String or not (is_numeric(dtype) or is_temporal(dtype) or dtype == pl.Boolean):
            text, value = _text(col, dtype, c.ignore_case), str(c.value)
            value = value.lower() if c.ignore_case else value
            expr = text == value if c.op == "eq" else text != value
        else:
            value = _literal_for(dtype, c.value, c.column)
            expr = col == value if c.op == "eq" else col != value
        return expr.fill_null(False)

    # Text operators work on any column (numbers are compared as their text).
    text = _text(col, dtype, c.ignore_case)
    if c.op in ("in", "not_in"):
        values = [str(v).lower() if c.ignore_case else str(v) for v in c.value]
        expr = text.is_in(values)
        return (expr if c.op == "in" else ~expr).fill_null(False)
    needle = str(c.value).lower() if c.ignore_case else str(c.value)
    expr = {
        "contains": text.str.contains(needle, literal=True),
        "not_contains": ~text.str.contains(needle, literal=True),
        "starts_with": text.str.starts_with(needle),
        "ends_with": text.str.ends_with(needle),
    }[c.op]
    return expr.fill_null(False)


def mask_expr(df: pl.DataFrame, conditions: list[Condition], combine: str) -> pl.Expr:
    exprs = [_condition_expr(df, c) for c in conditions]
    result = exprs[0]
    for e in exprs[1:]:
        result = (result & e) if combine == "and" else (result | e)
    return result


def evaluate(df: pl.DataFrame, conditions: list[Condition], combine: str) -> pl.Series:
    return df.select(mask_expr(df, conditions, combine).alias("m")).to_series().fill_null(False)


def describe_conditions(conditions: list[Condition], combine: str) -> str:
    parts = []
    for c in conditions:
        if c.op in ValueFree or (c.op == "invalid_date" and c.value is None):
            parts.append(f"{c.column} {OP_WORDS[c.op]}")
        elif c.op == "invalid_date":
            parts.append(f"{c.column} is not a valid date ({c.value})")
        elif c.op in ("between", "not_between"):
            parts.append(f"{c.column} {OP_WORDS[c.op]} {c.value} and {c.value2}")
        elif c.op in ("in", "not_in"):
            shown = ", ".join(repr(v) for v in c.value[:5]) + (", …" if len(c.value) > 5 else "")
            parts.append(f"{c.column} {OP_WORDS[c.op]} {shown}")
        elif c.op == "differs_from":
            parts.append(f"{c.column} ≠ {formula_text(c.formula)}")
        else:
            value = c.value if isinstance(c.value, (int, float)) and not isinstance(c.value, bool) else repr(c.value)
            parts.append(f"{c.column} {OP_WORDS[c.op]} {value}")
        if c.ignore_case and c.op in TEXT_OPS | {"eq", "ne", "duplicate"}:
            parts[-1] += " (any case)"
    return f" {combine.upper()} ".join(parts)


# ---------------------------------------------------------------------------
# pandas code generation (for the recipe). Mirrors the semantics above.
# ---------------------------------------------------------------------------
def _pd_col(name: str) -> str:
    return f"df[{name!r}]"


def formula_code(terms: list) -> str:
    """Python precedence matches ours; division uses safe_div so x/0 is missing, like InsightFlow."""
    def operand(t):
        return _pd_col(t.column) if t.column is not None else _num(t.number)

    # Build products first so division can be wrapped.
    sums: list[str] = [operand(terms[0])]
    ops: list[str] = []
    for i in range(1, len(terms), 2):
        op, right = terms[i], operand(terms[i + 1])
        if op == "*":
            sums[-1] = f"{sums[-1]} * {right}"
        elif op == "/":
            sums[-1] = f"safe_div({sums[-1]}, {right})"
        else:
            ops.append(op)
            sums.append(right)
    code = sums[0]
    for op, part in zip(ops, sums[1:]):
        code = f"{code} {op} {part}"
    return f"({code})"


def needs_safe_div(conditions: list[Condition] | None, formula: list | None) -> bool:
    terms = list(formula or [])
    for c in conditions or []:
        terms += list(c.formula or [])
    return "/" in [t for t in terms if isinstance(t, str)]


def _pd_text(col: str, ignore_case: bool) -> str:
    text = f'{col}.astype("string")'
    return f"{text}.str.lower()" if ignore_case else text


def _pd_literal(value: Any, kind: str) -> str:
    if kind == "number":
        return _num(float(value))
    if kind == "date":
        return f"pd.Timestamp({str(value)!r})"
    if kind == "bool":
        return repr(str(value).strip().lower() in ("true", "yes", "1"))
    return repr(str(value))


def condition_code(c: Condition, kind: str) -> str:
    """kind = 'number' | 'date' | 'bool' | 'text' (the column's type at this step)."""
    col = _pd_col(c.column)
    if c.op == "is_empty":
        return f'({col}.isna() | ({col}.astype("string").str.strip() == ""))' if kind == "text" else f"{col}.isna()"
    if c.op == "not_empty":
        return f'({col}.notna() & ({col}.astype("string").str.strip() != ""))' if kind == "text" else f"{col}.notna()"
    if c.op == "duplicate":
        key = _pd_text(col, c.ignore_case) if kind == "text" else col
        return f"({col}.notna() & {key}.duplicated(keep=False))"
    if c.op == "extra_spaces":
        if kind != "text":
            return "pd.Series(False, index=df.index)"
        return f'({col}.astype("string") != {col}.astype("string").str.strip())'
    if c.op in ("invalid_number", "invalid_email", "invalid_date"):
        if (c.op == "invalid_number" and kind == "number") or (c.op == "invalid_date" and kind == "date"):
            return "pd.Series(False, index=df.index)"
        text = f'{col}.astype("string").str.strip()'
        filled = f"{text}.fillna(\"\")"
        if c.op == "invalid_date":
            check = f'pd.to_datetime({text}, format={(c.value or DATE_FORMATS[0])!r}, errors="coerce").isna()'
        else:
            pattern = FLOAT_RE if c.op == "invalid_number" else EMAIL_RE
            check = f"~{filled}.str.contains({pattern!r}, regex=True)"
        return f'({col}.notna() & ({filled} != "") & {check})'
    if c.op == "differs_from":
        return f"(({col} - {formula_code(c.formula)}).abs() > {c.tolerance!r})"
    if c.op in NUMERIC_COMPARE:
        lit_kind = "date" if kind == "date" else "number"
        a = _pd_literal(c.value, lit_kind)
        if c.op in ("between", "not_between"):
            b = _pd_literal(c.value2, lit_kind)
            lo, hi = (a, b)
            if lit_kind == "number" and float(c.value) > float(c.value2):
                lo, hi = b, a
            inside = f"{col}.between({lo}, {hi})"
            return inside if c.op == "between" else f"({col}.notna() & ~{inside})"
        return f"({col} {OP_WORDS[c.op].replace('≤', '<=').replace('≥', '>=')} {a})"
    if c.op in ("eq", "ne"):
        if kind == "text":
            value = str(c.value).lower() if c.ignore_case else str(c.value)
            return f"({_pd_text(col, c.ignore_case)} {'==' if c.op == 'eq' else '!='} {value!r})"
        return f"({col} {'==' if c.op == 'eq' else '!='} {_pd_literal(c.value, kind)})"
    text = _pd_text(col, c.ignore_case)
    if c.op in ("in", "not_in"):
        values = [str(v).lower() if c.ignore_case else str(v) for v in c.value]
        inside = f"{text}.isin({values!r})"
        return inside if c.op == "in" else f"({col}.notna() & ~{inside})"
    needle = str(c.value).lower() if c.ignore_case else str(c.value)
    return {
        "contains": f"{text}.str.contains({needle!r}, regex=False)",
        "not_contains": f"~{text}.str.contains({needle!r}, regex=False)",
        "starts_with": f"{text}.str.startswith({needle!r})",
        "ends_with": f"{text}.str.endswith({needle!r})",
    }[c.op]


def mask_code(conditions: list[Condition], combine: str, kinds: dict[str, str]) -> str:
    joiner = " & " if combine == "and" else " | "
    parts = [f"({condition_code(c, kinds.get(c.column, 'text'))}).fillna(False).astype(bool)" for c in conditions]
    return joiner.join(parts)


def kind_of(dtype_label_value: str) -> str:
    return {"integer": "number", "float": "number", "date": "date", "datetime": "date", "boolean": "bool"}.get(dtype_label_value, "text")

