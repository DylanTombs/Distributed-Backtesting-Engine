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

    def test_multi_megabyte_payload_is_sliced_before_regex(self):
        from research.context.scraper import clean_raw_text, _PRE_CLEAN_CAP
        # Content beyond the pre-clean cap must never survive into the result
        text = "a" * _PRE_CLEAN_CAP + "SENTINEL"
        result = clean_raw_text(text)
        assert "SENTINEL" not in result
        assert len(result) <= 8_000


def _fake_getaddrinfo(ip: str):
    """Return a getaddrinfo stub resolving every hostname to ``ip``."""
    def fake(host, port, **kwargs):
        return [(2, 1, 6, "", (ip, 0))]
    return fake


@pytest.fixture
def public_dns(monkeypatch):
    """Resolve all hostnames to a globally routable address (no real DNS)."""
    from research.context import scraper
    monkeypatch.setattr(scraper.socket, "getaddrinfo", _fake_getaddrinfo("93.184.216.34"))


def _mock_response(status_code=200, text="<html>body</html>"):
    resp = MagicMock()
    resp.status_code = status_code
    resp.text = text
    resp.raise_for_status.return_value = None
    return resp


# ---------------------------------------------------------------------------
# SSRF validation (P1-1)
# ---------------------------------------------------------------------------

class TestSsrfValidation:
    @pytest.mark.parametrize("url", [
        "file:///etc/passwd",
        "ftp://example.com/x",
        "javascript:alert(1)",
        "gopher://example.com",
        "http://",                      # no hostname
        "not a url at all",
    ])
    def test_rejects_disallowed_schemes_and_missing_host(self, url):
        from research.context.scraper import _is_public_http_url
        assert _is_public_http_url(url) is False

    @pytest.mark.parametrize("ip", [
        "169.254.169.254",   # cloud metadata
        "127.0.0.1",         # loopback
        "10.0.0.1",          # RFC 1918
        "192.168.1.1",       # RFC 1918
        "0.0.0.0",           # unspecified
        "::1",               # IPv6 loopback
    ])
    def test_rejects_non_global_ip_literals(self, ip):
        from research.context.scraper import _is_public_http_url
        host = f"[{ip}]" if ":" in ip else ip
        assert _is_public_http_url(f"http://{host}/latest/meta-data") is False

    def test_rejects_hostname_resolving_to_private_address(self, monkeypatch):
        from research.context import scraper
        monkeypatch.setattr(
            scraper.socket, "getaddrinfo", _fake_getaddrinfo("10.0.0.5")
        )
        assert scraper._is_public_http_url("https://internal.example.com") is False

    def test_rejects_unresolvable_hostname(self, monkeypatch):
        from research.context import scraper

        def raise_gaierror(host, port, **kwargs):
            raise OSError("resolution failed")

        monkeypatch.setattr(scraper.socket, "getaddrinfo", raise_gaierror)
        assert scraper._is_public_http_url("https://nx.example.com") is False

    def test_accepts_hostname_resolving_to_global_address(self, public_dns):
        from research.context.scraper import _is_public_http_url
        assert _is_public_http_url("https://example.com/article") is True

    def test_fetch_refuses_redirects(self, public_dns):
        from research.context.scraper import fetch_article
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = _mock_response(status_code=302, text="")
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            assert fetch_article("https://example.com") is None

    def test_fetch_never_called_for_rejected_url(self):
        from research.context.scraper import fetch_article
        mock_httpx = MagicMock()
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            result = fetch_article("http://169.254.169.254/latest/meta-data")
        assert result is None
        mock_httpx.get.assert_not_called()

    def test_fetch_disables_follow_redirects(self, public_dns):
        from research.context.scraper import fetch_article
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = _mock_response()
        with patch.dict(sys.modules, {"httpx": mock_httpx, "trafilatura": None}):
            fetch_article("https://example.com")
        assert mock_httpx.get.call_args.kwargs["follow_redirects"] is False


class TestFetchArticle:
    def test_returns_none_when_httpx_unavailable(self, public_dns):
        from research.context.scraper import fetch_article
        with patch.dict(sys.modules, {"trafilatura": None, "httpx": None}):
            result = fetch_article("https://example.com")
        assert result is None

    def test_trafilatura_extracts_from_fetched_html(self, public_dns):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.extract.return_value = "Clean article text here"
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = _mock_response(text="<html>content</html>")
        with patch.dict(sys.modules, {"trafilatura": mock_traf, "httpx": mock_httpx}):
            result = fetch_article("https://example.com")
        assert result == "Clean article text here"
        # trafilatura must only extract from HTML we downloaded — never fetch
        mock_traf.extract.assert_called_once()
        mock_traf.fetch_url.assert_not_called()

    def test_trafilatura_none_extract_returns_raw_body(self, public_dns):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.extract.return_value = None
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = _mock_response(text="Fallback page text")
        with patch.dict(sys.modules, {"trafilatura": mock_traf, "httpx": mock_httpx}):
            result = fetch_article("https://example.com")
        assert result == "Fallback page text"

    def test_trafilatura_exception_returns_raw_body(self, public_dns):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.extract.side_effect = RuntimeError("parse error")
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = _mock_response(text="raw body text")
        with patch.dict(sys.modules, {"trafilatura": mock_traf, "httpx": mock_httpx}):
            result = fetch_article("https://example.com")
        assert result == "raw body text"

    def test_httpx_exception_returns_none(self, public_dns):
        from research.context.scraper import fetch_article
        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = RuntimeError("connection refused")
        with patch.dict(sys.modules, {"trafilatura": None, "httpx": mock_httpx}):
            result = fetch_article("https://example.com")
        assert result is None

    def test_truncates_long_article(self, public_dns):
        from research.context.scraper import fetch_article
        mock_traf = MagicMock()
        mock_traf.extract.return_value = "x" * 20_000
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = _mock_response()
        with patch.dict(sys.modules, {"trafilatura": mock_traf, "httpx": mock_httpx}):
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
