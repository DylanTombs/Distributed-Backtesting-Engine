"""Backtest runner — calls the compiled ml_backtest binary for a date window.

Binary interface (positional args):
  ml_backtest <feature_csv> <symbol> [model_pt] [feature_scaler_csv] [target_scaler_csv]

The binary writes ml_equity.csv and ml_trades.csv to its CWD. Each request
runs the binary inside its own temporary directory (Phase 7.3, resolving
ADR-027), so concurrent requests share no mutable state; all input paths
are passed absolute.

For each backtest request:
  1. Filter the symbol's feature CSV to the requested date window.
  2. Run the binary against that filtered CSV inside a per-run temp dir.
  3. Read equity + trades; compute metrics in Python.
  4. Archive and return.
"""
from __future__ import annotations

import csv
import logging
import math
import os
import shutil
import subprocess
import tempfile
import threading
import time
import uuid
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Optional

import pandas as pd  # noqa: E402

from research.data import fetcher
from .schemas import BacktestResponse, EquityPoint, StrategySpec
from .strategies import ResolvedStrategy, StrategyError, resolve, to_spec_file

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
BINARY        = PROJECT_ROOT / "backtester" / "ml_backtest"
RULES_BINARY  = PROJECT_ROOT / "backtester" / "backtest"
DATA_DIR      = PROJECT_ROOT / "backtester" / "data"
MODEL_DIR     = PROJECT_ROOT / "models"
OUTPUT_DIR    = PROJECT_ROOT / "output"
LOOKBACK_BARS = 60   # bars of history prepended so model has seq_len context

_LRU_MAX = 50

# Archived run directories are pruned after this many days (P2-6) so the
# hosted deployment's "request-level data only, 7-day TTL" privacy stance
# holds without manual cleanup.
_RUNS_TTL_DAYS = 7


class BacktestInputError(RuntimeError):
    """A backtest failed because of the client's request (bad window, unknown
    tickers) rather than a server fault. Messages are safe to return to the
    client verbatim — no paths, no build instructions."""

# Bound concurrent binary invocations (P1-10): per-run temp dirs remove the
# need for full serialisation, but an unbounded process count would exhaust a
# small hosted VM (warm-up plus live traffic). Default 2 suits shared-cpu-1x.
_MAX_CONCURRENT_RUNS = max(1, int(os.environ.get("MAX_CONCURRENT_BACKTESTS", "2")))
_run_slots = threading.BoundedSemaphore(_MAX_CONCURRENT_RUNS)

# Hard wall-clock cap per binary invocation.
_BINARY_TIMEOUT_S = 90


# ---------------------------------------------------------------------------
# LRU cache
# ---------------------------------------------------------------------------

class _LRUCache:
    """LRU cache safe for concurrent access.

    The warm-up daemon thread (ADR-033) writes entries while FastAPI
    threadpool workers read and write concurrently, so every compound
    OrderedDict operation must hold the lock (P0-2).
    """

    def __init__(self, max_size: int):
        self._cache: OrderedDict = OrderedDict()
        self._max = max_size
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[BacktestResponse]:
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
                return self._cache[key]
            return None

    def put(self, key: str, value: BacktestResponse) -> None:
        with self._lock:
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
    strategy: Optional[StrategySpec] = None,
) -> BacktestResponse:
    try:
        resolved = resolve(strategy)
    except StrategyError as exc:
        raise BacktestInputError(str(exc)) from exc

    cache_key = (
        f"{','.join(sorted(tickers))}|{date_start}|{date_end}|{resolved.hash}"
    )
    cached = _cache.get(cache_key)
    if cached is not None:
        logger.info("Cache hit for %s", cache_key)
        return BacktestResponse(**{**cached.model_dump(), "cached": True})

    if resolved.is_ml:
        result = _execute(tickers, date_start, date_end)
    else:
        result = _execute_rules(tickers, date_start, date_end, resolved)

    result = BacktestResponse(**{
        **result.model_dump(),
        "strategy": resolved.canonical,
        "experimental": resolved.is_ml,
    })
    _cache.put(cache_key, result)
    return result


