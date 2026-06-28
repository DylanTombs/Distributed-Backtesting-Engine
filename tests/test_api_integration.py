"""Integration tests for POST /api/backtest via FastAPI TestClient.

Tests cover:
- Schema validation (reversed dates, invalid ticker chars)
- Model-not-loaded guard (400)
- Happy path (200, mocked run_backtest)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

from research.api.app import app
from research.api.schemas import BacktestResponse, EquityPoint


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_VALID_PAYLOAD = {
    "tickers": ["AAPL"],
    "date_start": "2020-02-19",
    "date_end": "2020-03-23",
}


def _client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# Validation tests (no mocking — pure schema enforcement)
# ---------------------------------------------------------------------------

class TestBacktestValidation:
    def test_backtest_reversed_dates_returns_422(self):
        """model_validator on BacktestRequest rejects date_start > date_end."""
        resp = _client().post("/api/backtest", json={
            "tickers": ["AAPL"],
            "date_start": "2020-03-23",
            "date_end": "2020-02-19",   # reversed
        })
        assert resp.status_code == 422
        body = resp.json()
        assert "date_start" in str(body).lower() or "date" in str(body).lower()

    def test_backtest_invalid_ticker_returns_422(self):
        """field_validator on BacktestRequest rejects ticker symbols with path chars."""
        resp = _client().post("/api/backtest", json={
            "tickers": ["../evil"],
            "date_start": "2020-02-19",
            "date_end": "2020-03-23",
        })
        assert resp.status_code == 422

    def test_backtest_empty_tickers_returns_422(self):
        resp = _client().post("/api/backtest", json={
            "tickers": [],
            "date_start": "2020-02-19",
            "date_end": "2020-03-23",
        })
        assert resp.status_code == 422

    def test_backtest_missing_tickers_field_returns_422(self):
        resp = _client().post("/api/backtest", json={
            "date_start": "2020-02-19",
            "date_end": "2020-03-23",
        })
        assert resp.status_code == 422


# ---------------------------------------------------------------------------
# Guard: model not loaded
# ---------------------------------------------------------------------------

class TestBacktestModelNotLoaded:
    def test_backtest_model_not_loaded_returns_400(self):
        with patch("research.api.app.is_model_loaded", return_value=False):
            resp = _client().post("/api/backtest", json=_VALID_PAYLOAD)
        assert resp.status_code == 400
        assert "model" in resp.json()["detail"].lower()


# ---------------------------------------------------------------------------
# Happy path
# ---------------------------------------------------------------------------

class TestBacktestHappyPath:
    def _fake_response(self) -> BacktestResponse:
        fake_equity = [
            EquityPoint(date="2020-02-19", equity=100000.0),
            EquityPoint(date="2020-03-23", equity=108000.0),
        ]
        return BacktestResponse(
            run_id="20200323_120000",
            metrics={
                "symbol": "AAPL",
                "total_return_pct": 8.0,
                "sharpe_ratio": 0.42,
                "max_drawdown_pct": 5.1,
                "win_rate_pct": 65.0,
                "days": 23,
                "n_trades": 4,
            },
            equity=fake_equity,
            trades=[],
            cached=False,
            warning=None,
        )

    def test_backtest_happy_path_returns_200(self):
        fake_response = self._fake_response()
        with patch("research.api.app.is_model_loaded", return_value=True), \
             patch("research.api.app.run_backtest", return_value=fake_response):
            resp = _client().post("/api/backtest", json={
                "tickers": ["aapl"],          # lowercase — validator upcases
                "date_start": "2020-02-19",
                "date_end": "2020-03-23",
            })
        assert resp.status_code == 200
        body = resp.json()
        assert body["run_id"] == "20200323_120000"
        assert body["metrics"]["total_return_pct"] == 8.0
        assert len(body["equity"]) == 2
        assert not body["cached"]

    def test_backtest_happy_path_warning_field_present(self):
        """Response always includes a 'warning' field (may be null)."""
        fake_response = self._fake_response()
        with patch("research.api.app.is_model_loaded", return_value=True), \
             patch("research.api.app.run_backtest", return_value=fake_response):
            resp = _client().post("/api/backtest", json=_VALID_PAYLOAD)
        body = resp.json()
        assert "warning" in body
        assert body["warning"] is None

    def test_backtest_runtime_error_returns_500(self):
        """run_backtest raising RuntimeError maps to 500."""
        with patch("research.api.app.is_model_loaded", return_value=True), \
             patch("research.api.app.run_backtest", side_effect=RuntimeError("binary missing")):
            resp = _client().post("/api/backtest", json=_VALID_PAYLOAD)
        assert resp.status_code == 500
        assert "binary missing" in resp.json()["detail"]
