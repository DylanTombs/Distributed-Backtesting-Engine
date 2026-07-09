"""On-demand daily OHLCV fetching with a local cache (Phase 8.1, ADR-045).

Provider: Stooq's plain-CSV endpoint — no API key, no SDK dependency, full
daily history per request. US tickers map to ``{lower}.us`` symbols.

Cache policy: fetched files persist indefinitely under
``backtester/data/ohlcv/{TICKER}.csv`` (public market data, not user data —
distinct from the ADR-040 run-archive TTL). A fetch happens only when the
cache is missing or does not cover the requested end date; a same-day
re-fetch guard stops repeated network calls for genuinely unlisted ranges.

``DATA_FETCH_DISABLED=1`` makes the module cache-only: tests and air-gapped
deployments never touch the network.
"""
from __future__ import annotations

import csv
import logging
import os
import re
import time
from datetime import date
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

PROJECT_ROOT = Path(__file__).resolve().parents[2]
# OHLCV_CACHE_DIR points the cache at a persistent volume on hosted deploys
# (Phase 9.3) — without it, fetched data dies with the container filesystem
# on every redeploy and each ticker pays its network cost again.
OHLCV_DIR = Path(
    os.environ.get("OHLCV_CACHE_DIR")
    or PROJECT_ROOT / "backtester" / "data" / "ohlcv"
)
LEGACY_DATA_DIR = PROJECT_ROOT / "backtester" / "data"   # pre-Phase-8 raw CSVs

_STOOQ_URL = "https://stooq.com/q/d/l/?s={symbol}&i=d"
_FETCH_TIMEOUT_S = 15.0
_MAX_RESPONSE_BYTES = 10_000_000
_REFETCH_GUARD_S = 86_400          # don't re-fetch the same ticker twice a day

# Same shape rule the API boundary enforces (schemas.BacktestRequest)
_TICKER_RE = re.compile(r"[A-Z0-9.\-]{1,7}")

_EXPECTED_HEADER = ["Date", "Open", "High", "Low", "Close", "Volume"]


def fetch_enabled() -> bool:
    return os.environ.get("DATA_FETCH_DISABLED", "") not in ("1", "true", "yes")


def get_ohlcv_csv(ticker: str, date_end: str) -> Optional[Path]:
    """Return a path to a daily OHLCV CSV for ``ticker`` covering ``date_end``.

    Resolution order: fresh-enough cache → legacy in-repo CSV → network fetch
    (unless disabled). Returns None when no data can be obtained; callers
    surface that as a client error, never as a silent substitute (ADR-047).
    """
    if not _TICKER_RE.fullmatch(ticker):
        logger.warning("get_ohlcv_csv: rejected malformed ticker")
        return None

    cached = OHLCV_DIR / f"{ticker}.csv"
    if _covers(cached, date_end) or _recently_fetched(cached):
        return cached if cached.exists() else None

    legacy = LEGACY_DATA_DIR / f"{ticker}.csv"
    if _covers(legacy, date_end):
        return legacy

    if not fetch_enabled():
        logger.info("get_ohlcv_csv: fetch disabled and no cache for %s", ticker)
        # A stale cache still beats nothing when fetching is off.
        return cached if cached.exists() else (legacy if legacy.exists() else None)

    if _fetch_to_cache(ticker, cached):
        return cached
    return cached if cached.exists() else (legacy if legacy.exists() else None)


# ---------------------------------------------------------------------------
# Cache inspection
# ---------------------------------------------------------------------------

def _covers(path: Path, date_end: str) -> bool:
    """True when ``path`` exists and its last row is on/after ``date_end``.

    Historical windows (the common case for curated events) hit this
    immediately; only requests beyond the cached range trigger a re-fetch.
    """
    if not path.exists():
        return False
    last = _last_data_date(path)
    return last is not None and last >= date_end


def _recently_fetched(path: Path) -> bool:
    try:
        return path.exists() and (time.time() - path.stat().st_mtime) < _REFETCH_GUARD_S
    except OSError:
        return False


def _last_data_date(path: Path) -> Optional[str]:
    try:
        with open(path, newline="") as f:
            last_row = None
            for last_row in csv.reader(f):
                pass
        if last_row and _iso_date(last_row[0]):
            return last_row[0]
    except (OSError, IndexError):
        pass
    return None


def _iso_date(v: str) -> bool:
    try:
        date.fromisoformat(v)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Network fetch
# ---------------------------------------------------------------------------

def _stooq_symbol(ticker: str) -> str:
    # BRK-B → brk-b.us; class-share dots become dashes on Stooq
    return ticker.lower().replace(".", "-") + ".us"


def _fetch_to_cache(ticker: str, dest: Path) -> bool:
    """Download full daily history for ``ticker`` into ``dest``.

    Returns True on success. Failures are logged and return False — the
    caller decides how to degrade.
    """
    try:
        import httpx  # type: ignore
    except ImportError:
        logger.warning("httpx not installed — cannot fetch OHLCV")
        return False

    url = _STOOQ_URL.format(symbol=_stooq_symbol(ticker))
    try:
        resp = httpx.get(url, timeout=_FETCH_TIMEOUT_S, follow_redirects=True)
        resp.raise_for_status()
    except Exception as exc:  # noqa: BLE001
        logger.warning("OHLCV fetch failed for %s: %s", ticker, exc)
        return False

    body = resp.text
    if len(body) > _MAX_RESPONSE_BYTES:
        logger.warning("OHLCV response for %s exceeds size cap", ticker)
        return False

    rows = _validate_csv(body)
    if rows is None:
        # Stooq returns "No data" or an HTML page for unknown symbols; touch
        # the cache file path's mtime guard by NOT writing anything.
        logger.info("No OHLCV data available from provider for %s", ticker)
        return False

    OHLCV_DIR.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    tmp.write_text(body)
    tmp.replace(dest)   # atomic: readers never see a partial file
    logger.info("Cached %d OHLCV rows for %s", rows, ticker)
    return True


def _validate_csv(body: str) -> Optional[int]:
    """Validate provider output; return the data-row count or None."""
    lines = body.strip().splitlines()
    if len(lines) < 2:
        return None
    header = [h.strip() for h in lines[0].split(",")]
    if header[: len(_EXPECTED_HEADER)] != _EXPECTED_HEADER:
        return None
    first = lines[1].split(",")
    if not first or not _iso_date(first[0]):
        return None
    return len(lines) - 1
