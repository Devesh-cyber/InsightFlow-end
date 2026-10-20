"""
Generic validity checks: find values that look WRONG (not just unusual), on any dataset.

Every check is driven by the column's own pattern, never by its name:
  - repeated IDs:       a column where almost every value is unique, but a few repeat
  - pattern breakers:   a column that's mostly emails, with a few that aren't
  - spelling variants:  values identical except for case or spaces (Mumbai / MUMBAI)
  - impossible signs:   negatives (or zeros) in a column that is otherwise never negative (or always >= 1)
  - broken arithmetic:  C = A × B or C = A + B holds in almost every row, except a few

Checks only REPORT. Each comes with options the user can preview; none is applied
automatically and none is "recommended" unless the fix is deterministic.
"""

import itertools

import polars as pl

from app.engine.infer import DATE_ISO_RE
from app.engine.profile import column_role, is_numeric, pct
from app.engine.rules import EMAIL_RE

MIN_ROWS = 20                 # below this, patterns are too weak to call anything "wrong"
NEAR_UNIQUE = 0.9             # share of distinct values for a column to look like an identifier
PATTERN_SHARE = 0.8           # share of values that must follow a pattern before breakers are flagged
RARE_SHARE = 0.05             # "a few": at most 5% of values may break the pattern
RELATION_SHARE = 0.9          # share of complete rows where C = A op B must hold
MAX_RELATION_COLUMNS = 10
EXAMPLES = 5


def _option(label: str, op_type: str, params: dict, recommended: bool = False, note: str | None = None) -> dict:
    return {"label": label, "operation": {"type": op_type, "params": params}, "recommended": recommended, "note": note}


def _rule(conditions: list[dict], action: str, **extra) -> dict:
    return {"conditions": conditions, "combine": "and", "action": action, **extra}


def _rows(mask: pl.Series, limit: int = EXAMPLES) -> list[int]:
    return [i + 1 for i in mask.arg_true().head(limit).to_list()]


def _looks_like_dates(text: pl.Series) -> bool:
    values = text.drop_nulls()
    return len(values) > 0 and float(values.str.strip_chars().str.contains(DATE_ISO_RE).mean()) >= PATTERN_SHARE


# ---------------------------------------------------------------------------
def repeated_ids(df: pl.DataFrame) -> list[dict]:
    issues = []
    for name, dtype in zip(df.columns, df.dtypes):
        if not (dtype == pl.String or dtype.is_integer()):
            continue
        series = df.get_column(name)
        values = series.drop_nulls()
        n, distinct = len(values), values.n_unique()
        if n < MIN_ROWS or distinct == n or distinct < NEAR_UNIQUE * n:
            continue
        if dtype == pl.String:
            if _looks_like_dates(values):
                continue  # dates repeat naturally
            if float(values.str.contains(r"\s").mean()) > 0.5:
                continue  # sentences / full names: repeats are normal
        counts = values.value_counts(sort=True)
        repeated = counts.filter(pl.col("count") > 1)
        rows = int(repeated["count"].sum())
        examples = [{"value": v, "count": int(c)} for v, c in repeated.head(EXAMPLES).iter_rows()]
        shown = ", ".join(f"{e['value']!r} ×{e['count']}" for e in examples[:3])
        issues.append({
            "id": f"repeated_ids:{name}", "category": "duplicate_id", "severity": "warning", "column": name,
            "title": f"{repeated.height} repeated value(s) in '{name}'",
            "message": f"'{name}' is almost unique ({distinct} different values in {n} rows), which suggests it identifies "
                       f"each row, but some values repeat: {shown}. If every row should have its own '{name}', check these "
                       "rows; they may be duplicates or typing mistakes.",
            "evidence": {"repeated_values": repeated.height, "rows_involved": rows, "examples": examples,
                         "rows": _rows(series.is_duplicated() & series.is_not_null())},
            "options": [
                _option("Keep the first row for each value", "drop_duplicates", {"columns": [name], "keep": "first"},
                        note=f"Removes {rows - repeated.height} row(s). Only if the repeats are true duplicates."),
            ],
        })
    return issues


