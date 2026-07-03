"""Branch coverage for entities.py dateparser paths and extractor.py LLM pass."""
import sys
import os
import json
from datetime import datetime
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# entities.py — dateparser branch (lines 93-123)
# ---------------------------------------------------------------------------

class TestExtractDateRangeDateparser:
    def _make_dateparser(self, parsed_date=None):
        """Return a mock dateparser module."""
        mock = MagicMock()
        mock.parse.return_value = parsed_date
        return mock

    def test_explicit_range_text_resolved_by_dateparser(self):
        from research.context.entities import extract_date_range
        mock_dp = MagicMock()
        d1 = datetime(2020, 2, 1)
        d2 = datetime(2020, 4, 30)
        mock_dp.parse.side_effect = [d1, d2]
        with patch.dict(sys.modules, {"dateparser": mock_dp}):
            start, _end = extract_date_range("from February 2020 to April 2020")
        assert start is not None
        assert _end is not None
        assert start <= _end

    def test_single_month_year_expands_window(self):
        from research.context.entities import extract_date_range
        mock_dp = MagicMock()
        mock_dp.parse.return_value = datetime(2020, 3, 1)
        with patch.dict(sys.modules, {"dateparser": mock_dp}):
            start, _end = extract_date_range("Markets crashed in March 2020")
        # ±30 days around March 2020
        assert start is not None and "2020" in start
        assert _end is not None  and "2020" in _end

    def test_dateparser_parse_returns_none_falls_through_to_regex(self):
        from research.context.entities import extract_date_range
        mock_dp = MagicMock()
        mock_dp.parse.return_value = None   # dateparser can't parse it
        with patch.dict(sys.modules, {"dateparser": mock_dp}):
            start, end = extract_date_range("Something happened in 2008")
        # Regex fallback should still return a year-based range
        assert start is not None
        assert "2008" in start

    def test_missing_dateparser_falls_back_to_regex(self):
        from research.context.entities import extract_date_range
        with patch.dict(sys.modules, {"dateparser": None}):
            start, end = extract_date_range("The crash of 2008")
        assert start is not None
        assert "2008" in start

    def test_range_where_d1_after_d2_falls_through(self):
        from research.context.entities import extract_date_range
        mock_dp = MagicMock()
        # Reversed: d1 > d2 skips range branch; falls to single-date (3rd call)
        mock_dp.parse.side_effect = [
            datetime(2020, 5, 1),   # range d1
            datetime(2020, 1, 1),   # range d2 (d1 > d2 → skip)
            datetime(2020, 5, 1),   # single-date fallback
        ]
        with patch.dict(sys.modules, {"dateparser": mock_dp}):
            extract_date_range("from May 2020 to January 2020")
        # Just must not crash; single-date branch returns a window


# ---------------------------------------------------------------------------
# extractor.py — _llm_pass body (lines 138-170)
# ---------------------------------------------------------------------------

# Long enough to clear the _MIN_LLM_TEXT_CHARS guard in _llm_pass
_ARTICLE = (
    "Markets plunged today as investors reacted to the escalating crisis; "
    "financial stocks led the decline across all major indices."
)


class TestLlmPass:
    def _make_anthropic_mock(self, response_json: dict, raw_text: str = None):
        """Return a mock anthropic module whose messages.create returns given JSON."""
        text = raw_text or json.dumps(response_json)
        content_block = MagicMock()
        content_block.text = text

        message = MagicMock()
        message.content = [content_block]

        client = MagicMock()
        client.messages.create.return_value = message

        mock_anthropic = MagicMock()
        mock_anthropic.Anthropic.return_value = client
        return mock_anthropic

    def test_returns_none_without_api_key(self):
        from research.context.extractor import _llm_pass
        with patch.dict(os.environ, {}, clear=True):
            os.environ.pop("ANTHROPIC_API_KEY", None)
            result = _llm_pass("Some financial text")
        assert result is None

    def test_returns_extraction_result_with_api_key(self):
        from research.context.extractor import _llm_pass, ExtractionResult
        payload = {
            "event_label": "COVID crash",
            "tickers": ["AAPL", "MSFT"],
            "date_start": "2020-02-19",
            "date_end": "2020-03-23",
        }
        mock_anthropic = self._make_anthropic_mock(payload)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                result = _llm_pass(_ARTICLE)
        assert isinstance(result, ExtractionResult)
        assert result.event_label == "COVID crash"
        assert "AAPL" in result.tickers
        assert result.source == "llm"
        assert 0.4 <= result.confidence <= 0.80

    def test_strips_markdown_fences(self):
        from research.context.extractor import _llm_pass
        payload = {"event_label": "GFC", "tickers": ["GS"], "date_start": None, "date_end": None}
        raw = "```json\n" + json.dumps(payload) + "\n```"
        mock_anthropic = self._make_anthropic_mock(payload, raw_text=raw)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                result = _llm_pass(_ARTICLE)
        assert result is not None
        assert result.event_label == "GFC"

    def test_tickers_as_string_coerced_to_list(self):
        from research.context.extractor import _llm_pass
        payload = {"event_label": "Test", "tickers": "AAPL", "date_start": None, "date_end": None}
        mock_anthropic = self._make_anthropic_mock(payload)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                result = _llm_pass(_ARTICLE)
        assert isinstance(result.tickers, list)
        assert "AAPL" in result.tickers

    def test_json_parse_error_returns_none(self):
        from research.context.extractor import _llm_pass
        mock_anthropic = self._make_anthropic_mock({}, raw_text="not valid json {{{{")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                result = _llm_pass(_ARTICLE)
        assert result is None

    def test_anthropic_import_error_returns_none(self):
        from research.context.extractor import _llm_pass
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": None}):
                result = _llm_pass(_ARTICLE)
        assert result is None


