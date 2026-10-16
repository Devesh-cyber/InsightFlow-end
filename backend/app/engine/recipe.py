"""
"Python recipe": a pandas script that reproduces the cleaning steps.

Learners can see exactly what each click did, in the library they're learning,
and re-run it on next month's file. Generated from the version history, following
reverts, so it reflects the steps that actually produced the current version.
"""

from app.db.models import DatasetRecord, VersionRecord
from app.engine.infer import STANDARD_MISSING


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


def _step_code(op: dict) -> list[str]:
    t, p = op["type"], op.get("params") or {}
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
            suffix = ".dt.date" if to == "date" else ""
            lines.append(f'df[{c}] = pd.to_datetime(df[{c}]{fmt_arg}, errors="coerce"){suffix}')
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
        return [f"df[{_cols(p['columns'])}] = df[{_cols(p['columns'])}].replace({_cols(p['values'])}, pd.NA)"]
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
            lines.append(f"df[{c}] = df[{c}].clip(lower, upper)")
        else:
            lines.append(f"df = df[df[{c}].isna() | df[{c}].between(lower, upper)]")
        return lines
    return [f"# (no pandas equivalent generated for '{t}')"]


def pandas_recipe(dataset: DatasetRecord, versions: list[VersionRecord]) -> str:
    by_number = {v.version: v for v in versions}
    steps, base, complete = lineage(by_number, dataset.current_version)
    source = dataset.source_filename
    lines = [
        f"# InsightFlow recipe for '{dataset.name}' (version {dataset.current_version})",
        "# Reproduces the cleaning steps with pandas. Run it next to the original file.",
        "import pandas as pd",
        "",
        f"MISSING_MARKERS = {list(STANDARD_MISSING)!r}",
    ]
    text_columns = []
    if base is not None:
        text_columns = [c["name"] for c in (base.summary or {}).get("column_info", []) if c.get("dtype") == "text"]
    dtype_arg = f", dtype={ {c: 'string' for c in text_columns}!r}" if text_columns else ""
    if dataset.source_format == "xlsx":
        sheet = (base.summary or {}).get("source", {}).get("sheet") if base else dataset.sheet_name
        lines.append(f"df = pd.read_excel({source!r}, sheet_name={sheet!r}{dtype_arg}, na_values=MISSING_MARKERS)")
    else:
        lines.append(f"df = pd.read_csv({source!r}{dtype_arg}, na_values=MISSING_MARKERS, keep_default_na=False)")
    if text_columns:
        lines.append("# Text columns are read as text so codes like '00123' keep their leading zeros.")

    if not complete:
        lines += ["", "# NOTE: older history was pruned, so the earliest steps can't be shown.",
                  "# The steps below start from a later version."]
    for index, version in enumerate(steps, start=1):
        description = (version.summary or {}).get("description", version.operation.get("type"))
        lines += ["", f"# Step {index} (version {version.version}): {description}"]
        lines += _step_code(version.operation)
    stem = dataset.name.replace(" ", "_")
    lines += ["", f'df.to_csv("{stem}_cleaned.csv", index=False)', 'print(df.shape)', ""]
    return "\n".join(lines)
