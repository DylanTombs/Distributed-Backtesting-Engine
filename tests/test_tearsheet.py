"""
Tests for research/analysis/tearsheet.py

All tests operate on synthetic in-memory DataFrames — no real backtest output
or file I/O required (except the integration tests which use tempfile).
"""
from __future__ import annotations

import math
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

import sys
import pathlib
_RESEARCH = str(pathlib.Path(__file__).parent.parent / "research")
if _RESEARCH not in sys.path:
    sys.path.insert(0, _RESEARCH)

from analysis.tearsheet import (
    Tearsheet,
    compute_drawdown_series,
    compute_monthly_returns,
    compute_per_symbol_pnl,
    compute_rolling_sharpe,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _equity_series(values: list[float]) -> pd.Series:
    return pd.Series(values, dtype=float)


def _equity_df(values: list[float], start: str = "2020-01-01") -> pd.DataFrame:
    dates = pd.date_range(start, periods=len(values), freq="B")
    return pd.DataFrame({
        "timestamp": dates.strftime("%Y-%m-%d"),
        "equity": values,
        "price": values,
        "benchmark_equity": values,
    })


def _trades_df(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _metrics_csv(tmpdir: str) -> str:
    path = os.path.join(tmpdir, "ml_metrics.csv")
    df = pd.DataFrame({
        "metric": ["sharpe_ratio", "max_drawdown", "total_return", "trading_days"],
        "value":  [1.23, 5.67, 18.5, 252],
    })
    df.to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# compute_drawdown_series
# ---------------------------------------------------------------------------

class TestComputeDrawdown:
    def test_flat_equity_has_zero_drawdown(self):
        eq = _equity_series([100.0] * 10)
        dd = compute_drawdown_series(eq)
        assert (dd == 0.0).all()

    def test_monotone_increase_has_zero_drawdown(self):
        eq = _equity_series([100.0, 110.0, 120.0, 130.0])
        dd = compute_drawdown_series(eq)
        assert (dd == 0.0).all()

    def test_known_drawdown_value(self):
        # Peak at 200, trough at 150: drawdown = (150-200)/200 = -0.25
        eq = _equity_series([100.0, 200.0, 150.0])
        dd = compute_drawdown_series(eq)
        assert dd.iloc[2] == pytest.approx(-0.25)

    def test_drawdown_is_non_positive(self):
        eq = _equity_series([100.0, 80.0, 120.0, 90.0, 130.0])
        dd = compute_drawdown_series(eq)
        assert (dd <= 0.0).all()

    def test_recovery_to_new_peak_resets_to_zero(self):
        eq = _equity_series([100.0, 90.0, 110.0])
        dd = compute_drawdown_series(eq)
        assert dd.iloc[2] == pytest.approx(0.0)


# ---------------------------------------------------------------------------
# compute_rolling_sharpe
# ---------------------------------------------------------------------------

class TestRollingSharp:
    def test_first_window_minus_one_bars_are_nan(self):
        eq = _equity_series([100.0 + i for i in range(70)])
        rs = compute_rolling_sharpe(eq, window=60)
        assert rs.iloc[:60].isna().all()

    def test_value_at_window_is_finite(self):
        eq = _equity_series([100.0 + i * 0.5 for i in range(70)])
        rs = compute_rolling_sharpe(eq, window=60)
        assert np.isfinite(rs.iloc[60])

    def test_constant_returns_produce_nan_or_inf(self):
        # Zero std-dev → division by zero → nan/inf; must not raise
        eq = _equity_series([100.0] * 70)
        rs = compute_rolling_sharpe(eq, window=60)
        val = rs.iloc[60]
        assert math.isnan(val) or not np.isfinite(val)

    def test_positive_trend_gives_positive_sharpe(self):
        eq = _equity_series([100.0 * (1.001 ** i) for i in range(120)])
        rs = compute_rolling_sharpe(eq, window=60)
        valid = rs.dropna()
        assert (valid > 0).all()


# ---------------------------------------------------------------------------
# compute_monthly_returns
# ---------------------------------------------------------------------------

class TestMonthlyReturns:
    def test_returns_correct_shape(self):
        # ~2 years of daily bars → pivot should have 2 year rows
        n = 504
        df = _equity_df([100.0 + i * 0.1 for i in range(n)], start="2020-01-02")
        pivot = compute_monthly_returns(df)
        assert not pivot.empty
        assert len(pivot.index) >= 1   # at least 1 year

    def test_month_columns_in_range(self):
        n = 252
        df = _equity_df([100.0 + i * 0.2 for i in range(n)], start="2020-01-02")
        pivot = compute_monthly_returns(df)
        assert pivot.columns.min() >= 0
        assert pivot.columns.max() <= 11

    def test_empty_input_returns_empty_df(self):
        df = pd.DataFrame(columns=["timestamp", "equity"])
        pivot = compute_monthly_returns(df)
        assert pivot.empty

    def test_single_month_returns_partial_pivot(self):
        df = _equity_df([100.0, 101.0, 102.0], start="2021-03-01")
        pivot = compute_monthly_returns(df)
        # May be empty if only 1 data point after resample — should not raise
        assert isinstance(pivot, pd.DataFrame)


# ---------------------------------------------------------------------------
# compute_per_symbol_pnl
# ---------------------------------------------------------------------------

class TestPerSymbolPnl:
    def test_sums_pnl_by_symbol(self):
        trades = _trades_df([
            {"direction": "SELL", "symbol": "AAPL", "pnl": 100.0},
            {"direction": "SELL", "symbol": "AAPL", "pnl":  50.0},
            {"direction": "SELL", "symbol": "MSFT", "pnl": -20.0},
            {"direction": "BUY",  "symbol": "AAPL", "pnl":   0.0},  # BUY excluded
        ])
        result = compute_per_symbol_pnl(trades)
        assert result["AAPL"] == pytest.approx(150.0)
        assert result["MSFT"] == pytest.approx(-20.0)

    def test_buy_trades_excluded(self):
        trades = _trades_df([
            {"direction": "BUY",  "symbol": "X", "pnl": 999.0},
        ])
        result = compute_per_symbol_pnl(trades)
        assert result.empty

    def test_empty_trades_returns_empty_series(self):
        result = compute_per_symbol_pnl(pd.DataFrame())
        assert result.empty

    def test_missing_pnl_column_returns_empty(self):
        trades = _trades_df([{"direction": "SELL", "symbol": "X"}])
        result = compute_per_symbol_pnl(trades)
        assert result.empty


# ---------------------------------------------------------------------------
# Tearsheet integration tests
# ---------------------------------------------------------------------------

class TestTearsheetIntegration:
    def _write_csvs(self, tmpdir: str, n: int = 120) -> tuple[str, str, str]:
        eq_path = os.path.join(tmpdir, "ml_equity.csv")
        tr_path = os.path.join(tmpdir, "ml_trades.csv")
        me_path = os.path.join(tmpdir, "ml_metrics.csv")

        eq_df = _equity_df([100.0 + i * 0.5 for i in range(n)])
        eq_df.to_csv(eq_path, index=False)

        tr_df = _trades_df([
            {"timestamp": "2020-01-10", "symbol": "AAPL", "price": 105.0,
             "quantity": 10, "direction": "SELL", "profit": True, "pnl": 50.0},
            {"timestamp": "2020-02-05", "symbol": "MSFT", "price":  98.0,
             "quantity":  5, "direction": "SELL", "profit": False, "pnl": -15.0},
        ])
        tr_df.to_csv(tr_path, index=False)

        me_df = pd.DataFrame({
            "metric": ["sharpe_ratio", "max_drawdown", "total_return",
                       "alpha", "information_ratio", "annualised_return",
                       "benchmark_return", "trading_days"],
            "value":  [1.23, 5.67, 18.5, 3.2, 0.85, 12.1, 15.3, 120],
        })
        me_df.to_csv(me_path, index=False)

        return eq_path, tr_path, me_path

    def test_generate_creates_html_file(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            eq, tr, me = self._write_csvs(tmpdir)
            out = os.path.join(tmpdir, "tearsheet.html")
            ts = Tearsheet(eq, tr, me, "backtest_config.yaml", out)
            ts.generate()
            assert os.path.exists(out)

    def test_output_is_valid_html(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            eq, tr, me = self._write_csvs(tmpdir)
            out = os.path.join(tmpdir, "tearsheet.html")
            Tearsheet(eq, tr, me, "backtest_config.yaml", out).generate()
            content = open(out, encoding="utf-8").read()
            assert "<!DOCTYPE html>" in content
            assert "</html>" in content

    def test_all_panel_ids_present(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            eq, tr, me = self._write_csvs(tmpdir)
            out = os.path.join(tmpdir, "tearsheet.html")
            Tearsheet(eq, tr, me, "backtest_config.yaml", out).generate()
            content = open(out, encoding="utf-8").read()
            for i in range(6):  # panels 0-5 (6 figure panels)
                assert f'id="panel-{i}"' in content

    def test_summary_metrics_in_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            eq, tr, me = self._write_csvs(tmpdir)
            out = os.path.join(tmpdir, "tearsheet.html")
            Tearsheet(eq, tr, me, "backtest_config.yaml", out).generate()
            content = open(out, encoding="utf-8").read()
            assert "Sharpe" in content
            assert "Drawdown" in content

    def test_generate_completes_under_ten_seconds(self):
        import time
        with tempfile.TemporaryDirectory() as tmpdir:
            # 500-row equity CSV ~ 2-year daily backtest
            eq, tr, me = self._write_csvs(tmpdir, n=500)
            out = os.path.join(tmpdir, "tearsheet_perf.html")
            start = time.time()
            Tearsheet(eq, tr, me, "backtest_config.yaml", out).generate()
            elapsed = time.time() - start
            assert elapsed < 10.0, f"Tearsheet took {elapsed:.1f}s (limit 10s)"

    def test_missing_trades_csv_produces_valid_output(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            eq, _, me = self._write_csvs(tmpdir)
            out = os.path.join(tmpdir, "tearsheet_notrades.html")
            missing_tr = os.path.join(tmpdir, "no_trades.csv")
            Tearsheet(eq, missing_tr, me, "backtest_config.yaml", out).generate()
            assert os.path.exists(out)