def pattern_breakers(df: pl.DataFrame) -> list[dict]:
    """Columns that are mostly emails, with a few values that aren't."""
    issues = []
    for name, dtype in zip(df.columns, df.dtypes):
        if dtype != pl.String:
            continue
        text = df.get_column(name).str.strip_chars()
        present = text.is_not_null() & (text != "")
        values = text.filter(present)
        if len(values) < MIN_ROWS:
            continue
        valid = values.str.contains(EMAIL_RE)
        share = float(valid.mean())
        bad = int((~valid).sum())
        if share < PATTERN_SHARE or bad == 0 or bad > RARE_SHARE * len(values) + 1:
            continue
        examples = values.filter(~valid).value_counts(sort=True).head(EXAMPLES)
        shown = ", ".join(repr(v) for v, _ in examples.iter_rows())
        condition = [{"column": name, "op": "invalid_email"}]
        issues.append({
            "id": f"invalid_email:{name}", "category": "invalid_values", "severity": "warning", "column": name,
            "title": f"{bad} value(s) in '{name}' aren't valid emails",
            "message": f"{pct(int(valid.sum()), len(values))}% of '{name}' are email addresses, but {bad} aren't: {shown}. "
                       "A placeholder like 'Unknown' is not an email either.",
            "evidence": {"invalid": bad, "examples": [v for v, _ in examples.iter_rows()],
                         "rows": _rows((present & ~text.str.contains(EMAIL_RE)).fill_null(False))},
            "options": [
                _option("Mark them as missing", "apply_rule", _rule(condition, "set_missing", target=name),
                        note="Keeps the rows; the invalid values become empty."),
                _option("Remove those rows", "apply_rule", _rule(condition, "remove_rows"), note=f"Removes {bad} row(s)."),
            ],
        })
    return issues


def spelling_variants(df: pl.DataFrame) -> list[dict]:
    issues = []
    for name, dtype in zip(df.columns, df.dtypes):
        if dtype != pl.String:
            continue
        values = df.get_column(name).drop_nulls()
        if len(values) < 2:
            continue
        distinct = values.n_unique()
        if distinct > max(50, 0.5 * len(values)):
            continue  # free text: spelling variety is expected
        counts = values.value_counts(sort=True)
        frame = counts.with_columns(pl.col(name).str.strip_chars().str.to_lowercase().alias("key"))
        groups = frame.group_by("key").agg(pl.col(name), pl.col("count"), pl.len().alias("spellings")).filter(pl.col("spellings") > 1)
        if groups.height == 0:
            continue
        mapping: dict[str, str] = {}
        described = []
        for _key, spellings, numbers, _ in groups.sort("key").iter_rows():
            ranked = sorted(zip(spellings, numbers), key=lambda x: (-x[1], x[0]))
            canonical = ranked[0][0].strip()
            for spelling, _count in ranked:
                if spelling != canonical:
                    mapping[spelling] = canonical
            described.append(", ".join(f"{s!r} ({c})" for s, c in ranked))
        issues.append({
            "id": f"spelling:{name}", "category": "inconsistent_spelling", "severity": "info", "column": name,
            "title": f"'{name}' has {groups.height} value(s) spelled more than one way",
            "message": f"These differ only by capital letters or spaces, so they're counted as different categories: "
                       f"{'; '.join(described[:3])}{' …' if len(described) > 3 else ''}.",
            "evidence": {"groups": groups.height, "mapping": mapping},
            "options": [
                _option("Use the most common spelling for each", "standardize_values", {"column": name, "mapping": mapping},
                        recommended=True, note="; ".join(f"{a!r} → {b!r}" for a, b in list(mapping.items())[:6])),
            ],
        })
    return issues


