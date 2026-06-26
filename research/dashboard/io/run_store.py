"""
run_store.py — reads backtest run artifacts from output/runs/<timestamp>/ directories.

Each run directory contains:
  ml_equity.csv       — timestamp, equity, price, benchmark_equity
  ml_trades.csv       — timestamp, symbol, price, quantity, direction, profit, pnl
  ml_metrics.csv      — metric, value (two-column key-value CSV)
  tearsheet_*.html    — rendered HTML report (optional)
  backtest_config.yaml — config snapshot (optional)
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

import pandas as pd


@dataclass
class RunMeta:
    run_id: str                       # timestamp directory name, e.g. "20260401_143022"
    symbols: list[str]                # inferred from ml_trades.csv symbol column
    metrics: dict[str, float]         # from ml_metrics.csv
    tearsheet_path: Optional[str] = None  # path to tearsheet HTML, or None


@dataclass
class RunArtifacts:
    meta: RunMeta
    equity: pd.DataFrame    # columns: timestamp, equity, price, benchmark_equity
    trades: pd.DataFrame    # columns: timestamp, symbol, price, quantity, direction, profit, pnl


def list_runs(output_dir: str = "output/runs") -> list[RunMeta]:
    """Return all run metadata sorted newest-first.

    Scans `output_dir` for subdirectories, reads ml_metrics.csv from each,
    and returns a list of RunMeta objects.  Directories that are missing
    ml_metrics.csv are silently skipped.
    """
    root = Path(output_dir)
    if not root.exists():
        return []

    runs: list[RunMeta] = []
    for entry in sorted(root.iterdir(), reverse=True):
        if not entry.is_dir():
            continue
        metrics_path = entry / "ml_metrics.csv"
        if not metrics_path.exists():
            continue
        try:
            metrics = _load_metrics(metrics_path)
            trades_path = entry / "ml_trades.csv"
            symbols = _infer_symbols(trades_path)
            tearsheet = _find_tearsheet(entry)
            runs.append(RunMeta(
                run_id=entry.name,
                symbols=symbols,
                metrics=metrics,
                tearsheet_path=tearsheet,
            ))
        except Exception:
            continue

    return runs


def load_run(run_id: str, output_dir: str = "output/runs") -> RunArtifacts:
    """Load all artifacts for one run.

    Raises FileNotFoundError if the run directory or ml_equity.csv is missing.
    """
    run_dir = Path(output_dir) / run_id
    if not run_dir.exists():
        raise FileNotFoundError(f"Run directory not found: {run_dir}")

    equity_path = run_dir / "ml_equity.csv"
    if not equity_path.exists():
        raise FileNotFoundError(f"ml_equity.csv missing in run {run_id}")

    equity = pd.read_csv(equity_path)
    trades_path = run_dir / "ml_trades.csv"
    trades = pd.read_csv(trades_path) if trades_path.exists() else pd.DataFrame()
    metrics = _load_metrics(run_dir / "ml_metrics.csv") if (run_dir / "ml_metrics.csv").exists() else {}
    symbols = _infer_symbols(trades_path) if trades_path.exists() else []
    tearsheet = _find_tearsheet(run_dir)

    meta = RunMeta(run_id=run_id, symbols=symbols, metrics=metrics, tearsheet_path=tearsheet)
    return RunArtifacts(meta=meta, equity=equity, trades=trades)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _load_metrics(path: Path) -> dict[str, float]:
    df = pd.read_csv(path)
    if "metric" not in df.columns or "value" not in df.columns:
        return {}
    return dict(zip(df["metric"], df["value"].astype(float)))


def _infer_symbols(trades_path: Path) -> list[str]:
    if not trades_path.exists():
        return []
    df = pd.read_csv(trades_path)
    if "symbol" not in df.columns:
        return []
    return sorted(df["symbol"].unique().tolist())


def _find_tearsheet(run_dir: Path) -> Optional[str]:
    for f in run_dir.glob("tearsheet_*.html"):
        return str(f)
    tearsheet = run_dir / "tearsheet.html"
    return str(tearsheet) if tearsheet.exists() else None
