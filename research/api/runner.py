"""Backtest runner — invokes the compiled C++ backtester for a date window.

Architecture:
  1. Read the user's existing backtest_config.yaml (strategy stays fixed — same
     model, thresholds, risk params the user has already configured).
  2. Identify which requested tickers have feature CSVs on disk; fall back to
     whatever is configured when none match.
  3. For each symbol, write a *temp filtered CSV* containing only rows whose
     timestamp falls within [date_start, date_end].
  4. Write a *temp config YAML* pointing at those filtered CSVs (all other
     strategy parameters copied from the base config unchanged).
  5. Invoke ``backtester/ml_backtest <temp_config.yaml>`` directly — no
     pipeline re-run, no model re-export, no feature re-engineering.
  6. Read results from the output directory, archive the run, clean up temps.

An LRU cache keyed on (tickers, date_start, date_end) makes repeated clicks
on the same page instant.
"""
from __future__ import annotations

import csv
import logging
import os
import shutil
import subprocess
import tempfile
import time
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

from .schemas import BacktestResponse, EquityPoint

logger = logging.getLogger(__name__)

PROJECT_ROOT   = Path(__file__).resolve().parents[2]
BASE_CONFIG    = PROJECT_ROOT / "backtest_config.yaml"
BINARY         = PROJECT_ROOT / "backtester" / "ml_backtest"
DATA_DIR       = PROJECT_ROOT / "backtester" / "data"
MODEL_DIR      = PROJECT_ROOT / "models"
OUTPUT_DIR     = PROJECT_ROOT / "output"

