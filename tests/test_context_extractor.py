"""Tests for research/context/extractor.py."""
import sys
import os
from unittest.mock import patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from research.context.extractor import (
    ExtractionResult,
    extract,
    _rule_pass,
    CONFIDENCE_THRESHOLD,
)


class TestRulePass:
    def test_returns_extraction_result(self):
        result = _rule_pass("The COVID-19 crash wiped out airline stocks")
        assert isinstance(result, ExtractionResult)

    def test_detects_covid_event(self):
        result = _rule_pass("COVID crash pandemic selloff march 2020")
        assert result.event_key == "covid_crash"
        assert result.confidence >= CONFIDENCE_THRESHOLD

    def test_detects_tickers(self):
        result = _rule_pass("AAPL and MSFT fell during the crash")
        assert "AAPL" in result.tickers
        assert "MSFT" in result.tickers

    def test_no_event_gives_low_confidence(self):
        result = _rule_pass("The cat sat on the mat")
        assert result.confidence < CONFIDENCE_THRESHOLD

    def test_source_is_rules(self):
        result = _rule_pass("Any text at all")
        assert result.source == "rules"

    def test_event_provides_dates(self):
        result = _rule_pass("The dot-com crash was brutal for CSCO")
        assert result.date_start is not None
        assert result.date_end is not None

    def test_event_and_tickers_boosts_confidence(self):
        result_event_only = _rule_pass("The dot-com crash")
        result_with_tickers = _rule_pass("The dot-com crash hit CSCO INTC MSFT hard")
        assert result_with_tickers.confidence >= result_event_only.confidence


class TestExtract:
    def test_high_confidence_skips_llm(self):
        text = "COVID crash pandemic selloff march 2020 AAPL MSFT AMZN"
        with patch("research.context.extractor._llm_pass") as mock_llm:
            result = extract(text)
        mock_llm.assert_not_called()  # noqa: SIM115
        assert result.event_key == "covid_crash"

    def test_low_confidence_triggers_llm(self):
        """When rule pass returns low confidence, LLM fallback is called."""
        text = "something financial happened recently"
        with patch("research.context.extractor._llm_pass") as mock_llm:
            mock_llm.return_value = None  # LLM returns nothing useful
            extract(text)
        mock_llm.assert_called_once()

    def test_llm_result_merged_when_rules_low(self):
        text = "something financial happened recently"
        llm_result = ExtractionResult(
            event_label="Test Event",
            event_key=None,
            tickers=["AAPL"],
            date_start="2020-03-01",
            date_end="2020-03-31",
            confidence=0.75,
            source="llm",
        )
        with patch("research.context.extractor._llm_pass", return_value=llm_result):
            result = extract(text)
        assert result.event_label == "Test Event"
        assert result.source == "rules+llm"

    def test_llm_none_falls_back_to_rules(self):
        text = "something financial happened recently"
        with patch("research.context.extractor._llm_pass", return_value=None):
            result = extract(text)
        assert result.source == "rules"

    def test_returns_extraction_result(self):
        result = extract("The financial crisis hit banks hard")
        assert isinstance(result, ExtractionResult)
        assert 0.0 <= result.confidence <= 1.0


class TestMergeSemantics:
    """P2-7: merged responses must stay internally consistent."""

    def _rule(self, **overrides) -> ExtractionResult:
        base = dict(
            event_label="COVID-19 Crash",
            event_key="covid_crash",
            tickers=[],
            date_start="2020-02-19",
            date_end="2020-03-23",
            confidence=0.55,
            source="rules",
        )
        return ExtractionResult(**{**base, **overrides})

    def _llm(self, **overrides) -> ExtractionResult:
        base = dict(
            event_label=None,
            event_key=None,
            tickers=["AAPL"],
            date_start="2021-01-01",
            date_end="2021-06-30",
            confidence=0.70,
            source="llm",
        )
        return ExtractionResult(**{**base, **overrides})

    def test_differing_llm_label_drops_stale_event_key(self):
        from research.context.extractor import _merge
        merged = _merge(self._rule(), self._llm(event_label="Something Else"))
        assert merged.event_key is None
        assert merged.event_label == "Something Else"

    def test_curated_dates_win_over_llm_dates_when_event_stands(self):
        from research.context.extractor import _merge
        merged = _merge(self._rule(), self._llm())
        assert merged.event_key == "covid_crash"
        assert merged.event_label == "COVID-19 Crash"
        assert merged.date_start == "2020-02-19"
        assert merged.date_end == "2020-03-23"

    def test_matching_labels_keep_curated_key_and_window(self):
        from research.context.extractor import _merge
        merged = _merge(
            self._rule(), self._llm(event_label="COVID-19 Crash")
        )
        assert merged.event_key == "covid_crash"
        assert merged.date_start == "2020-02-19"

    def test_no_rule_event_takes_llm_fields(self):
        from research.context.extractor import _merge
        rule = self._rule(event_key=None, event_label=None,
                          date_start=None, date_end=None, confidence=0.2)
        merged = _merge(rule, self._llm(event_label="New Event"))
        assert merged.event_key is None
        assert merged.event_label == "New Event"
        assert merged.date_start == "2021-01-01"

    def test_confidence_is_max_of_both_passes(self):
        from research.context.extractor import _merge
        merged = _merge(self._rule(confidence=0.55), self._llm(confidence=0.70))
        assert merged.confidence == 0.70


class TestApiSchemas:
    """Smoke tests for API schema validation."""

    def test_context_request_needs_content(self):
        from research.api.schemas import ContextRequest
        req = ContextRequest(url=None, raw_text=None)
        assert not req.has_content()

    def test_context_request_with_url(self):
        from research.api.schemas import ContextRequest
        req = ContextRequest(url="https://example.com")
        assert req.has_content()

    def test_backtest_request_upcases_tickers(self):
        from research.api.schemas import BacktestRequest
        req = BacktestRequest(tickers=["aapl", "msft"],
                              date_start="2020-02-19", date_end="2020-03-23")
        assert req.tickers == ["AAPL", "MSFT"]

    def test_backtest_request_empty_tickers_raises(self):
        from pydantic import ValidationError
        from research.api.schemas import BacktestRequest
        with pytest.raises(ValidationError):
            BacktestRequest(tickers=[], date_start="2020-02-19", date_end="2020-03-23")
