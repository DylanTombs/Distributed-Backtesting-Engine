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
                result = _llm_pass("The COVID crash hit markets hard")
        assert isinstance(result, ExtractionResult)
        assert result.event_label == "COVID crash"
        assert "AAPL" in result.tickers
        assert result.source == "llm"

    def test_strips_markdown_fences(self):
        from research.context.extractor import _llm_pass
        payload = {"event_label": "GFC", "tickers": ["GS"], "date_start": None, "date_end": None}
        raw = "```json\n" + json.dumps(payload) + "\n```"
        mock_anthropic = self._make_anthropic_mock(payload, raw_text=raw)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                result = _llm_pass("Lehman Brothers collapsed")
        assert result is not None
        assert result.event_label == "GFC"

    def test_tickers_as_string_coerced_to_list(self):
        from research.context.extractor import _llm_pass
        payload = {"event_label": "Test", "tickers": "AAPL", "date_start": None, "date_end": None}
        mock_anthropic = self._make_anthropic_mock(payload)
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                result = _llm_pass("Some text")
        assert isinstance(result.tickers, list)
        assert "AAPL" in result.tickers

    def test_json_parse_error_returns_none(self):
        from research.context.extractor import _llm_pass
        mock_anthropic = self._make_anthropic_mock({}, raw_text="not valid json {{{{")
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": mock_anthropic}):
                result = _llm_pass("Some text")
        assert result is None

    def test_anthropic_import_error_returns_none(self):
        from research.context.extractor import _llm_pass
        with patch.dict(os.environ, {"ANTHROPIC_API_KEY": "test-key"}):
            with patch.dict(sys.modules, {"anthropic": None}):
                result = _llm_pass("Some text")
        assert result is None
