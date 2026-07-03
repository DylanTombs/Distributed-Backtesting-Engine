"""Route-level tests for POST /api/context (P2-9).

Before this file the endpoint being exposed publicly in Phase 7 had zero
route-level coverage: the has_content 422, the fetch-failure 422, the 0.15
confidence floor, and the happy-path field mapping were all untested.
"""
from __future__ import annotations

from unittest.mock import patch

from fastapi.testclient import TestClient

from research.api.app import app
from research.context.extractor import ExtractionResult


def _client() -> TestClient:
    return TestClient(app)


def _result(**overrides) -> ExtractionResult:
    base = dict(
        event_label="COVID-19 Crash",
        event_key="covid_crash",
        tickers=["AAPL", "MSFT"],
        date_start="2020-02-19",
        date_end="2020-03-23",
        confidence=0.85,
        source="rules",
    )
    return ExtractionResult(**{**base, **overrides})


_TEXT = "Markets plunged as the pandemic selloff accelerated across indices."


class TestContextValidation:
    def test_neither_url_nor_raw_text_returns_422(self):
        resp = _client().post("/api/context", json={})
        assert resp.status_code == 422
        assert "url or raw_text" in resp.json()["detail"]

    def test_empty_strings_count_as_no_content(self):
        resp = _client().post("/api/context", json={"url": None, "raw_text": ""})
        assert resp.status_code == 422


class TestContextFetchFailure:
    def test_unfetchable_url_returns_422_with_fallback_hint(self):
        with patch("research.context.scraper.fetch_article", return_value=None):
            resp = _client().post(
                "/api/context", json={"url": "https://example.com/article"}
            )
        assert resp.status_code == 422
        assert "raw_text" in resp.json()["detail"]


class TestConfidenceFloor:
    def test_confidence_below_floor_returns_422(self):
        with patch(
            "research.context.extractor.extract",
            return_value=_result(confidence=0.10, event_key=None,
                                 event_label=None, tickers=[]),
        ):
            resp = _client().post("/api/context", json={"raw_text": _TEXT})
        assert resp.status_code == 422
        assert "No financial context" in resp.json()["detail"]

    def test_confidence_at_floor_passes(self):
        with patch(
            "research.context.extractor.extract",
            return_value=_result(confidence=0.15),
        ):
            resp = _client().post("/api/context", json={"raw_text": _TEXT})
        assert resp.status_code == 200


class TestContextHappyPath:
    def test_extraction_fields_mapped_to_response(self):
        with patch(
            "research.context.extractor.extract", return_value=_result()
        ):
            resp = _client().post("/api/context", json={"raw_text": _TEXT})
        assert resp.status_code == 200
        body = resp.json()
        assert body["event_key"] == "covid_crash"
        assert body["event_label"] == "COVID-19 Crash"
        assert body["tickers"] == ["AAPL", "MSFT"]
        assert body["date_start"] == "2020-02-19"
        assert body["date_end"] == "2020-03-23"
        assert body["confidence"] == 0.85
        assert body["source"] == "rules"

    def test_raw_text_takes_priority_over_url(self):
        with patch(
            "research.context.extractor.extract", return_value=_result()
        ), patch("research.context.scraper.fetch_article") as mock_fetch:
            resp = _client().post("/api/context", json={
                "raw_text": _TEXT,
                "url": "https://example.com/article",
            })
        assert resp.status_code == 200
        mock_fetch.assert_not_called()
