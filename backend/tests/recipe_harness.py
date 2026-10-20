"""Run a list of operations through InsightFlow AND through its generated pandas recipe; return both results."""

import io
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

import polars as pl

from app.db.models import DatasetRecord, VersionRecord
from app.engine.infer import infer_types
from app.engine.operations import column_info, run_operation
from app.engine.readers import read_table
from app.engine.recipe import pandas_recipe


def run_both(csv_bytes: bytes, steps: list[tuple[str, dict]], name: str = "data") -> tuple[pl.DataFrame, list[dict], str]:
    """Returns (InsightFlow result, rows produced by pandas as dicts of strings, recipe code)."""
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / f"{name}.csv"
        src.write_bytes(csv_bytes)
        inferred = infer_types(read_table(src, "csv").df)
        df = inferred.df
        now = datetime.now(timezone.utc)
        dataset_id = uuid4()
        versions = [VersionRecord(dataset_id, 1, "p", df.height, df.width, 0, {"type": "upload"},
                                  {"column_info": inferred.columns}, now)]
        for number, (op, params) in enumerate(steps, start=2):
            result = run_operation(df, op, params, with_samples=False)
            df = result.df
            versions.append(VersionRecord(dataset_id, number, "p", df.height, df.width, 0, result.operation,
                                          {"column_info": column_info(df), "description": result.description}, now))
        record = DatasetRecord(dataset_id, uuid4(), name, src.name, "csv", "x", 1, None, None, len(versions), "ready",
                               df.height, df.width, now, now)
        code = pandas_recipe(record, versions)
        (Path(tmp) / "recipe.py").write_text(code)
        proc = subprocess.run([sys.executable, "recipe.py"], cwd=tmp, capture_output=True, text=True)
        if proc.returncode != 0:
            raise AssertionError(f"recipe failed:\n{proc.stderr}\n--- code ---\n{code}")
        produced = (Path(tmp) / f"{name}_cleaned.csv").read_text()
    import csv
    return df, list(csv.DictReader(io.StringIO(produced))), code


def assert_same(df: pl.DataFrame, pandas_rows: list[dict], code: str) -> None:
    import csv
    buf = io.StringIO()
    df.write_csv(buf)
    ours = list(csv.DictReader(io.StringIO(buf.getvalue())))
    assert len(ours) == len(pandas_rows), f"row count {len(ours)} vs pandas {len(pandas_rows)}\n{code}"
    assert list(ours[0].keys() if ours else []) == list(pandas_rows[0].keys() if pandas_rows else []), code
    for i, (a, b) in enumerate(zip(ours, pandas_rows)):
        for col in a:
            x, y = a[col], b[col]
            if x == y:
                continue
            try:
                if abs(float(x) - float(y)) <= 1e-9 * max(1.0, abs(float(x))):
                    continue
            except ValueError:
                pass
            if x[:10] == y[:10] and len(x) >= 10 and x[4:5] == "-" and y[10:] in ("", " 00:00:00", "T00:00:00"):
                continue  # same date, pandas may print midnight time
            raise AssertionError(f"row {i + 1}, column {col!r}: InsightFlow {x!r} vs pandas {y!r}\n--- code ---\n{code}")
