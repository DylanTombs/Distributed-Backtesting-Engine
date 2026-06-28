"""Backtest runner — calls the compiled ml_backtest binary for a date window.

Binary interface (positional args):
  ml_backtest <feature_csv> <symbol> [model_pt] [feature_scaler_csv] [target_scaler_csv]

The binary writes ml_equity.csv and ml_trades.csv to its CWD. We run it
from PROJECT_ROOT so the default model/scaler paths resolve correctly.

For each backtest request:
  1. Filter the symbol's feature CSV to the requested date window.
  2. Run the binary against that filtered CSV.
  3. Read equity + trades; compute metrics in Python.
  4. Archive and return.
"""
from __future__ import annotations

import csv
import logging
import math
import shutil
import subprocess
import tempfile
import threading
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd  # noqa: E402

from .schemas import BacktestResponse, EquityPoint

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BINARY        = PROJECT_ROOT / "backtester" / "ml_backtest"
DATA_DIR      = PROJECT_ROOT / "backtester" / "data"
MODEL_DIR     = PROJECT_ROOT / "models"
OUTPUT_DIR    = PROJECT_ROOT / "output"
LOOKBACK_BARS = 60   # bars of history prepended so model has seq_len context

_LRU_MAX = 20

# Serialise binary invocations: the C++ binary always writes ml_equity.csv and
# ml_trades.csv to PROJECT_ROOT (its CWD), so concurrent requests would race on
# those files.  The LRU cache lookup remains concurrent — only _execute() is
# serialised.
_binary_lock = threading.Lock()


# ---------------------------------------------------------------------------
# LRU cache
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def is_model_loaded() -> bool:
    return (MODEL_DIR / "transformer.pt").exists()


def run_backtest(
    tickers: list[str],
    date_start: str,
    date_end: str,
) -> BacktestResponse:
    cache_key = f"{','.join(sorted(tickers))}|{date_start}|{date_end}"
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for %s", cache_key)
        return BacktestResponse(**{**cached.model_dump(), "cached": True})

    with _binary_lock:
        result = _execute(tickers, date_start, date_end)
    _cache.put(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _execute(tickers: list[str], date_start: str, date_end: str) -> BacktestResponse:
    if not BINARY.exists():
        raise RuntimeError(
            f"ml_backtest binary not found at {BINARY}. "
            "Build it with Docker or cmake in backtester/."
        )

    # Find the best available symbol with a feature CSV on disk
    symbol, src_csv, warning = _resolve_symbol(tickers)

    tmp_dir = Path(tempfile.mkdtemp(prefix="tt_backtest_"))
    try:
        filtered_csv = _filter_csv(src_csv, symbol, date_start, date_end, tmp_dir)

        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        _run_binary(filtered_csv, symbol)
        return _archive_and_read(run_id, symbol, date_start, date_end, warning=warning)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------

def _resolve_symbol(tickers: list[str]) -> tuple[str, Path, Optional[str]]:
    """Return (symbol, feature_csv_path, warning) for the first ticker that has data.

    Returns warning=None when an exact match is found.  When the fallback is
    used, warning contains a human-readable description of the substitution so
    callers can surface it to the user.
    """
    for ticker in tickers:
        candidate = DATA_DIR / f"{ticker}_features.csv"
        if candidate.exists():
            logger.info("Using feature CSV for requested ticker %s", ticker)
            return ticker, candidate, None

    # Fallback: use whatever features file exists
    csvs = sorted(DATA_DIR.glob("*_features.csv"))
    if csvs:
        symbol = csvs[0].stem.replace("_features", "")
        warning_msg = (
            f"None of the requested tickers {tickers} have feature CSVs. "
            f"Fell back to '{symbol}'. Results reflect '{symbol}', not the requested ticker(s)."
        )
        logger.info(
            "None of %s have feature CSVs — falling back to %s", tickers, symbol
        )
        return symbol, csvs[0], warning_msg

    raise RuntimeError(
        f"No feature CSVs found in {DATA_DIR}. "
        "Run feature engineering first: python research/features/pipeline.py"
    )


# ---------------------------------------------------------------------------
# Date filtering
# ---------------------------------------------------------------------------

def _filter_csv(
    src: Path, symbol: str, date_start: str, date_end: str, out_dir: Path
) -> Path:
    df = pd.read_csv(src)

    date_col = "timestamp" if "timestamp" in df.columns else "date"
    if date_col not in df.columns:
        raise RuntimeError(f"No timestamp/date column in {src}")

    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).reset_index(drop=True)

    window_mask = (df[date_col] >= date_start) & (df[date_col] <= date_end)
    window_rows = df[window_mask]

    if window_rows.empty:
        raise RuntimeError(
            f"No data for {symbol} in [{date_start} → {date_end}]. "
            f"The feature CSV covers "
            f"{df[date_col].min().date()} – {df[date_col].max().date()}."
        )

    # Prepend LOOKBACK_BARS of history so the model has seq_len context
    first_idx = window_rows.index[0]
    lookback_start = max(0, first_idx - LOOKBACK_BARS)
    sliced = df.iloc[lookback_start:window_rows.index[-1] + 1].copy()

    # Binary expects 'date' column name
    if date_col == "timestamp":
        sliced = sliced.rename(columns={"timestamp": "date"})

    out = out_dir / f"{symbol}_features.csv"
    sliced.to_csv(out, index=False)
    logger.info("Filtered %s: %d rows in window", symbol, len(sliced))
    return out


