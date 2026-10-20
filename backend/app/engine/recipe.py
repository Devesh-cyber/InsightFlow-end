"""
"Python recipe": a pandas script that reproduces the cleaning steps.

Learners can see exactly what each click did, in the library they're learning,
and re-run it on next month's file. Generated from the version history, following
reverts, so it reflects the steps that actually produced the current version.
"""

import math

from app.db.models import DatasetRecord, VersionRecord
from app.engine.infer import STANDARD_MISSING
from app.engine.rules import CALC_DECIMALS, Condition, Operand, formula_code, kind_of, mask_code


def lineage(versions: dict[int, VersionRecord], current: int) -> tuple[list[VersionRecord], VersionRecord | None, bool]:
    """Walk back from `current`: returns (cleaning steps oldest-first, base version, complete?)."""
    steps: list[VersionRecord] = []
    number = current
    seen: set[int] = set()
    while number in versions and number not in seen:
        seen.add(number)
        version = versions[number]
        op_type = (version.operation or {}).get("type")
        if op_type == "revert":
            number = version.operation["params"]["to_version"]
            continue
        if op_type in ("upload", "select_sheet"):
            return list(reversed(steps)), version, True
        steps.append(version)
        number -= 1
    return list(reversed(steps)), None, False


def _q(value) -> str:
    return repr(value)


def _cols(columns) -> str:
    return "[" + ", ".join(_q(c) for c in columns) + "]"


