"""Unit tests for _LRUCache and _compute_metrics in research/api/runner.py.

Note: runner.py is excluded from coverage measurement via .coveragerc, but
these tests still run and must pass to keep CI green.
"""
from __future__ import annotations

import math

import pytest

from research.api.runner import _LRUCache, _compute_metrics
from research.api.schemas import EquityPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _ep(date: str, equity: float) -> EquityPoint:
    return EquityPoint(date=date, equity=equity)


def _sell_trade(profit: float) -> dict:
    return {"direction": "SELL", "profit": str(profit)}


# ---------------------------------------------------------------------------
# _LRUCache
# ---------------------------------------------------------------------------

class TestLRUCache:
    def test_lru_cache_miss_returns_none(self):
        cache = _LRUCache(max_size=5)
        assert cache.get("missing") is None

    def test_lru_cache_stores_and_retrieves(self):
        cache = _LRUCache(max_size=5)
        cache.put("k", "value")
        assert cache.get("k") == "value"

    def test_lru_cache_hit_moves_to_end(self):
        """Accessing 'A' bumps it to MRU; on the next eviction 'B' (LRU) is dropped."""
        cache = _LRUCache(max_size=2)
        cache.put("A", 1)
        cache.put("B", 2)
        # Touch A so it becomes MRU; B becomes LRU
        assert cache.get("A") == 1
        # Add C — should evict B (the LRU), not A
        cache.put("C", 3)
        assert cache.get("A") == 1    # A survived
        assert cache.get("B") is None  # B was evicted
        assert cache.get("C") == 3    # C was just inserted

    def test_lru_cache_evicts_lru_item(self):
        """With max_size=2, inserting a third entry evicts the first."""
        cache = _LRUCache(max_size=2)
        cache.put("A", 1)
        cache.put("B", 2)
        cache.put("C", 3)
        assert cache.get("A") is None  # evicted
        assert cache.get("B") == 2
        assert cache.get("C") == 3

    def test_lru_cache_overwrite_updates_value(self):
        cache = _LRUCache(max_size=5)
        cache.put("k", "first")
        cache.put("k", "second")
        assert cache.get("k") == "second"


# ---------------------------------------------------------------------------
# _compute_metrics
# ---------------------------------------------------------------------------

class TestComputeMetrics:
    def test_compute_metrics_empty_equity_returns_empty_dict(self):
        result = _compute_metrics([], [], "AAPL", "2020-01-01", "2020-12-31")
        assert result == {}

    def test_compute_metrics_total_return(self):
        equity = [_ep("2020-01-01", 100.0), _ep("2020-01-02", 110.0)]
        result = _compute_metrics(equity, [], "AAPL", "2020-01-01", "2020-01-02")
        assert abs(result["total_return_pct"] - 10.0) < 0.01

    def test_compute_metrics_max_drawdown(self):
        # Peak at 120, then drops to 90 → drawdown = (120-90)/120 * 100 = 25%
        equity = [
            _ep("2020-01-01", 100.0),
            _ep("2020-01-02", 120.0),
            _ep("2020-01-03", 90.0),
        ]
        result = _compute_metrics(equity, [], "AAPL", "2020-01-01", "2020-01-03")
        assert abs(result["max_drawdown_pct"] - 25.0) < 0.01

    def test_compute_metrics_zero_std_sharpe(self):
        # Flat equity — all returns are 0, std=0, so Sharpe must be 0
        equity = [_ep(f"2020-01-0{i}", 100.0) for i in range(1, 6)]
        result = _compute_metrics(equity, [], "AAPL", "2020-01-01", "2020-01-05")
        assert result["sharpe_ratio"] == 0.0

    def test_compute_metrics_win_rate_from_trades(self):
        # Two profitable SELL trades, one losing SELL trade → win_rate ≈ 66.7%
        trades = [
            _sell_trade(profit=500.0),
            _sell_trade(profit=200.0),
            _sell_trade(profit=-100.0),
        ]
        equity = [_ep("2020-01-01", 100.0), _ep("2020-01-02", 105.0)]
        result = _compute_metrics(equity, trades, "AAPL", "2020-01-01", "2020-01-02")
        assert abs(result["win_rate_pct"] - 66.7) < 0.1

    def test_compute_metrics_returns_expected_keys(self):
        equity = [_ep("2020-01-01", 100.0), _ep("2020-01-02", 110.0)]
        result = _compute_metrics(equity, [], "AAPL", "2020-01-01", "2020-01-02")
        for key in (
            "symbol",
            "total_return_pct",
            "max_drawdown_pct",
            "sharpe_ratio",
            "win_rate_pct",
            "days",
            "n_trades",
        ):
            assert key in result, f"Missing key: {key}"

    def test_compute_metrics_sharpe_uses_bessel_correction(self):
        """Sharpe must use sample std (÷ n-1), not population std (÷ n).

        With equity [100, 101, 102, 103] we compute the expected Sharpe using
        the Bessel-corrected formula and assert the function returns the same
        value within 0.001.
        """
        values = [100.0, 101.0, 102.0, 103.0]
        equity = [_ep(f"2020-01-0{i+1}", v) for i, v in enumerate(values)]

        rets = [
            (values[i] - values[i - 1]) / values[i - 1]
            for i in range(1, len(values))
        ]
        n = len(rets)
        mean_r = sum(rets) / n
        # Bessel-corrected variance (÷ n-1 = ÷2)
        var_r = sum((r - mean_r) ** 2 for r in rets) / (n - 1)
        std_r = math.sqrt(var_r)
        expected_sharpe = round(mean_r / std_r * math.sqrt(252), 3)

        result = _compute_metrics(
            equity, [], "AAPL", "2020-01-01", "2020-01-04"
        )
        assert abs(result["sharpe_ratio"] - expected_sharpe) < 0.001
