"""Deterministic data profiler. Runs in the sandbox.

Reads JSON control message from stdin: {"format": "csv"|"xlsx", "path": "..."}
Emits JSON profile to stdout. No network; no LLM.

Columns produced per field:
- name
- type (numeric | boolean | datetime | text | mixed)
- null_count
- non_null_count
- cardinality (approx via ``nunique``)
- stats (numeric: min/max/mean/median/std/p25/p75; text: min_len/max_len; top-3 values)
"""
from __future__ import annotations
import json
import math
import sys
import warnings
from typing import Any

warnings.filterwarnings("ignore")

# Deliberately imported here (inside sandbox) so main process is unaffected.
import pandas as pd  # type: ignore


def _series_type(s: "pd.Series") -> str:
    if pd.api.types.is_bool_dtype(s):
        return "boolean"
    if pd.api.types.is_numeric_dtype(s):
        return "numeric"
    if pd.api.types.is_datetime64_any_dtype(s):
        return "datetime"
    # Try datetime coercion sample
    non_null = s.dropna().head(50)
    if len(non_null) > 0 and pd.api.types.is_object_dtype(s):
        try:
            parsed = pd.to_datetime(non_null, errors="coerce", utc=False)
            if parsed.notna().mean() > 0.9:
                return "datetime"
        except Exception:  # noqa: BLE001
            pass
    if pd.api.types.is_object_dtype(s) or pd.api.types.is_string_dtype(s):
        return "text"
    return "mixed"


def _safe(v: Any) -> Any:
    if v is None:
        return None
    if isinstance(v, float):
        if math.isnan(v) or math.isinf(v):
            return None
        return v
    if hasattr(v, "isoformat"):
        try:
            return v.isoformat()
        except Exception:  # noqa: BLE001
            return str(v)
    if isinstance(v, (int, str, bool)):
        return v
    return str(v)


def _column_profile(col_name: str, s: "pd.Series") -> dict:
    ctype = _series_type(s)
    null_count = int(s.isna().sum())
    non_null = s.dropna()
    non_null_count = int(len(non_null))
    try:
        cardinality = int(non_null.nunique())
    except Exception:  # noqa: BLE001
        cardinality = None

    stats: dict = {}
    top: list = []
    if ctype == "numeric" and non_null_count > 0:
        try:
            desc = non_null.describe(percentiles=[0.25, 0.5, 0.75])
            stats = {
                "min": _safe(float(desc.get("min"))) if "min" in desc else None,
                "max": _safe(float(desc.get("max"))) if "max" in desc else None,
                "mean": _safe(float(desc.get("mean"))) if "mean" in desc else None,
                "std": _safe(float(desc.get("std"))) if "std" in desc else None,
                "p25": _safe(float(desc.get("25%"))) if "25%" in desc else None,
                "median": _safe(float(desc.get("50%"))) if "50%" in desc else None,
                "p75": _safe(float(desc.get("75%"))) if "75%" in desc else None,
            }
            try:
                hist = non_null.value_counts(bins=10, sort=False).tolist()
                stats["histogram"] = [int(x) for x in hist]
            except Exception:  # noqa: BLE001
                stats["histogram"] = []
        except Exception:  # noqa: BLE001
            stats = {}
    elif ctype == "text" and non_null_count > 0:
        try:
            lengths = non_null.astype(str).str.len()
            stats = {
                "min_len": int(lengths.min()),
                "max_len": int(lengths.max()),
                "avg_len": float(round(lengths.mean(), 2)),
            }
        except Exception:  # noqa: BLE001
            stats = {}
    elif ctype == "boolean" and non_null_count > 0:
        try:
            vc = non_null.value_counts()
            stats = {"true_count": int(vc.get(True, 0)), "false_count": int(vc.get(False, 0))}
        except Exception:  # noqa: BLE001
            stats = {}
    elif ctype == "datetime" and non_null_count > 0:
        try:
            parsed = pd.to_datetime(non_null, errors="coerce")
            stats = {
                "min": _safe(parsed.min()),
                "max": _safe(parsed.max()),
            }
        except Exception:  # noqa: BLE001
            stats = {}

    try:
        vc = non_null.value_counts().head(3)
        top = [{"value": _safe(idx), "count": int(cnt)} for idx, cnt in vc.items()]
    except Exception:  # noqa: BLE001
        top = []

    return {
        "name": str(col_name),
        "type": ctype,
        "null_count": null_count,
        "non_null_count": non_null_count,
        "cardinality": cardinality,
        "stats": stats,
        "top_values": top,
    }


def main() -> None:
    ctl = json.loads(sys.stdin.read() or "{}")
    fmt = ctl.get("format")
    path = ctl.get("path")
    if not fmt or not path:
        json.dump({"error": "missing format/path"}, sys.stdout)
        return

    if fmt == "csv":
        # Row cap to bound memory in Phase 1.
        df = pd.read_csv(path, nrows=200_000, low_memory=False)
    elif fmt == "xlsx":
        df = pd.read_excel(path, engine="openpyxl", nrows=200_000)
    else:
        json.dump({"error": f"unsupported format {fmt}"}, sys.stdout)
        return

    columns = [_column_profile(c, df[c]) for c in df.columns]
    result = {
        "row_count": int(len(df)),
        "column_count": int(len(df.columns)),
        "columns": columns,
        "profile_meta": {"row_cap": 200_000, "engine": "pandas"},
    }
    json.dump(result, sys.stdout, default=str)


if __name__ == "__main__":
    main()