def warmup_cache() -> None:
    """Pre-populate the LRU cache for every curated event.

    Runs in a background daemon thread at startup.  Each event is attempted
    independently — a failure (missing binary, CSV out of range, etc.) is
    logged and skipped so the remaining events still warm up.
    """
    from research.context.events import EVENTS  # local import avoids circular dep

    # Distinguish "binary not built" (expected in CI/dev) from per-event
    # failures (a production incident on a hosted deploy) — a 0/41 prime
    # must never look healthy in the logs (P2-3). Warm-up primes the
    # default (transparent) strategy, so the rules binary is the gate.
    if not RULES_BINARY.exists():
        logger.warning(
            "warmup_cache: backtest binary not found at %s — "
            "skipping pre-warm entirely", RULES_BINARY,
        )
        return

    logger.info("warmup_cache: starting pre-warm for %d events", len(EVENTS))
    hits = 0
    for key, ev in EVENTS.items():
        try:
            run_backtest(
                tickers=ev.tickers,
                date_start=ev.date_start,
                date_end=ev.date_end,
            )
            hits += 1
            logger.debug("warmup_cache: primed %s", key)
        except Exception as exc:  # noqa: BLE001
            logger.warning("warmup_cache: failed to prime %s — %s", key, exc)
    logger.info("warmup_cache: complete — %d/%d events primed", hits, len(EVENTS))


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------

def _execute_rules(
    tickers: list[str], date_start: str, date_end: str,
    resolved: ResolvedStrategy,
) -> BacktestResponse:
    """Transparent-strategy path (Phase 8): raw OHLCV + rules binary.

    No silent symbol substitution here — if none of the requested tickers
    has obtainable data, that is the client's answer (ADR-047).
    """
    if not RULES_BINARY.exists():
        raise RuntimeError(
            f"backtest binary not found at {RULES_BINARY}. "
            "Build it with cmake in backtester/."
        )

    symbol: Optional[str] = None
    src_csv: Optional[Path] = None
    for ticker in tickers:
        candidate = fetcher.get_ohlcv_csv(ticker, date_end)
        if candidate is not None:
            symbol, src_csv = ticker, candidate
            break
    if symbol is None or src_csv is None:
        raise BacktestInputError(
            f"No market data is available for {tickers}. The symbol may be "
            "delisted or not covered by the data provider."
        )

    run_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"
    run_dir = Path(tempfile.mkdtemp(prefix="tt_run_"))
    try:
        filtered_csv = _filter_ohlcv(
            src_csv, symbol, date_start, date_end, run_dir,
            warmup_bars=resolved.warmup_bars(),
        )
        spec_path = run_dir / "strategy_spec.txt"
        spec_path.write_text(to_spec_file(resolved))

        with _run_slots:
            _invoke(
                [str(RULES_BINARY), str(filtered_csv), symbol,
                 str(spec_path), "."],
                cwd=run_dir,
            )
        return _archive_and_read(run_dir, run_id, symbol, date_start, date_end)
    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


def _filter_ohlcv(
    src: Path, symbol: str, date_start: str, date_end: str, out_dir: Path,
    warmup_bars: int,
) -> Path:
    """Slice an OHLCV CSV to the window plus indicator warm-up history."""
    df = pd.read_csv(src)
    df.columns = [c.strip().lower() for c in df.columns]

    date_col = next(
        (c for c in ("date", "timestamp") if c in df.columns), None
    )
    required = [date_col, "open", "high", "low", "close"]
    if date_col is None or any(c not in df.columns for c in required):
        raise RuntimeError(f"Unexpected OHLCV columns in data for {symbol}")

    df[date_col] = pd.to_datetime(df[date_col])
    df = df.sort_values(date_col).reset_index(drop=True)

    window_mask = (df[date_col] >= date_start) & (df[date_col] <= date_end)
    window_rows = df[window_mask]
    if window_rows.empty:
        raise BacktestInputError(
            f"No data for {symbol} in [{date_start} → {date_end}]. "
            f"Available data covers "
            f"{df[date_col].min().date()} – {df[date_col].max().date()}."
        )

    first_idx = window_rows.index[0]
    lookback_start = max(0, first_idx - warmup_bars)
    sliced = df.iloc[lookback_start:window_rows.index[-1] + 1]

    # Binary reads columns positionally: date, open, high, low, close
    out_cols = [date_col, "open", "high", "low", "close"]
    out = out_dir / f"{symbol}_ohlcv.csv"
    sliced[out_cols].rename(columns={date_col: "date"}).to_csv(out, index=False)
    logger.info("Filtered OHLCV %s: %d rows (incl. %d warm-up)",
                symbol, len(sliced), first_idx - lookback_start)
    return out