class TestLlmHardening(TestLlmPass):
    """P1-6/P1-7 regressions: guards on LLM input and validation of output."""

    def _call(self, payload, text=_ARTICLE, raw_text=None):
        from research.context.extractor import _llm_pass
        mock_anthropic = self._make_anthropic_mock(payload, raw_text=raw_text)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                result = _llm_pass(text)
        return result, mock_anthropic

    def test_short_text_skips_paid_llm_call(self):
        result, mock_anthropic = self._call({}, text="Too short")
        assert result is None
        mock_anthropic.Anthropic.assert_not_called()

    def test_client_created_with_timeout_and_retry_cap(self):
        from research.context.extractor import _LLM_TIMEOUT_S, _LLM_MAX_RETRIES
        _, mock_anthropic = self._call({"event_label": "x", "tickers": []})
        kwargs = mock_anthropic.Anthropic.call_args.kwargs
        assert kwargs["timeout"] == _LLM_TIMEOUT_S
        assert kwargs["max_retries"] == _LLM_MAX_RETRIES

    def test_article_text_is_delimited_in_user_message(self):
        _, mock_anthropic = self._call({"event_label": "x", "tickers": []})
        client = mock_anthropic.Anthropic.return_value
        content = client.messages.create.call_args.kwargs["messages"][0]["content"]
        assert content.startswith("<article>")
        assert content.rstrip().endswith("</article>")

    def test_non_string_tickers_are_skipped_not_500(self):
        payload = {
            "event_label": "Test",
            "tickers": ["AAPL", 42, None, {"t": "x"}, "MSFT"],
            "date_start": None,
            "date_end": None,
        }
        result, _ = self._call(payload)
        assert result.tickers == ["AAPL", "MSFT"]

    def test_invalid_ticker_shapes_are_dropped(self):
        payload = {
            "event_label": "Test",
            "tickers": ["../etc", "TOOLONGNAME", "aapl", "<img>"],
            "date_start": None,
            "date_end": None,
        }
        result, _ = self._call(payload)
        # "aapl" upcases to a valid ticker; the rest fail the boundary regex
        assert result.tickers == ["AAPL"]

    def test_invalid_dates_degrade_to_null(self):
        payload = {
            "event_label": "Test",
            "tickers": [],
            "date_start": "not-a-date",
            "date_end": "2020-13-45",
        }
        result, _ = self._call(payload)
        assert result.date_start is None
        assert result.date_end is None

    def test_oversized_label_is_truncated(self):
        payload = {"event_label": "L" * 500, "tickers": [],
                   "date_start": None, "date_end": None}
        result, _ = self._call(payload)
        assert len(result.event_label) <= 120

    def test_non_object_json_is_discarded(self):
        result, _ = self._call({}, raw_text='["a", "list"]')
        assert result is None

    def test_confidence_exact_boundaries_per_adr_031(self):
        # All fields valid → capped at exactly 0.80
        full = {
            "event_label": "COVID crash",
            "tickers": ["AAPL"],
            "date_start": "2020-02-19",
            "date_end": "2020-03-23",
        }
        result, _ = self._call(full)
        assert result.confidence == 0.80

        # All fields null → floor of exactly 0.4
        empty = {"event_label": None, "tickers": [],
                 "date_start": None, "date_end": None}
        result, _ = self._call(empty)
        assert result.confidence == 0.4

    def test_invalid_fields_earn_no_confidence_credit(self):
        # Invalid dates/tickers must not raise confidence above the floor
        payload = {
            "event_label": None,
            "tickers": [12345, "not a ticker!"],
            "date_start": "soonish",
            "date_end": "later",
        }
        result, _ = self._call(payload)
        assert result.confidence == 0.4
