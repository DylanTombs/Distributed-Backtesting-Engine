"""Tests for Phase 7.2 — API key auth, rate limiting, and CORS updates.

Covers:
- require_api_key: dev-mode allow-all, 401 on missing/invalid, pass on valid
- rate limiting: 11th /api/backtest within a minute from one IP → 429
- CORS: X-API-Key preflight-approved (P1-2); extension-ID pinning (P1-3)
"""
from __future__ import annotations

from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

import research.api.auth as auth
from research.api.app import app

_PAYLOAD = {
    "tickers": ["AAPL"],
    "date_start": "2020-02-19",
    "date_end": "2020-03-23",
}


def _client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------------------
# API key authentication
# ---------------------------------------------------------------------------

class TestApiKeyAuth:
    def test_dev_mode_no_keys_configured_allows_all(self, monkeypatch):
        monkeypatch.setattr(auth, "API_KEYS", frozenset())
        resp = _client().post("/api/context", json={"raw_text": "x" * 100})
        assert resp.status_code != 401

    def test_missing_key_returns_401(self, monkeypatch):
        monkeypatch.setattr(auth, "API_KEYS", frozenset({"key_valid"}))
        resp = _client().post("/api/backtest", json=_PAYLOAD)
        assert resp.status_code == 401

    def test_invalid_key_returns_401(self, monkeypatch):
        monkeypatch.setattr(auth, "API_KEYS", frozenset({"key_valid"}))
        resp = _client().post(
            "/api/backtest", json=_PAYLOAD, headers={"X-API-Key": "key_wrong"}
        )
        assert resp.status_code == 401

    def test_valid_key_passes_auth(self, monkeypatch):
        monkeypatch.setattr(auth, "API_KEYS", frozenset({"key_valid"}))
        with patch("research.api.app.is_model_loaded", return_value=False):
            resp = _client().post(
                "/api/backtest",
                json={**_PAYLOAD, "strategy": {"template": "ml_transformer"}},
                headers={"X-API-Key": "key_valid"},
            )
        # 400 (ML model not deployed) proves the request got past auth
        assert resp.status_code == 400

    def test_context_also_requires_key(self, monkeypatch):
        monkeypatch.setattr(auth, "API_KEYS", frozenset({"key_valid"}))
        resp = _client().post("/api/context", json={"raw_text": "x" * 100})
        assert resp.status_code == 401

    def test_health_is_exempt_from_auth(self, monkeypatch):
        monkeypatch.setattr(auth, "API_KEYS", frozenset({"key_valid"}))
        resp = _client().get("/api/health")
        assert resp.status_code == 200

    def test_load_keys_parses_and_strips(self, monkeypatch):
        monkeypatch.setenv("API_KEYS", " key_a , key_b ,, ")
        assert auth._load_keys() == frozenset({"key_a", "key_b"})

    def test_load_keys_empty_env(self, monkeypatch):
        monkeypatch.delenv("API_KEYS", raising=False)
        assert auth._load_keys() == frozenset()


# ---------------------------------------------------------------------------
# Rate limiting
# ---------------------------------------------------------------------------

@pytest.fixture
def rate_limiter_enabled():
    from research.api.rate_limit import limiter

    limiter.reset()
    limiter.enabled = True
    yield limiter
    limiter.enabled = False
    limiter.reset()


class TestRateLimiting:
    def test_backtest_returns_429_after_limit(self, rate_limiter_enabled):
        client = _client()
        ml_payload = {**_PAYLOAD, "strategy": {"template": "ml_transformer"}}
        with patch("research.api.app.is_model_loaded", return_value=False):
            statuses = [
                client.post("/api/backtest", json=ml_payload).status_code
                for _ in range(11)
            ]
        # First 10 pass the limiter (400: ML model not deployed); the 11th is
        # cut off by the rate limit
        assert statuses[:10] == [400] * 10
        assert statuses[10] == 429

    def test_health_is_exempt_from_rate_limit(self, rate_limiter_enabled):
        client = _client()
        statuses = [client.get("/api/health").status_code for _ in range(40)]
        assert all(s == 200 for s in statuses)


# ---------------------------------------------------------------------------
# CORS: X-API-Key preflight (P1-2) and extension-ID pinning (P1-3)
# ---------------------------------------------------------------------------

class TestCors:
    def test_preflight_approves_x_api_key_header(self):
        resp = _client().options(
            "/api/backtest",
            headers={
                "Origin": "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "X-API-Key,Content-Type",
            },
        )
        assert resp.status_code == 200
        allowed = resp.headers.get("access-control-allow-headers", "")
        assert "x-api-key" in allowed.lower()

    def test_unpinned_regex_matches_any_extension(self, monkeypatch):
        from research.api import cors

        monkeypatch.delenv("ALLOWED_EXTENSION_IDS", raising=False)
        assert cors._build_origin_regex() == r"chrome-extension://.*"

    def test_pinned_regex_matches_only_configured_ids(self, monkeypatch):
        import re

        from research.api import cors

        monkeypatch.setenv(
            "ALLOWED_EXTENSION_IDS",
            "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa, bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb",
        )
        regex = re.compile(cors._build_origin_regex())
        assert regex.fullmatch("chrome-extension://aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
        assert regex.fullmatch("chrome-extension://bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb")
        assert not regex.fullmatch("chrome-extension://cccccccccccccccccccccccccccccccc")
