"""Statistical Analysis Agent.

Given a Phase-1 dataset profile (columns[]) and the actual dataset bytes,
choose relevant statistical tests, generate the Python code that performs
them, and execute that code inside the sandbox (app/sandbox.py + workers/
stats_worker.py).

For Phase 2 we ship a DETERMINISTIC code generator. The LLM plug-in
happens in a follow-up: this module already goes through the same
sandboxed execution path, so swapping ``generate_code()`` for an LLM
call is a one-function change.

Result contract (returned by run_statistical_analysis()):
    {
      "ok": True,
      "tests_selected": [...],
      "code": "<python source>",
      "result": {...},          # JSON emitted by the sandbox on fd 3
      "elapsed_seconds": float,
      "sandbox_stdout_logs": str,
    }
"""
from __future__ import annotations
import json
import struct
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..sandbox import SandboxLimits, run_sandboxed

STATS_WORKER = str(Path(__file__).resolve().parent.parent / "workers" / "stats_worker.py")


@dataclass
class GenerationPlan:
    tests: list[str]
    numeric_cols: list[str]
    categorical_cols: list[str]
    datetime_cols: list[str]


def _classify(profile_columns: list[dict]) -> GenerationPlan:
    numeric, categorical, datetimes = [], [], []
    for c in profile_columns:
        t = c.get("type")
        name = c["name"]
        if t == "numeric":
            numeric.append(name)
        elif t == "datetime":
            datetimes.append(name)
        elif t in ("text", "boolean"):
            # Treat low-cardinality text/boolean as categorical.
            card = c.get("cardinality", 0) or 0
            if 0 < card <= 50:
                categorical.append(name)
    tests = []
    if len(numeric) >= 2:
        tests.append("pearson_correlation_matrix")
    if numeric:
        tests.append("iqr_outlier_detection")
    if categorical and numeric:
        tests.append("cohort_mean_comparison")
    if datetimes and numeric:
        tests.append("time_trend_slope")
    return GenerationPlan(tests=tests, numeric_cols=numeric,
                          categorical_cols=categorical, datetime_cols=datetimes)


def generate_code(plan: GenerationPlan) -> str:
    """Produce the Python snippet the sandbox will execute. Uses only the
    pre-injected globals df/pd/np/stats/math and assigns to RESULT."""
    lines = [
        "RESULT = {}",
        f"numeric_cols = {plan.numeric_cols!r}",
        f"categorical_cols = {plan.categorical_cols!r}",
        f"datetime_cols = {plan.datetime_cols!r}",
    ]

    if "pearson_correlation_matrix" in plan.tests:
        lines += [
            "if len(numeric_cols) >= 2:",
            "    corr_df = df[numeric_cols].corr(method='pearson').round(6)",
            "    RESULT['pearson_correlation'] = {",
            "        'columns': numeric_cols,",
            "        'matrix': [[float(corr_df.iloc[i, j]) for j in range(len(numeric_cols))]"
            "                   for i in range(len(numeric_cols))],",
            "    }",
        ]

    if "iqr_outlier_detection" in plan.tests:
        lines += [
            "outliers = {}",
            "for col in numeric_cols:",
            "    s = df[col].dropna()",
            "    if len(s) == 0:",
            "        continue",
            "    q1 = float(s.quantile(0.25))",
            "    q3 = float(s.quantile(0.75))",
            "    iqr = q3 - q1",
            "    low = q1 - 1.5 * iqr",
            "    high = q3 + 1.5 * iqr",
            "    mask = (s < low) | (s > high)",
            "    outliers[col] = {",
            "        'q1': q1, 'q3': q3, 'iqr': iqr,",
            "        'lower_fence': float(low), 'upper_fence': float(high),",
            "        'outlier_count': int(mask.sum()),",
            "        'outlier_indices': [int(i) for i in s.index[mask].tolist()[:50]],",
            "    }",
            "RESULT['iqr_outliers'] = outliers",
        ]

    if "cohort_mean_comparison" in plan.tests:
        lines += [
            "cohort = {}",
            "for cat in categorical_cols:",
            "    grp = df.groupby(cat, dropna=True)[numeric_cols].mean().round(6)",
            "    cohort[cat] = {",
            "        'group_means': {str(k): {c: float(v) for c, v in row.items()}"
            "                        for k, row in grp.to_dict(orient='index').items()},",
            "    }",
            "RESULT['cohort_means'] = cohort",
        ]

    if "time_trend_slope" in plan.tests:
        lines += [
            "trends = {}",
            "for tcol in datetime_cols:",
            "    ts = pd.to_datetime(df[tcol], errors='coerce')",
            "    x = ts.astype('int64').astype('float64').values",
            "    for ncol in numeric_cols:",
            "        y = df[ncol].astype('float64').values",
            "        mask = (~np.isnan(x)) & (~np.isnan(y))",
            "        if mask.sum() >= 3:",
            "            slope, intercept, r, p, _ = stats.linregress(x[mask], y[mask])",
            "            trends[f'{tcol}~{ncol}'] = {",
            "                'slope': float(slope), 'r': float(r), 'p_value': float(p),",
            "                'n': int(mask.sum()),",
            "            }",
            "RESULT['time_trends'] = trends",
        ]

    return "\n".join(lines) + "\n"


def build_stdin_payload(code: str, dataset_csv_bytes: bytes) -> bytes:
    control = json.dumps({"code": code, "input_format": "csv"}).encode("utf-8")
    return struct.pack(">I", len(control)) + control + dataset_csv_bytes


async def run_statistical_analysis(
    profile_columns: list[dict],
    dataset_csv_bytes: bytes,
    limits: SandboxLimits | None = None,
) -> dict[str, Any]:
    plan = _classify(profile_columns)
    code = generate_code(plan)
    payload = build_stdin_payload(code, dataset_csv_bytes)
    sb = await run_sandboxed(STATS_WORKER, payload, limits)
    return {
        "tests_selected": plan.tests,
        "code": code,
        "sandbox_ok": (sb.exit_code == 0 and not sb.timed_out),
        "sandbox_result": sb.result,
        "sandbox_stdout_logs": sb.stdout_logs,
        "sandbox_stderr_logs": sb.stderr_logs,
        "elapsed_seconds": sb.elapsed_seconds,
        "exit_code": sb.exit_code,
        "timed_out": sb.timed_out,
        "killed_by_signal": sb.killed_by_signal,
    }
