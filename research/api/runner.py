"""Backtest runner — bridges the FastAPI layer to the existing pipeline.

Runs ``run_pipeline.py`` in a subprocess so the API process stays responsive
and the pipeline's stdout streams naturally to logs. Results are read back
from the output directory.

An LRU cache keyed on (tickers_str, date_start, date_end) prevents re-running
identical backtests within the same server session.
"""
from __future__ import annotations

import csv
import json
import logging
import os
import subprocess
import sys
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Optional

from .schemas import BacktestResponse, EquityPoint

logger = logging.getLogger(__name__)

# Paths relative to the project root (where run_pipeline.py lives)
PROJECT_ROOT = Path(__file__).resolve().parents[2]
OUTPUT_DIR = PROJECT_ROOT / "output"
BACKTEST_CONFIG = PROJECT_ROOT / "backtest_config.yaml"
MODEL_DIR = PROJECT_ROOT / "models"

_LRU_MAX = 20


class _LRUCache:
    def __init__(self, max_size: int):
        self._cache: OrderedDict = OrderedDict()
        self._max = max_size

    def get(self, key: str) -> Optional[BacktestResponse]:
        if key in self._cache:
            self._cache.move_to_end(key)
            return self._cache[key]
        return None

    def put(self, key: str, value: BacktestResponse) -> None:
        self._cache[key] = value
        self._cache.move_to_end(key)
        if len(self._cache) > self._max:
            self._cache.popitem(last=False)


_cache = _LRUCache(_LRU_MAX)


def is_model_loaded() -> bool:
    """Return True if the exported transformer model artefact exists."""
    return (MODEL_DIR / "transformer.pt").exists()


def run_backtest(
    tickers: list[str],
    date_start: str,
    date_end: str,
    skip_train: bool = True,
) -> BacktestResponse:
    """Run (or retrieve from cache) a backtest for the given parameters.

    Currently delegates to the full pipeline via subprocess.  The date window
    is written into ``backtest_config.yaml`` before invoking so the C++
    backtester honours the requested period.
    """
    cache_key = f"{','.join(sorted(tickers))}|{date_start}|{date_end}"
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for %s", cache_key)
        return BacktestResponse(**{**cached.model_dump(), "cached": True})

    run_id = _generate_run_id()
    _write_event_config(tickers, date_start, date_end)

    cmd = [
        sys.executable,
        str(PROJECT_ROOT / "run_pipeline.py"),
        "--skip-features",
        "--archive-run",
        "--no-tearsheet",
    ]
    if skip_train:
        cmd.append("--skip-train")

    logger.info("Launching pipeline: %s", " ".join(cmd))
    t0 = time.monotonic()
    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)
    elapsed = time.monotonic() - t0

    if proc.returncode != 0:
        logger.error("Pipeline failed:\n%s", proc.stderr[-2000:])
        raise RuntimeError(
            f"Backtest pipeline exited with code {proc.returncode}. "
            f"stderr: {proc.stderr[-500:]}"
        )

    logger.info("Pipeline completed in %.1f s", elapsed)
    result = _read_results(run_id)
    _cache.put(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _generate_run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _write_event_config(tickers: list[str], date_start: str, date_end: str) -> None:
    """Patch backtest_config.yaml with the requested symbols and date window."""
    import yaml  # type: ignore

    if not BACKTEST_CONFIG.exists():
        logger.warning("backtest_config.yaml not found at %s", BACKTEST_CONFIG)
        return

    with open(BACKTEST_CONFIG) as f:
        cfg = yaml.safe_load(f) or {}

    cfg["symbols"] = tickers
    cfg["start_date"] = date_start
    cfg["end_date"] = date_end

    with open(BACKTEST_CONFIG, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


def _read_results(run_id: str) -> BacktestResponse:
    """Read equity, trades, and metrics from the most-recent archive run."""
    runs_dir = OUTPUT_DIR / "runs"
    if not runs_dir.exists():
        raise RuntimeError("No archived runs found in output/runs/")

    # Pick the latest run directory
    run_dirs = sorted(runs_dir.iterdir(), key=lambda p: p.name, reverse=True)
    if not run_dirs:
        raise RuntimeError("output/runs/ is empty after pipeline completed")

    latest = run_dirs[0]
    actual_run_id = latest.name

    equity = _read_equity(latest / "ml_equity.csv")
    trades = _read_trades(latest / "ml_trades.csv")
    metrics = _read_metrics(latest / "ml_metrics.csv")

    return BacktestResponse(
        run_id=actual_run_id,
        metrics=metrics,
        equity=equity,
        trades=trades,
        cached=False,
    )


def _read_equity(path: Path) -> list[EquityPoint]:
    if not path.exists():
        return []
    points: list[EquityPoint] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            ts = row.get("timestamp") or row.get("date") or ""
            eq = row.get("equity", "0")
            try:
                points.append(EquityPoint(date=ts, equity=float(eq)))
            except (ValueError, TypeError):
                continue
    return points


def _read_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    trades: list[dict] = []
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            trades.append(dict(row))
    return trades[:500]  # cap to keep response size reasonable


def _read_metrics(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            return {k: _coerce(v) for k, v in row.items()}
    return {}


def _coerce(v: str):
    try:
        return int(v)
    except (ValueError, TypeError):
        pass
    try:
        return float(v)
    except (ValueError, TypeError):
        pass
    return v