def _execute(tickers: list[str], date_start: str, date_end: str) -> BacktestResponse:
    if not BINARY.exists():
        raise RuntimeError(
            f"ml_backtest binary not found at {BINARY}. "
            "Build it with Docker or cmake in backtester/."
        )

    # Find the best available symbol with a feature CSV on disk
    symbol, src_csv, warning = _resolve_symbol(tickers)

    # UUID suffix keeps run_ids collision-free under concurrency (P1-9);
    # the timestamp prefix keeps archive directories human-sortable.
    run_id = f"{datetime.now():%Y%m%d_%H%M%S}_{uuid.uuid4().hex[:8]}"

    # Per-run working directory: the binary's CWD, so its fixed-name output
    # files are isolated per request. A run that writes nothing can never be
    # mistaken for a fresh result — the directory starts empty (P0-1).
    run_dir = Path(tempfile.mkdtemp(prefix="tt_run_"))
    try:
        filtered_csv = _filter_csv(src_csv, symbol, date_start, date_end, run_dir)

        with _run_slots:
            _run_binary(filtered_csv, symbol, cwd=run_dir)
        return _archive_and_read(
            run_dir, run_id, symbol, date_start, date_end, warning=warning
        )

    finally:
        shutil.rmtree(run_dir, ignore_errors=True)


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

    logger.error("No feature CSVs found in %s — run the feature pipeline", DATA_DIR)
    raise BacktestInputError(
        "No market data is available on the server for the requested tickers."
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
        raise BacktestInputError(
            f"No data for {symbol} in [{date_start} → {date_end}]. "
            f"Available data covers "
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

def _run_binary(feature_csv: Path, symbol: str, cwd: Path) -> None:
    model_pt      = MODEL_DIR / "transformer.pt"
    feat_scaler   = MODEL_DIR / "feature_scaler.csv"
    target_scaler = MODEL_DIR / "target_scaler.csv"

    _invoke(
        [
            str(BINARY),
            str(feature_csv),
            symbol,
            str(model_pt),
            str(feat_scaler),
            str(target_scaler),
        ],
        cwd=cwd,
    )


def _invoke(cmd: list[str], cwd: Path) -> None:
    """Run an engine binary with timeout, logging, and exit-code checking."""
    logger.info("Running: %s", " ".join(cmd))
    t0 = time.monotonic()

    try:
        proc = subprocess.run(
            cmd,
            cwd=str(cwd),   # binary writes ml_equity.csv into the per-run dir
            capture_output=True,
            text=True,
            timeout=_BINARY_TIMEOUT_S,
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(
            f"backtest binary exceeded the {_BINARY_TIMEOUT_S}s time limit"
        ) from exc

    logger.info("Binary exited %.1f s  rc=%d", time.monotonic() - t0, proc.returncode)
    if proc.stdout:
        logger.info("stdout: %s", proc.stdout[-500:])
    if proc.stderr:
        logger.debug("stderr: %s", proc.stderr[-500:])

    if proc.returncode != 0:
        raise RuntimeError(
            f"backtest binary failed (exit {proc.returncode}): "
            f"{proc.stderr[-400:] or proc.stdout[-400:]}"
        )


# ---------------------------------------------------------------------------
# Archive + read
# ---------------------------------------------------------------------------

def _archive_and_read(
    run_dir: Path, run_id: str, symbol: str, date_start: str, date_end: str,
    warning: Optional[str] = None,
) -> BacktestResponse:
    # Binary writes to its per-run CWD
    equity_src = run_dir / "ml_equity.csv"
    trades_src = run_dir / "ml_trades.csv"

    # The run directory started empty, so absence here means the binary
    # exited 0 without producing output — fail loudly rather than returning
    # an empty result (P0-1).
    if not equity_src.exists():
        raise RuntimeError("ml_backtest produced no output")

    runs_root = OUTPUT_DIR / "runs"
    _prune_old_runs(runs_root)

    run_dir = runs_root / run_id
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


def _prune_old_runs(runs_root: Path, ttl_days: int = _RUNS_TTL_DAYS) -> None:
    """Delete archived run directories older than ``ttl_days`` (P2-6).

    Best-effort: failures are logged, never raised — pruning must not break
    a live backtest request.
    """
    if not runs_root.exists():
        return
    cutoff = time.time() - ttl_days * 86_400
    try:
        for entry in runs_root.iterdir():
            if entry.is_dir() and entry.stat().st_mtime < cutoff:
                shutil.rmtree(entry, ignore_errors=True)
                logger.info("Pruned expired run archive %s", entry.name)
    except OSError as exc:
        logger.warning("Run-archive pruning failed: %s", exc)


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