def _step_code(op: dict, dtypes: dict[str, str] | None = None, after: dict[str, str] | None = None) -> list[str]:
    """dtypes = column types before this step, after = column types after it (InsightFlow labels)."""
    t, p = op["type"], op.get("params") or {}
    dtypes, after = dtypes or {}, after or {}
    if t == "drop_columns":
        return [f"df = df.drop(columns={_cols(p['columns'])})"]
    if t == "rename_columns":
        return [f"df = df.rename(columns={p['mapping']!r})"]
    if t == "drop_duplicates":
        subset = _cols(p["columns"]) if p.get("columns") else "None"
        return [f"df = df.drop_duplicates(subset={subset}, keep={p.get('keep', 'first')!r})"]
    if t == "drop_empty_columns":
        return ['df = df.dropna(axis="columns", how="all")']
    if t == "drop_constant_columns":
        return ["df = df.loc[:, df.nunique(dropna=True) != 1]"]
    if t == "drop_missing_rows":
        subset = _cols(p["columns"]) if p.get("columns") else "None"
        return [f"df = df.dropna(how={p.get('how', 'any')!r}, subset={subset})"]
    if t == "fill_missing":
        lines = []
        for c in p["columns"]:
            s = p["strategy"]
            fill = {
                "mean": f"df[{_q(c)}].mean()",
                "median": f"df[{_q(c)}].median()",
                "mode": f"df[{_q(c)}].mode().sort_values().iloc[0]",
                "constant": _q(p.get("value")),
            }.get(s)
            if fill:
                lines.append(f"df[{_q(c)}] = df[{_q(c)}].fillna({fill})")
            else:
                lines.append(f"df[{_q(c)}] = df[{_q(c)}].{'ffill' if s == 'forward' else 'bfill'}()")
        return lines
    if t == "convert_type":
        c, to = _q(p["column"]), p["to"]
        lines = []
        if p.get("treat_as_missing"):
            lines.append(f"df[{c}] = df[{c}].replace({_cols(p['treat_as_missing'])}, pd.NA)")
        if to == "integer":
            lines.append(f'df[{c}] = pd.to_numeric(df[{c}], errors="coerce").astype("Int64")')
        elif to == "float":
            lines.append(f'df[{c}] = pd.to_numeric(df[{c}], errors="coerce")')
        elif to == "boolean":
            trues = p.get("true_values") or ["true", "yes", "y", "1", "t"]
            falses = p.get("false_values") or ["false", "no", "n", "0", "f"]
            mapping = {**{v: True for v in trues}, **{v: False for v in falses}}
            lines.append(f'df[{c}] = df[{c}].astype("string").str.strip().str.lower().map({mapping!r}).astype("boolean")')
        elif to in ("date", "datetime"):
            fmt = p.get("format") or (p.get("format_options") or [None])[0]
            fmt_arg = f", format={fmt!r}" if fmt else ""
            lines.append(f'df[{c}] = pd.to_datetime(df[{c}]{fmt_arg}, errors="coerce")')
        else:
            lines.append(f'df[{c}] = df[{c}].astype("string")')
        return lines
    if t == "parse_formatted_number":
        c = _q(p["column"])
        lines = [f'cleaned = df[{c}].astype("string").str.replace(r"[₹$€£,%\\s]", "", regex=True)',
                 'numbers = pd.to_numeric(cleaned, errors="coerce")']
        if p.get("is_percent"):
            lines.append(f'numbers = numbers.where(~df[{c}].astype("string").str.strip().str.endswith("%"), numbers / 100)')
        lines.append(f"df[{c}] = numbers")
        return lines
    if t == "mark_missing":
        lines = []
        for c in p["columns"]:
            if kind_of(dtypes.get(c, "text")) == "text":
                lines.append(f'df[{_q(c)}] = df[{_q(c)}].mask(df[{_q(c)}].astype("string").str.strip().isin({_cols(p["values"])}))')
            else:
                numbers = [float(v) for v in p["values"] if _is_number(v)]
                lines.append(f"df[{_q(c)}] = df[{_q(c)}].mask(df[{_q(c)}].isin({numbers!r}))")
        return lines
    if t == "trim_whitespace":
        return [f"df[{_q(c)}] = df[{_q(c)}].str.strip()" for c in p["columns"]]
    if t == "change_case":
        method = {"lower": "lower", "upper": "upper", "title": "title"}[p["case"]]
        return [f"df[{_q(c)}] = df[{_q(c)}].str.{method}()" for c in p["columns"]]
    if t == "replace_values":
        c = _q(p["column"])
        if p.get("match") == "contains":
            return [f"df[{c}] = df[{c}].str.replace({_q(p['find'])}, {_q(p['replace'])}, regex=False)"]
        return [f"df[{c}] = df[{c}].replace({_q(p['find'])}, {_q(p['replace'])})"]
    if t == "round_numbers":
        return [f"df[{_cols(p['columns'])}] = df[{_cols(p['columns'])}].round({p['decimals']})"]
    if t == "handle_outliers":
        c = _q(p["column"])
        if p.get("method", "iqr") == "iqr":
            factor = p.get("factor") or 1.5
            lines = [f"q1, q3 = df[{c}].quantile([0.25, 0.75])",
                     f"lower, upper = q1 - {factor} * (q3 - q1), q3 + {factor} * (q3 - q1)"]
        else:
            factor = p.get("factor") or 3.0
            lines = [f"mean, std = df[{c}].mean(), df[{c}].std()",
                     f"lower, upper = mean - {factor} * std, mean + {factor} * std"]
        if p["action"] == "cap":
            if dtypes.get(p["column"]) == "integer":  # InsightFlow keeps whole numbers: ceil(lower), floor(upper)
                lines.append(f"df[{c}] = df[{c}].clip(math.ceil(lower), math.floor(upper))")
            else:
                lines.append(f"df[{c}] = df[{c}].clip(lower, upper)")
        else:
            lines.append(f"df = df[df[{c}].isna() | df[{c}].between(lower, upper)]")
        return lines
    if t == "standardize_values":
        return [f"df[{_q(p['column'])}] = df[{_q(p['column'])}].replace({p['mapping']!r})"]
    if t == "apply_rule":
        return _rule_code(p, dtypes, after)
    return [f"# (no pandas equivalent generated for '{t}')"]


def _is_number(value) -> bool:
    try:
        float(value)
        return True
    except (TypeError, ValueError):
        return False


def _rule_code(p: dict, dtypes: dict[str, str], after: dict[str, str]) -> list[str]:
    conditions = [Condition.model_validate(c) for c in p.get("conditions") or []]
    kinds = {name: kind_of(label) for name, label in dtypes.items()}
    action, target = p["action"], p.get("target")
    lines = [f"mask = {mask_code(conditions, p.get('combine', 'and'), kinds)}" if conditions
             else "mask = pd.Series(True, index=df.index)"]
    if action == "remove_rows":
        return lines + ["df = df[~mask]"]
    if action == "keep_rows":
        return lines + ["df = df[mask]"]
    t = _q(target)
    became_decimal = dtypes.get(target) == "integer" and after.get(target) == "float"
    if became_decimal:
        lines.append(f'df[{t}] = df[{t}].astype("Float64")')
    if action == "set_missing":
        return lines + [f"df.loc[mask, {t}] = pd.NA"]
    if action == "set_value":
        kind = kind_of(after.get(target, dtypes.get(target, "text")))
        value = p["value"]
        if kind == "number":
            literal = repr(int(float(value))) if after.get(target) == "integer" else repr(float(value))
        elif kind == "date":
            literal = f"pd.Timestamp({str(value)!r})"
        elif kind == "bool":
            literal = repr(str(value).strip().lower() == "true")
        else:
            literal = repr(str(value))
        return lines + [f"df.loc[mask, {t}] = {literal}"]
    if action == "clamp":
        lo, hi = p.get("min"), p.get("max")
        if dtypes.get(target) == "integer":
            lo = None if lo is None else math.ceil(lo)
            hi = None if hi is None else math.floor(hi)
        return lines + [f"df.loc[mask, {t}] = df.loc[mask, {t}].clip(lower={lo!r}, upper={hi!r})"]
    # calculate
    terms = [Operand.model_validate(x) if isinstance(x, dict) else x for x in p["formula"]]
    formula = f"({formula_code(terms)}).round({CALC_DECIMALS})"
    if target not in dtypes:
        return lines + [f"df[{t}] = {formula}.where(mask)"]
    return lines + [f"df.loc[mask, {t}] = {formula}[mask]"]