def impossible_signs(df: pl.DataFrame) -> list[dict]:
    issues = []
    rows = df.height
    for name, dtype in zip(df.columns, df.dtypes):
        if not is_numeric(dtype):
            continue
        series = df.get_column(name)
        values = series.drop_nulls()
        n = len(values)
        if n < MIN_ROWS or column_role(dtype, rows, n, values.n_unique()) != "numeric":
            continue
        negatives = int((values < 0).sum())
        if 0 < negatives <= RARE_SHARE * n:
            condition = [{"column": name, "op": "lt", "value": 0}]
            examples = sorted(values.filter(values < 0).unique().head(EXAMPLES).to_list())
            issues.append({
                "id": f"negative:{name}", "category": "invalid_values", "severity": "warning", "column": name,
                "title": f"{negatives} negative value(s) in '{name}'",
                "message": f"'{name}' is never negative except {negatives} value(s): {examples}. If it can't be negative "
                           "(an age, a price, a count), these are errors.",
                "evidence": {"count": negatives, "examples": examples, "rows": _rows((series < 0).fill_null(False))},
                "options": [
                    _option("Mark them as missing", "apply_rule", _rule(condition, "set_missing", target=name)),
                    _option("Remove those rows", "apply_rule", _rule(condition, "remove_rows"), note=f"Removes {negatives} row(s)."),
                ],
            })
        positive = values.filter(values >= 0)
        zeros = int((values == 0).sum())
        if (dtype.is_integer() and 0 < zeros <= RARE_SHARE * n and len(positive) and
                int((positive > 0).sum()) == len(positive) - zeros and float(positive.filter(positive > 0).min()) >= 1):
            condition = [{"column": name, "op": "eq", "value": 0}]
            issues.append({
                "id": f"zero:{name}", "category": "invalid_values", "severity": "warning", "column": name,
                "title": f"{zeros} zero(s) in '{name}'",
                "message": f"'{name}' is always at least 1 except {zeros} zero(s). If zero isn't a valid amount here "
                           "(e.g. quantity ordered), check these rows.",
                "evidence": {"count": zeros, "rows": _rows((series == 0).fill_null(False))},
                "options": [
                    _option("Mark them as missing", "apply_rule", _rule(condition, "set_missing", target=name)),
                    _option("Remove those rows", "apply_rule", _rule(condition, "remove_rows"), note=f"Removes {zeros} row(s)."),
                ],
            })
    return issues


# ---------------------------------------------------------------------------
# Arithmetic relationships between columns
# ---------------------------------------------------------------------------
def _relation_candidates(df: pl.DataFrame) -> list[str]:
    columns = []
    for name, dtype in zip(df.columns, df.dtypes):
        if not is_numeric(dtype):
            continue
        values = df.get_column(name).drop_nulls()
        if len(values) < MIN_ROWS or column_role(dtype, df.height, len(values), values.n_unique()) != "numeric":
            continue
        columns.append(name)
    columns.sort(key=lambda c: df.get_column(c).null_count())
    return columns[:MAX_RELATION_COLUMNS]


def _mismatch(df_cols: dict, target: str, a: str, b: str, op: str) -> pl.Expr:
    c, x, y = pl.col(target).cast(pl.Float64), pl.col(a).cast(pl.Float64), pl.col(b).cast(pl.Float64)
    expected = x * y if op == "*" else x + y
    tolerance = pl.max_horizontal(pl.lit(0.011), expected.abs() * 1e-9)
    return (c - expected).abs() > tolerance


