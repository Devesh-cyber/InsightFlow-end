"""Convert Polars / Python values into JSON-safe values (no NaN, no Infinity, ISO dates)."""

import datetime
import math
from decimal import Decimal
from typing import Any


def to_json(value: Any) -> Any:
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, (datetime.datetime, datetime.date, datetime.time)):
        return value.isoformat()
    if isinstance(value, datetime.timedelta):
        return value.total_seconds()
    if isinstance(value, Decimal):
        return float(value)
    if isinstance(value, (list, tuple)):
        return [to_json(v) for v in value]
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    return str(value)


def rnd(value: Any, digits: int = 6) -> Any:
    """Round floats for display, keeping JSON safety."""
    value = to_json(value)
    if isinstance(value, float):
        return round(value, digits)
    return value