_LRU_MAX = 20


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
    skip_train: bool = True,
) -> BacktestResponse:
    cache_key = f"{','.join(sorted(tickers))}|{date_start}|{date_end}"
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for %s", cache_key)
        return BacktestResponse(**{**cached.model_dump(), "cached": True})

    result = _execute(tickers, date_start, date_end)
    _cache.put(cache_key, result)
    return result


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _execute(tickers: list[str], date_start: str, date_end: str) -> BacktestResponse:
    base_cfg = _load_base_config()

    # Resolve which symbols actually have feature data on disk
    available = _find_available_symbols(tickers, base_cfg)
    if not available:
        raise RuntimeError(
            f"No feature CSVs found for {tickers}. "
            f"Available data is in {DATA_DIR}/ — run feature engineering first."
        )

    tmp_dir = Path(tempfile.mkdtemp(prefix="tt_backtest_"))
    try:
        # Filter each symbol's CSV to the requested date window
        filtered = _filter_csvs(available, date_start, date_end, tmp_dir)
        if not filtered:
            raise RuntimeError(
                f"No data in [{date_start} → {date_end}] for {list(available.keys())}. "
                "Try a wider date range."
            )

        # Write a temp config pointing at the filtered CSVs
        tmp_cfg_path = tmp_dir / "backtest_config.yaml"
        _write_temp_config(base_cfg, filtered, tmp_cfg_path)

        # Run the binary
        run_id = datetime.now().strftime("%Y%m%d_%H%M%S")
        _run_binary(tmp_cfg_path)

        # Archive and return
        return _archive_and_read(run_id)

    finally:
        shutil.rmtree(tmp_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Symbol resolution
# ---------------------------------------------------------------------------

def _find_available_symbols(
    requested: list[str],
    base_cfg: dict,
) -> dict[str, Path]:
    """Return {symbol: feature_csv_path} for symbols that have data on disk.

    Priority:
      1. Requested tickers that have a matching *_features.csv in DATA_DIR.
      2. Fall back to whatever the base config already has configured, so the
         user's default backtest still runs when none of the page tickers match.
    """
    found: dict[str, Path] = {}

    for ticker in requested:
        candidate = DATA_DIR / f"{ticker}_features.csv"
        if candidate.exists():
            found[ticker] = candidate

    if found:
        return found

    # Nothing matched — use whatever is already in backtest_config.yaml
    logger.info(
        "No feature CSVs for %s; falling back to base-config symbols", requested
    )
    symbol = base_cfg.get("symbol", "")
    feature_csv = base_cfg.get("feature_csv", "")
    if symbol and feature_csv:
        p = Path(feature_csv)
        if not p.is_absolute():
            p = PROJECT_ROOT / p
        # Also handle Docker paths like /backtester/data/...
        if not p.exists():
            local_guess = DATA_DIR / p.name
            if local_guess.exists():
                p = local_guess
        if p.exists():
            found[symbol] = p

    # Additional numbered symbols
    for i in range(1, 20):
        s = base_cfg.get(f"symbol_{i}", "")
        f = base_cfg.get(f"feature_csv_{i}", "")
        if s and f:
            p = Path(f)
            if not p.is_absolute():
                p = PROJECT_ROOT / p
            if not p.exists():
                local_guess = DATA_DIR / p.name
                if local_guess.exists():
                    p = local_guess
            if p.exists():
                found[s] = p

    return found


# ---------------------------------------------------------------------------
# Date filtering
# ---------------------------------------------------------------------------

def _filter_csvs(
    symbols: dict[str, Path],
    date_start: str,
    date_end: str,
    out_dir: Path,
) -> dict[str, Path]:
    """Write date-filtered copies of each feature CSV; return {symbol: path}."""
    filtered: dict[str, Path] = {}

    for symbol, src_path in symbols.items():
        df = pd.read_csv(src_path)

        # Normalise date column (may be 'timestamp' or 'date')
        date_col = "timestamp" if "timestamp" in df.columns else "date"
        if date_col not in df.columns:
            logger.warning("No date column in %s — skipping", src_path)
            continue

        df[date_col] = pd.to_datetime(df[date_col])
        mask = (df[date_col] >= date_start) & (df[date_col] <= date_end)
        slice_df = df[mask].copy()

        if slice_df.empty:
            logger.warning(
                "%s has no rows in [%s, %s]", symbol, date_start, date_end
            )
            continue

        out_path = out_dir / f"{symbol}_features.csv"
        slice_df.to_csv(out_path, index=False)
        logger.info(
            "Filtered %s: %d rows → %d rows (%s–%s)",
            symbol, len(df), len(slice_df), date_start, date_end,
        )
        filtered[symbol] = out_path

    return filtered


# ---------------------------------------------------------------------------
# Temp config
# ---------------------------------------------------------------------------

def _write_temp_config(
    base_cfg: dict,
    filtered: dict[str, Path],
    out_path: Path,
) -> None:
    """Clone the base config, replace symbol/feature_csv entries with filtered
    paths, and write to out_path."""
    cfg = {k: v for k, v in base_cfg.items()}

    # Clear old symbol entries
    keys_to_remove = ["symbol", "feature_csv"]
    for i in range(1, 20):
        keys_to_remove += [f"symbol_{i}", f"feature_csv_{i}"]
    for k in keys_to_remove:
        cfg.pop(k, None)

    # Replace with filtered symbols
    symbols = list(filtered.items())
    cfg["symbol"]      = symbols[0][0]
    cfg["feature_csv"] = str(symbols[0][1])
    for i, (sym, path) in enumerate(symbols[1:], start=1):
        cfg[f"symbol_{i}"]      = sym
        cfg[f"feature_csv_{i}"] = str(path)

    # Fix model/scaler paths — resolve Docker-style /models/... to local paths
    for key, local in [
        ("model_pt",           MODEL_DIR / "transformer.pt"),
        ("feature_scaler_csv", MODEL_DIR / "feature_scaler.csv"),
        ("target_scaler_csv",  MODEL_DIR / "target_scaler.csv"),
    ]:
        if key in cfg:
            p = Path(cfg[key])
            if not p.exists() and local.exists():
                cfg[key] = str(local)

    cfg["output_dir"] = str(OUTPUT_DIR)

    with open(out_path, "w") as f:
        yaml.dump(cfg, f, default_flow_style=False)


# ---------------------------------------------------------------------------
# Binary invocation
# ---------------------------------------------------------------------------

def _run_binary(config_path: Path) -> None:
    if not BINARY.exists():
        raise RuntimeError(
            f"ml_backtest binary not found at {BINARY}. "
            "Build it first: cd backtester && mkdir -p build && "
            "cd build && cmake .. && make"
        )

    cmd = [str(BINARY), str(config_path)]
    logger.info("Running: %s", " ".join(cmd))
    t0 = time.monotonic()

    proc = subprocess.run(cmd, cwd=str(PROJECT_ROOT), capture_output=True, text=True)

    logger.info("Binary exited in %.1f s", time.monotonic() - t0)
    if proc.stdout:
        logger.debug("stdout: %s", proc.stdout[-1000:])
    if proc.stderr:
        logger.debug("stderr: %s", proc.stderr[-1000:])

    if proc.returncode != 0:
        raise RuntimeError(
            f"ml_backtest failed (exit {proc.returncode}): {proc.stderr[-500:]}"
        )


# ---------------------------------------------------------------------------
# Archive and read results
# ---------------------------------------------------------------------------

def _archive_and_read(run_id: str) -> BacktestResponse:
    run_dir = OUTPUT_DIR / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    archived: list[str] = []
    for name in ("ml_equity.csv", "ml_trades.csv", "ml_metrics.csv"):
        src = OUTPUT_DIR / name
        if src.exists():
            shutil.copy2(src, run_dir / name)
            archived.append(name)

    if not archived:
        raise RuntimeError(
            "Backtest produced no output files — check logs for backtester errors."
        )

    equity  = _read_equity(run_dir / "ml_equity.csv")
    trades  = _read_trades(run_dir / "ml_trades.csv")
    metrics = _read_metrics(run_dir / "ml_metrics.csv")

    return BacktestResponse(
        run_id=run_id,
        metrics=metrics,
        equity=equity,
        trades=trades,
        cached=False,
    )


# ---------------------------------------------------------------------------
# CSV readers
# ---------------------------------------------------------------------------

def _read_equity(path: Path) -> list[EquityPoint]:
    if not path.exists():
        return []
    points: list[EquityPoint] = []
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            ts = row.get("timestamp") or row.get("date") or ""
            try:
                points.append(EquityPoint(date=ts, equity=float(row.get("equity", 0))))
            except (ValueError, TypeError):
                continue
    return points


def _read_trades(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with open(path, newline="") as f:
        return [dict(r) for r in csv.DictReader(f)][:500]


def _read_metrics(path: Path) -> dict:
    if not path.exists():
        return {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            return {k: _coerce(v) for k, v in row.items()}
    return {}


def _load_base_config() -> dict:
    if not BASE_CONFIG.exists():
        return {}
    with open(BASE_CONFIG) as f:
        return yaml.safe_load(f) or {}


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
