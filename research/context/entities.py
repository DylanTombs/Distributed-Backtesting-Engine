"""Ticker and date entity extraction from plain text.

Two extractors are provided:
  - extract_tickers: regex over a bundled S&P 500 + NASDAQ 100 allow-list
  - extract_date_range: dateparser-based natural-language date resolution
"""
from __future__ import annotations

import re
from datetime import timedelta
from typing import Optional

# ---------------------------------------------------------------------------
# Ticker allow-list  (S&P 500 + NASDAQ 100 representative set)
# Kept inline to avoid a network call; update periodically.
# ---------------------------------------------------------------------------
_SP500_TICKERS: frozenset[str] = frozenset({
    "AAPL", "MSFT", "AMZN", "NVDA", "GOOGL", "GOOG", "META", "TSLA", "BRK", "BRKB",
    "UNH", "LLY", "JPM", "V", "XOM", "AVGO", "PG", "MA", "HD", "CVX",
    "MRK", "ABBV", "COST", "PEP", "ADBE", "WMT", "BAC", "CRM", "TMO", "ORCL",
    "KO", "MCD", "CSCO", "NFLX", "DIS", "ACN", "DHR", "ABT", "TMUS", "CMCSA",
    "NEE", "VZ", "TXN", "PM", "RTX", "NKE", "BMY", "QCOM", "AMGN", "T",
    "LOW", "SPGI", "HON", "UNP", "GE", "DE", "SBUX", "AMAT", "ISRG", "CAT",
    "INTC", "IBM", "AXP", "GS", "BLK", "MS", "C", "WFC", "USB", "PNC",
    "SCHW", "CB", "MMC", "AIG", "MET", "PRU", "AFL", "TRV", "HIG", "ALL",
    "XLF", "XLK", "XLE", "XLV", "XLI", "XLU", "XLB", "XLC", "XLRE", "XLP",
    "SPY", "QQQ", "DIA", "IWM", "VTI", "EEM", "VGK", "EWG", "EWJ", "EWU",
    "EWI", "EWP", "EWC", "EWA", "FXI", "EWT", "EWZ", "RSX", "URTH",
    "TLT", "IEF", "SHY", "HYG", "LQD", "BND", "AGG",
    "GLD", "SLV", "USO", "UNG", "DBA", "DBB",
    "BAC", "JPM", "GS", "MS", "C", "WFC", "SIVB", "FRC", "SBNY", "KRE",
    "UAL", "AAL", "DAL", "LUV", "CCL", "RCL", "NCLH", "MGM", "LVS", "WYNN",
    "OXY", "HAL", "SLB", "CVX", "XOM", "COP", "MPC", "VLO", "PSX",
    "AMGN", "GILD", "BIIB", "REGN", "MRNA", "PFE", "BNTX", "JNJ", "ABT",
    "NVDA", "AMD", "INTC", "QCOM", "MU", "WDC", "STX", "SMCI", "AMAT", "LRCX",
    "NFLX", "DIS", "PARA", "WBD", "FOXA", "VIAC", "CMCSA",
    "COIN", "MSTR", "MARA", "RIOT", "HUT",
    "GME", "AMC", "BB", "NOK", "BBBY",
    "BABA", "JD", "PDD", "BIDU", "NIO", "XPEV", "LI",
    "LMT", "RTX", "NOC", "GD", "BA", "HII",
    "TWTR", "SNAP", "PINS", "LYFT", "UBER", "ABNB",
    "MUSK", "HOOD", "SOFI",
    # Additional major tickers
    "MSFT", "AAPL", "GOOGL", "AMZN", "META", "NFLX", "TSLA",
})

# Regex: $TICKER or stand-alone ALL-CAPS word 2-5 chars that is in the allow-list
_TICKER_RE = re.compile(r'\$([A-Z]{1,5})|(?<![A-Z])([A-Z]{2,5})(?![A-Z])')

# Words that look like tickers but are common English words to skip
_STOP_WORDS: frozenset[str] = frozenset({
    "I", "A", "AN", "THE", "AND", "OR", "OF", "IN", "ON", "AT", "TO", "BY",
    "FOR", "BUT", "NOT", "ARE", "WAS", "IS", "BE", "AS", "IT", "WE", "IF",
    "UP", "DO", "DID", "GO", "GET", "GOT", "HAS", "HAD", "ITS", "ALL",
    "NEW", "NOW", "HOW", "WHO", "WHY", "OUT", "USE", "TWO", "TOO", "ANY",
    "CEO", "CFO", "CTO", "IPO", "GDP", "CPI", "PPI", "PMI", "FED", "SEC",
    "US", "UK", "EU", "UN", "IMF", "WHO", "WTO", "G7", "G20",
    "Q1", "Q2", "Q3", "Q4", "YOY", "QOQ", "YTD", "TTM", "EPS", "PE",
    "AI", "ML", "IT", "API", "SaaS", "GAAP",
    "ETF", "IPO", "M&A", "LBO", "DCF",
})


def extract_tickers(text: str) -> list[str]:
    """Return unique tickers found in text, filtered against the allow-list.

    Matches both ``$AAPL`` and bare ``AAPL`` forms.
    """
    found: list[str] = []
    seen: set[str] = set()
    for m in _TICKER_RE.finditer(text):
        ticker = (m.group(1) or m.group(2)).upper()
        if ticker in _STOP_WORDS:
            continue
        if ticker not in _SP500_TICKERS:
            continue
        if ticker not in seen:
            seen.add(ticker)
            found.append(ticker)
    return found[:10]


def extract_date_range(text: str) -> tuple[Optional[str], Optional[str]]:
    """Parse a date or date range from natural-language text.

    Returns ``(date_start, date_end)`` as ISO strings, or ``(None, None)``
    when no date-like phrases are detected.

    Relies on ``dateparser`` if installed; falls back to regex year extraction.
    """
    try:
        import dateparser  # type: ignore
        settings = {"PREFER_DAY_OF_MONTH": "first", "RETURN_AS_TIMEZONE_AWARE": False}

        # Look for explicit ranges like "from March 2020 to April 2020"
        range_re = re.compile(
            r'(?:from\s+)?'
            r'(\w[\w ,]+?\d{4})'
            r'\s+(?:to|through|until|-)\s+'
            r'(\w[\w ,]+?\d{4})',
            re.IGNORECASE,
        )
        m = range_re.search(text)
        if m:
            d1 = dateparser.parse(m.group(1), settings=settings)
            d2 = dateparser.parse(m.group(2), settings=settings)
            if d1 and d2 and d1 <= d2:
                return d1.date().isoformat(), d2.date().isoformat()

        # Single date phrase — expand to a ±30-day window
        date_re = re.compile(
            r'\b(?:January|February|March|April|May|June|July|August|September|'
            r'October|November|December)\s+\d{4}\b'
            r'|\b(?:Q[1-4]\s+\d{4})\b'
            r'|\b\d{4}\b',
            re.IGNORECASE,
        )
        matches = date_re.findall(text)
        if matches:
            parsed = dateparser.parse(matches[0], settings=settings)
            if parsed:
                d = parsed.date()
                return (d - timedelta(days=30)).isoformat(), (d + timedelta(days=30)).isoformat()

    except ImportError:
        pass

    # Regex-only fallback: grab a 4-digit year and return ±6-month window
    year_m = re.search(r'\b(19[5-9]\d|20[0-2]\d)\b', text)
    if year_m:
        year = int(year_m.group(1))
        return f"{year}-01-01", f"{year}-12-31"

    return None, None