# ---------------------------------------------------------------------------
# Binary invocation
# ---------------------------------------------------------------------------

def _run_binary(feature_csv: Path, symbol: str) -> None:
    model_pt      = MODEL_DIR / "transformer.pt"
    feat_scaler   = MODEL_DIR / "feature_scaler.csv"
    target_scaler = MODEL_DIR / "target_scaler.csv"

    cmd = [
        str(BINARY),
        str(feature_csv),
        symbol,
        str(model_pt),
        str(feat_scaler),
        str(target_scaler),
    ]

    logger.info("Running: %s", " ".join(cmd))
    t0 = time.monotonic()

    proc = subprocess.run(
        cmd,
        cwd=str(PROJECT_ROOT),   # binary writes ml_equity.csv here
        capture_output=True,
        text=True,
    )

    logger.info("Binary exited %.1f s  rc=%d", time.monotonic() - t0, proc.returncode)
    if proc.stdout:
        logger.info("stdout: %s", proc.stdout[-500:])
    if proc.stderr:
        logger.debug("stderr: %s", proc.stderr[-500:])

    if proc.returncode != 0:
        raise RuntimeError(
            f"ml_backtest failed (exit {proc.returncode}): "
            f"{proc.stderr[-400:] or proc.stdout[-400:]}"
        )


# ---------------------------------------------------------------------------
# Archive + read
# ---------------------------------------------------------------------------

def _archive_and_read(
    run_id: str, symbol: str, date_start: str, date_end: str,
    warning: Optional[str] = None,
) -> BacktestResponse:
    # Binary writes to PROJECT_ROOT (its CWD)
    equity_src = PROJECT_ROOT / "ml_equity.csv"
    trades_src = PROJECT_ROOT / "ml_trades.csv"

    run_dir = OUTPUT_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    for src, name in [(equity_src, "ml_equity.csv"), (trades_src, "ml_trades.csv")]:
        if src.exists():
            shutil.copy2(src, run_dir / name)

    equity  = _read_equity(run_dir / "ml_equity.csv", date_start, date_end)
    trades  = _read_trades(run_dir / "ml_trades.csv", date_start, date_end)
    metrics = _compute_metrics(equity, trades, symbol, date_start, date_end)

    # Persist computed metrics alongside the run
    _write_metrics_csv(metrics, run_dir / "ml_metrics.csv")

    return BacktestResponse(
        run_id=run_id,
        metrics=metrics,
        equity=equity,
        trades=trades,
        cached=False,
        warning=warning,
    )


