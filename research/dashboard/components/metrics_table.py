"""Reusable summary metrics table component."""

from __future__ import annotations

import pandas as pd

# Display order and human-readable labels for ml_metrics.csv keys
_METRIC_ORDER = [
    ("sharpe_ratio",      "Sharpe Ratio"),
    ("information_ratio", "Info Ratio"),
    ("total_return",      "Total Return (%)"),
    ("annualised_return", "Ann. Return (%)"),
    ("benchmark_return",  "Benchmark Return (%)"),
    ("alpha",             "Alpha (%)"),
    ("max_drawdown",      "Max Drawdown (%)"),
    ("trading_days",      "Trading Days"),
]


def metrics_summary_table(runs: list) -> pd.DataFrame:
    """Build a comparison DataFrame with one column per run.

    Rows are ordered by _METRIC_ORDER; any unknown keys are appended at the end.
    """
    if not runs:
        return pd.DataFrame()

    ordered_keys = [k for k, _ in _METRIC_ORDER]
    label_map = dict(_METRIC_ORDER)

    all_keys: list[str] = list(ordered_keys)
    for run in runs:
        for k in run.meta.metrics:
            if k not in all_keys:
                all_keys.append(k)

    rows = []
    for key in all_keys:
        row: dict = {"Metric": label_map.get(key, key)}
        for run in runs:
            col = _run_label(run)
            val = run.meta.metrics.get(key)
            row[col] = f"{val:.3f}" if val is not None else "—"
        rows.append(row)

    return pd.DataFrame(rows).set_index("Metric")


def metric_delta_table(baseline_run, compare_run) -> pd.DataFrame:
    """Two-column table showing baseline value, comparison value, and % delta."""
    rows = []
    all_keys = set(baseline_run.meta.metrics) | set(compare_run.meta.metrics)
    label_map = dict(_METRIC_ORDER)

    for key in [k for k, _ in _METRIC_ORDER] + sorted(all_keys - set(k for k, _ in _METRIC_ORDER)):
        base_val = baseline_run.meta.metrics.get(key)
        cmp_val  = compare_run.meta.metrics.get(key)
        if base_val is None and cmp_val is None:
            continue
        delta = ""
        if (base_val is not None and cmp_val is not None
                and base_val != 0 and base_val != cmp_val):
            pct = (cmp_val - base_val) / abs(base_val) * 100
            delta = f"{pct:+.1f}%"
        rows.append({
            "Metric":   label_map.get(key, key),
            "Baseline": f"{base_val:.3f}" if base_val is not None else "—",
            "Compare":  f"{cmp_val:.3f}"  if cmp_val  is not None else "—",
            "Δ":        delta,
        })
    return pd.DataFrame(rows)


def _run_label(run) -> str:
    symbols = ", ".join(run.meta.symbols) if run.meta.symbols else "unknown"
    return f"{run.meta.run_id} ({symbols})"