def _types(version: VersionRecord | None) -> dict[str, str]:
    return {c["name"]: c.get("dtype", "text") for c in ((version.summary or {}).get("column_info", []) if version else [])}


def pandas_recipe(dataset: DatasetRecord, versions: list[VersionRecord]) -> str:
    by_number = {v.version: v for v in versions}
    steps, base, complete = lineage(by_number, dataset.current_version)
    source = dataset.source_filename
    base_types = _types(base)
    text_columns = [c for c, t in base_types.items() if t == "text"]
    date_columns = [c for c, t in base_types.items() if t in ("date", "datetime")]

    uses_math = any(v.operation.get("type") in ("handle_outliers", "apply_rule") for v in steps)
    uses_div = False
    for v in steps:
        if v.operation.get("type") == "apply_rule":
            params = v.operation.get("params") or {}
            terms = [t for t in params.get("formula") or [] if isinstance(t, str)]
            for c in params.get("conditions") or []:
                terms += [t for t in c.get("formula") or [] if isinstance(t, str)]
            uses_div = uses_div or "/" in terms

    lines = [
        f"# InsightFlow recipe for '{dataset.name}' (version {dataset.current_version})",
        "# Reproduces the cleaning steps with pandas. Run it next to the original file.",
    ]
    if uses_math:
        lines.append("import math")
    lines += ["import pandas as pd", "", f"MISSING_MARKERS = {list(STANDARD_MISSING)!r}"]
    if uses_div:
        lines += ["", "", "def safe_div(a, b):",
                  '    """Division where x / 0 gives a missing value (as in InsightFlow), not infinity."""',
                  "    return (a / b).where(b != 0)", ""]
    dtype_arg = f", dtype={ {c: 'string' for c in text_columns}!r}" if text_columns else ""
    if dataset.source_format == "xlsx":
        sheet = (base.summary or {}).get("source", {}).get("sheet") if base else dataset.sheet_name
        lines.append(f"df = pd.read_excel({source!r}, sheet_name={sheet!r}{dtype_arg}, na_values=MISSING_MARKERS)")
    else:
        lines.append(f"df = pd.read_csv({source!r}{dtype_arg}, na_values=MISSING_MARKERS, keep_default_na=False)")
    if text_columns:
        lines += [
            "# Text columns are read as text so codes like '00123' keep their leading zeros.",
            "# Like InsightFlow, blank cells and missing markers with spaces around them (e.g. '  ', ' NA ') count as missing.",
            'for col in df.select_dtypes(include="string").columns:',
            "    df[col] = df[col].mask(df[col].str.strip().isin(MISSING_MARKERS))",
        ]
    for c in date_columns:
        fmt = ', format="%Y-%m-%d"' if base_types[c] == "date" else ""
        lines.append(f'df[{c!r}] = pd.to_datetime(df[{c!r}]{fmt}, errors="coerce")')

    if not complete:
        lines += ["", "# NOTE: older history was pruned, so the earliest steps can't be shown.",
                  "# The steps below start from a later version."]
    current = dict(base_types)
    for index, version in enumerate(steps, start=1):
        description = (version.summary or {}).get("description", version.operation.get("type"))
        after = _types(version) or dict(current)
        lines += ["", f"# Step {index} (version {version.version}): {description}"]
        lines += _step_code(version.operation, current, after)
        current = after
    stem = dataset.name.replace(" ", "_")
    lines += ["", f'df.to_csv("{stem}_cleaned.csv", index=False)', 'print(df.shape)', ""]
    return "\n".join(lines)
