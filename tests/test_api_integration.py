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

class TestRequestSizeLimits:
    """P1-8: oversized payloads are rejected at the boundary."""

    def test_oversized_raw_text_returns_422(self):
        from research.api.schemas import MAX_RAW_TEXT_CHARS
        resp = _client().post("/api/context", json={
            "raw_text": "a" * (MAX_RAW_TEXT_CHARS + 1),
        })
        assert resp.status_code == 422

    def test_too_many_tickers_returns_422(self):
        from research.api.schemas import MAX_TICKERS
        resp = _client().post("/api/backtest", json={
            **_VALID_PAYLOAD,
            "tickers": ["AAPL"] * (MAX_TICKERS + 1),
        })
        assert resp.status_code == 422

    def test_oversized_body_returns_413(self):
        from research.api.app import MAX_BODY_BYTES
        resp = _client().post(
            "/api/context",
            content=b"x" * (MAX_BODY_BYTES + 1),
            headers={"Content-Type": "application/json"},
        )
        assert resp.status_code == 413

    def test_non_http_url_scheme_returns_422(self):
        resp = _client().post("/api/context", json={
            "url": "file:///etc/passwd",
        })
        assert resp.status_code == 422


class TestStrategyValidation:
    """Phase 8.3: malformed strategy specs fail at the boundary with 422."""

    def test_unknown_template_returns_422(self):
        resp = _client().post("/api/backtest", json={
            **_VALID_PAYLOAD,
            "strategy": {"template": "arbitrary_python"},
        })
        assert resp.status_code == 422

    def test_cross_against_constant_returns_422(self):
        resp = _client().post("/api/backtest", json={
            **_VALID_PAYLOAD,
            "strategy": {"rules": {"entry": [{
                "indicator": "SMA", "period": 5,
                "op": "crosses_above", "value": 100,
            }]}},
        })
        assert resp.status_code == 422

    def test_template_and_rules_together_returns_422(self):
        resp = _client().post("/api/backtest", json={
            **_VALID_PAYLOAD,
            "strategy": {
                "template": "buy_hold",
                "rules": {"entry": [{"indicator": "PRICE", "op": ">",
                                     "value": 0}]},
            },
        })
        assert resp.status_code == 422

    def test_too_many_conditions_returns_422(self):
        cond = {"indicator": "PRICE", "op": ">", "value": 0}
        resp = _client().post("/api/backtest", json={
            **_VALID_PAYLOAD,
            "strategy": {"rules": {"entry": [cond] * 9}},
        })
        assert resp.status_code == 422


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
    def test_ml_strategy_without_model_returns_400(self):
        """The model gate applies only to the experimental ML path (8.3)."""
        with patch("research.api.app.is_model_loaded", return_value=False):
            resp = _client().post("/api/backtest", json={
                **_VALID_PAYLOAD,
                "strategy": {"template": "ml_transformer"},
            })
        assert resp.status_code == 400
        assert "model" in resp.json()["detail"].lower()

    def test_default_strategy_needs_no_model(self):
        """Transparent strategies must run on servers with no ML artefacts."""
        with patch("research.api.app.is_model_loaded", return_value=False), \
             patch("research.api.app.run_backtest") as mock_run:
            mock_run.return_value = BacktestResponse(
                run_id="r", metrics={}, equity=[], trades=[])
            resp = _client().post("/api/backtest", json=_VALID_PAYLOAD)
        assert resp.status_code == 200
        mock_run.assert_called_once()


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

    def test_backtest_runtime_error_returns_sanitised_500(self):
        """P2-1: server-fault details (paths, stderr) never reach the client."""
        secret_detail = "ml_backtest failed: /Users/someone/secret/path stderr dump"
        with patch("research.api.app.is_model_loaded", return_value=True), \
             patch("research.api.app.run_backtest", side_effect=RuntimeError(secret_detail)):
            resp = _client().post("/api/backtest", json=_VALID_PAYLOAD)
        assert resp.status_code == 500
        assert "secret" not in resp.json()["detail"]
        assert "/Users" not in resp.json()["detail"]

    def test_backtest_input_error_returns_422(self):
        """P2-1: client-input problems are 4xx, not 500."""
        from research.api.runner import BacktestInputError
        msg = "No data for AAPL in [1980-01-01 → 1980-02-01]."
        with patch("research.api.app.is_model_loaded", return_value=True), \
             patch("research.api.app.run_backtest", side_effect=BacktestInputError(msg)):
            resp = _client().post("/api/backtest", json=_VALID_PAYLOAD)
        assert resp.status_code == 422
        assert msg in resp.json()["detail"]

    def test_backtest_unexpected_exception_returns_generic_500(self):
        """P2-2: non-RuntimeError surprises (e.g. wrong-arch binary → OSError)
        map to a generic 500 instead of escaping as a bare exception."""
        with patch("research.api.app.is_model_loaded", return_value=True), \
             patch("research.api.app.run_backtest",
                   side_effect=OSError(8, "Exec format error")):
            resp = _client().post("/api/backtest", json=_VALID_PAYLOAD)
        assert resp.status_code == 500
        assert "Exec format" not in resp.json()["detail"]

    def test_context_unexpected_exception_returns_generic_500(self):
        """P2-2: adversarial text crashing the extractor yields a generic 500."""
        with patch("research.context.extractor.extract",
                   side_effect=ValueError("boom from adversarial text")):
            resp = _client().post("/api/context", json={"raw_text": "x" * 100})
        assert resp.status_code == 500
        assert "boom" not in resp.json()["detail"]


class TestWarmupGate:
    """Cloud Run scale-to-zero: warm-up must be skippable via env (10.1)."""

    def test_warmup_enabled_by_default(self, monkeypatch):
        from research.api.app import warmup_enabled
        monkeypatch.delenv("CACHE_WARMUP_DISABLED", raising=False)
        assert warmup_enabled() is True

    def test_warmup_disabled_via_env(self, monkeypatch):
        from research.api.app import warmup_enabled
        for value in ("1", "true", "yes"):
            monkeypatch.setenv("CACHE_WARMUP_DISABLED", value)
            assert warmup_enabled() is False