def find_relationships(df: pl.DataFrame, involving: set[str] | None = None) -> list[dict]:
    """Relationships C = A op B that hold in >= 90% of complete rows (op is * or +)."""
    columns = _relation_candidates(df)
    if len(columns) < 3:
        return []
    exprs, keys = [], []
    for target in columns:
        for a, b in itertools.combinations([c for c in columns if c != target], 2):
            if involving is not None and not ({target, a, b} & involving):
                continue
            complete = pl.col(target).is_not_null() & pl.col(a).is_not_null() & pl.col(b).is_not_null()
            for op in ("*", "+"):
                bad = _mismatch({}, target, a, b, op) & complete
                exprs += [complete.sum().alias(f"n{len(keys)}"), bad.sum().alias(f"b{len(keys)}")]
                keys.append((target, a, b, op))
    if not keys:
        return []
    counts = df.select(exprs).row(0)
    found = []
    for i, (target, a, b, op) in enumerate(keys):
        complete, bad = counts[2 * i], counts[2 * i + 1]
        if complete >= MIN_ROWS and (complete - bad) >= RELATION_SHARE * complete:
            found.append({"target": target, "a": a, "b": b, "op": op, "complete": int(complete), "violations": int(bad)})
    # Keep the strongest explanation per target column.
    best: dict[str, dict] = {}
    for r in found:
        if r["target"] not in best or r["violations"] < best[r["target"]]["violations"]:
            best[r["target"]] = r
    return list(best.values())


def relation_formula(r: dict) -> list:
    return [{"column": r["a"]}, r["op"], {"column": r["b"]}]


def relation_text(r: dict) -> str:
    return f"{r['a']} {'×' if r['op'] == '*' else '+'} {r['b']}"


def violation_mask(df: pl.DataFrame, r: dict) -> pl.Series:
    complete = pl.col(r["target"]).is_not_null() & pl.col(r["a"]).is_not_null() & pl.col(r["b"]).is_not_null()
    return df.select((_mismatch({}, r["target"], r["a"], r["b"], r["op"]) & complete).alias("m")).to_series()


def broken_relationships(df: pl.DataFrame) -> list[dict]:
    issues = []
    for r in find_relationships(df):
        if not r["violations"]:
            continue
        mask = violation_mask(df, r)
        formula = relation_formula(r)
        # same tolerance as the detector, so "Show rows" and the fixes cover exactly the flagged rows
        condition = [{"column": r["target"], "op": "differs_from", "formula": formula, "tolerance": 0.011}]
        issues.append({
            "id": f"relationship:{r['target']}", "category": "broken_relationship", "severity": "warning", "column": r["target"],
            "title": f"'{r['target']}' ≠ {relation_text(r)} in {r['violations']} row(s)",
            "message": f"In {r['complete'] - r['violations']} of {r['complete']} rows, {r['target']} = {relation_text(r)}. "
                       f"These {r['violations']} row(s) don't follow it, so one of the three values is probably wrong.",
            "evidence": {**r, "rows": _rows(mask)},
            "options": [
                _option(f"Recalculate {r['target']} = {relation_text(r)} in those rows", "apply_rule",
                        _rule(condition, "calculate", target=r["target"], formula=formula),
                        note=f"Only if {r['a']} and {r['b']} are right in those rows."),
                _option("Remove those rows", "apply_rule", _rule(condition, "remove_rows"), note=f"Removes {r['violations']} row(s)."),
            ],
        })
    return issues


def find_validity_issues(df: pl.DataFrame) -> list[dict]:
    if df.height == 0:
        return []
    return repeated_ids(df) + pattern_breakers(df) + spelling_variants(df) + impossible_signs(df) + broken_relationships(df)


def relationship_damage(before: pl.DataFrame, after: pl.DataFrame, changed: set[str]) -> list[str]:
    """Warnings for a preview: does this step break a relationship that held before?"""
    notes = []
    for r in find_relationships(before, involving=changed):
        if not {r["target"], r["a"], r["b"]} <= set(after.columns):
            continue
        try:
            now = int(violation_mask(after, r).sum())
        except pl.exceptions.PolarsError:
            continue
        if now > r["violations"]:
            notes.append(f"This step makes {r['target']} ≠ {relation_text(r)} in {now - r['violations']} more row(s) "
                         f"({now} in total). That relationship holds in almost every other row.")
    return notes
