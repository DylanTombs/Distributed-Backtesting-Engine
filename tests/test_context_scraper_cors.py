"""Tests for research/context/scraper.py and research/api/cors.py."""
import sys
import os
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


# ---------------------------------------------------------------------------
# scraper.py
# ---------------------------------------------------------------------------

class TestCleanRawText:
    def test_collapses_whitespace(self):
        from research.context.scraper import clean_raw_text
        result = clean_raw_text("hello   world\n\nfoo")
        assert "  " not in result
        assert result == "hello world foo"

    def test_truncates_to_max_chars(self):
        from research.context.scraper import clean_raw_text
        long_text = "a" * 10_000
        result = clean_raw_text(long_text)
        assert len(result) <= 8_000

    def test_strips_leading_trailing_space(self):
        from research.context.scraper import clean_raw_text
        assert clean_raw_text("  hello  ") == "hello"

    def test_empty_string(self):
        from research.context.scraper import clean_raw_text
        assert clean_raw_text("") == ""


class TestFetchArticle:
    def test_returns_none_when_trafilatura_and_httpx_unavailable(self):
        from research.context.scraper import fetch_article
        with patch.dict(sys.modules, {"trafilatura": None, "httpx": None}):
            result = fetch_article("https://example.com")
        assert result is None

    def test_trafilatura_success_path(self):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.fetch_url.return_value = "<html>content</html>"
        mock_traf.extract.return_value = "Clean article text here"
        with patch.dict(sys.modules, {"trafilatura": mock_traf}):
            result = fetch_article("https://example.com")
        assert result == "Clean article text here"

    def test_trafilatura_none_extract_falls_back_to_httpx(self):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.fetch_url.return_value = "<html/>"
        mock_traf.extract.return_value = None   # trafilatura extracts nothing

        mock_resp = MagicMock()
        mock_resp.ok = True
        mock_resp.text = "Fallback page text"
        mock_resp.raise_for_status.return_value = None

        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_resp

        with patch.dict(sys.modules, {"trafilatura": mock_traf, "httpx": mock_httpx}):
            result = fetch_article("https://example.com")
        assert result == "Fallback page text"

    def test_trafilatura_exception_falls_back_to_httpx(self):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.fetch_url.side_effect = RuntimeError("network error")

        mock_resp = MagicMock()
        mock_resp.text = "httpx fallback text"
        mock_resp.raise_for_status.return_value = None

        mock_httpx = MagicMock()
        mock_httpx.get.return_value = mock_resp

        with patch.dict(sys.modules, {"trafilatura": mock_traf, "httpx": mock_httpx}):
            result = fetch_article("https://example.com")
        assert result == "httpx fallback text"

    def test_httpx_exception_returns_none(self):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.fetch_url.return_value = None
        mock_traf.extract.return_value = None

        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = RuntimeError("connection refused")

        with patch.dict(sys.modules, {"trafilatura": mock_traf, "httpx": mock_httpx}):
            result = fetch_article("https://example.com")
        assert result is None

    def test_truncates_long_article(self):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.fetch_url.return_value = "<html/>"
        mock_traf.extract.return_value = "x" * 20_000
        with patch.dict(sys.modules, {"trafilatura": mock_traf}):
            result = fetch_article("https://example.com")
        assert result is not None
        assert len(result) <= 8_000


# ---------------------------------------------------------------------------
# cors.py — smoke test: add_cors should register middleware without error
# ---------------------------------------------------------------------------

class TestAddCors:
    def test_add_cors_does_not_raise(self):
        pytest.importorskip("fastapi")
        from fastapi import FastAPI
        from research.api.cors import add_cors
        app = FastAPI()
        add_cors(app)     # should not raise
        # Middleware stack is non-empty after registration
        assert len(app.user_middleware) >= 1

    def test_cors_allows_extension_origin_regex(self):
        pytest.importorskip("fastapi")
        from research.api.cors import _ALLOWED_ORIGIN_REGEX
        import re
        re_obj = re.compile(_ALLOWED_ORIGIN_REGEX)
        assert re_obj.match("chrome-extension://abcdefghijklmnop")
        assert not re_obj.match("https://evil.com")
