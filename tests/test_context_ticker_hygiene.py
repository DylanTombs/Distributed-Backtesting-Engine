"""Ticker hygiene cross-checks for curated events and the entity allow-list.

P1-11 regression: symbols like GBP (a currency code) and MUSK (a surname)
pass the boundary regex but are not tradable tickers — they fail only at
data-fetch time, shipping broken Quick-Pick cards. These tests encode the
class of defect so it cannot reappear.

P2-8: several curated events reference historically accurate but delisted
symbols (SIVB, ENE, ...). That is acceptable ONLY while every such event
also lists at least one still-listed proxy, so runner._resolve_symbol
(ADR-028) can fall through to real data.
"""
from __future__ import annotations

import re

from research.context.entities import _SP500_TICKERS
from research.context.events import EVENTS

# Same shape rule the API boundary applies (schemas.BacktestRequest)
_TICKER_RE = re.compile(r"[A-Z0-9.\-]{1,7}")

# Symbols that look like tickers but are not tradable US-listed instruments.
# Currency codes belong here — their exposure is available via ETFs
# (GBP → FXB, EUR → FXE, JPY → FXY).
_NON_TRADABLE = frozenset({
    # ISO 4217 currency codes
    "GBP", "EUR", "USD", "JPY", "CHF", "CNY", "AUD", "CAD", "NZD", "HKD",
    # People, not companies
    "MUSK",
})

# Delisted/acquired symbols knowingly kept for historical accuracy (P2-8).
# Any event using one of these MUST also list a live proxy.
_KNOWN_DELISTED = frozenset({
    "SIVB", "FRC", "SBNY", "PACW",          # 2023 regional bank failures
    "ENE", "WCOM",                          # 2001-02 accounting collapses
    "TWTR", "BBBY", "VIAC", "RSX",          # taken private / delisted
})


class TestEventTickers:
    def test_every_event_ticker_matches_boundary_regex(self):
        for key, ev in EVENTS.items():
            for t in ev.tickers:
                assert _TICKER_RE.fullmatch(t), (
                    f"Event {key!r} ticker {t!r} fails the boundary regex"
                )

    def test_no_event_ticker_is_a_non_tradable_symbol(self):
        for key, ev in EVENTS.items():
            bad = set(ev.tickers) & _NON_TRADABLE
            assert not bad, (
                f"Event {key!r} lists non-tradable symbol(s) {sorted(bad)} — "
                "use the equivalent ETF (e.g. FXB for GBP exposure)"
            )

    def test_every_event_has_at_least_one_listed_ticker(self):
        """Delisted symbols are allowed only alongside a live proxy (P2-8)."""
        for key, ev in EVENTS.items():
            live = [t for t in ev.tickers if t not in _KNOWN_DELISTED]
            assert live, (
                f"Event {key!r} contains only delisted tickers {ev.tickers} — "
                "ADR-028 fallback would substitute arbitrary data"
            )

    def test_event_tickers_are_unique_within_each_event(self):
        for key, ev in EVENTS.items():
            assert len(ev.tickers) == len(set(ev.tickers)), (
                f"Event {key!r} has duplicate tickers: {ev.tickers}"
            )


class TestEntityAllowlist:
    def test_allowlist_contains_no_non_tradable_symbols(self):
        bad = _SP500_TICKERS & _NON_TRADABLE
        assert not bad, (
            f"Entity allow-list contains non-tradable symbol(s) {sorted(bad)}"
        )

    def test_allowlist_symbols_match_boundary_regex(self):
        for t in _SP500_TICKERS:
            assert _TICKER_RE.fullmatch(t), (
                f"Allow-list symbol {t!r} fails the boundary regex"
            )