# ---------------------------------------------------------------------------
# Metrics computation (binary doesn't produce a metrics file)
# ---------------------------------------------------------------------------

def _compute_metrics(
    equity: list[EquityPoint],
    trades: list[dict],
    symbol: str,
    date_start: str,
    date_end: str,
) -> dict:
    if not equity:
        return {}

    values   = [p.equity for p in equity]
    initial  = values[0]
    final    = values[-1]
    days     = len(values)

    total_return_pct = (final - initial) / initial * 100

    # Max drawdown
    peak = values[0]
    max_dd = 0.0
    for v in values:
        if v > peak:
            peak = v
        dd = (peak - v) / peak * 100
        if dd > max_dd:
            max_dd = dd

    # Sharpe (annualised, assumes daily bars, rf=0)
    # Bessel-corrected sample variance (n-1 denominator) per ADR-015.
    if len(values) > 1:
        rets = [(values[i] - values[i - 1]) / values[i - 1] for i in range(1, len(values))]
        n = len(rets)
        mean_r = sum(rets) / n
        # Require at least 2 return observations for Bessel correction
        var_r  = sum((r - mean_r) ** 2 for r in rets) / (n - 1) if n > 1 else 0.0
        std_r  = math.sqrt(var_r) if var_r > 0 else 0.0
        sharpe = (mean_r / std_r * math.sqrt(252)) if std_r > 0 else 0.0
    else:
        sharpe = 0.0

    # Win rate from trades (SELL/COVER directions are the closing legs)
    closing = [
        t for t in trades
        if t.get("direction", "").upper() in ("SELL", "COVER", "SHORT")
    ] or trades
    wins = sum(1 for t in closing if _coerce(t.get("profit", 0)) > 0)
    win_rate_pct = (wins / len(closing) * 100) if closing else 0.0

    return {
        "symbol":            symbol,
        "date_start":        date_start,
        "date_end":          date_end,
        "days":              days,
        "total_return_pct":  round(total_return_pct, 2),
        "max_drawdown_pct":  round(max_dd, 2),
        "sharpe_ratio":      round(sharpe, 3),
        "win_rate_pct":      round(win_rate_pct, 1),
        "initial_equity":    round(initial, 2),
        "final_equity":      round(final, 2),
        "n_trades":          len(trades),
    }


def _write_metrics_csv(metrics: dict, path: Path) -> None:
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(metrics.keys()))
        w.writeheader()
        w.writerow(metrics)


# ---------------------------------------------------------------------------
# CSV readers
# ---------------------------------------------------------------------------

def _read_equity(
    path: Path, date_start: str = "", date_end: str = ""
) -> list[EquityPoint]:
    if not path.exists():
        return []

    # Parse datetime bounds once so comparisons are type-safe
    start_dt = datetime.fromisoformat(date_start) if date_start else None
    end_dt   = datetime.fromisoformat(date_end)   if date_end   else None

    # Collect all rows first; do not rely on sort order for early termination —
    # the equity CSV may not be strictly chronological.
    points: list[EquityPoint] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts = row.get("timestamp") or row.get("date") or ""
            try:
                row_dt = datetime.fromisoformat(ts)
            except ValueError:
                logger.debug("_read_equity: skipping unparseable timestamp %r", ts)
                continue
            if start_dt and row_dt < start_dt:
                continue
            if end_dt and row_dt > end_dt:
                continue
            try:
                points.append(EquityPoint(date=ts, equity=float(row.get("equity", 0))))
            except (ValueError, TypeError):
                continue
    return points


def _read_trades(
    path: Path, date_start: str = "", date_end: str = ""
) -> list[dict]:
    if not path.exists():
        return []
    rows = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts = row.get("timestamp") or row.get("date") or ""
            if date_start and ts < date_start:
                continue
            if date_end and ts > date_end:
                break  # C++ engine writes trades in chronological order; safe to break
            rows.append(dict(row))
    return rows[:500]


def _coerce(v) -> float:
    try:
        return float(v)
    except (ValueError, TypeError):
        return 0.0
