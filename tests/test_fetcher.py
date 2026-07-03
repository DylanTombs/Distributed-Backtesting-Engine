"""Unit tests for on-demand OHLCV fetching (Phase 8.1, ADR-045).

All tests run offline: the autouse conftest fixture sets
DATA_FETCH_DISABLED=1; network-path tests re-enable it and mock httpx.
"""
from __future__ import annotations

import sys
from unittest.mock import MagicMock, patch

from research.data import fetcher

_CSV = (
    "Date,Open,High,Low,Close,Volume\n"
    "2020-03-02,99,101,98,100,1000\n"
    "2020-03-03,100,102,99,101,1000\n"
    "2020-03-04,101,103,100,102,1000\n"
)


def _sandbox(monkeypatch, tmp_path):
    ohlcv = tmp_path / "ohlcv"
    legacy = tmp_path / "legacy"
    ohlcv.mkdir()
    legacy.mkdir()
    monkeypatch.setattr(fetcher, "OHLCV_DIR", ohlcv)
    monkeypatch.setattr(fetcher, "LEGACY_DATA_DIR", legacy)
    return ohlcv, legacy


class TestCacheResolution:
    def test_covering_cache_is_used_without_fetch(self, monkeypatch, tmp_path):
        ohlcv, _ = _sandbox(monkeypatch, tmp_path)
        (ohlcv / "AAPL.csv").write_text(_CSV)
        path = fetcher.get_ohlcv_csv("AAPL", "2020-03-03")
        assert path == ohlcv / "AAPL.csv"

    def test_legacy_csv_is_used_when_cache_missing(self, monkeypatch, tmp_path):
        _, legacy = _sandbox(monkeypatch, tmp_path)
        (legacy / "AAPL.csv").write_text(_CSV)
        path = fetcher.get_ohlcv_csv("AAPL", "2020-03-03")
        assert path == legacy / "AAPL.csv"

    def test_no_data_returns_none_when_fetch_disabled(self, monkeypatch, tmp_path):
        _sandbox(monkeypatch, tmp_path)
        assert fetcher.get_ohlcv_csv("ZZZZ", "2020-03-03") is None

    def test_malformed_ticker_rejected(self, monkeypatch, tmp_path):
        _sandbox(monkeypatch, tmp_path)
        assert fetcher.get_ohlcv_csv("../etc", "2020-03-03") is None
        assert fetcher.get_ohlcv_csv("TOOLONGNAME", "2020-03-03") is None

    def test_stale_cache_returned_when_fetch_disabled(self, monkeypatch, tmp_path):
        """A cache that doesn't cover date_end still beats nothing offline."""
        ohlcv, _ = _sandbox(monkeypatch, tmp_path)
        (ohlcv / "AAPL.csv").write_text(_CSV)
        path = fetcher.get_ohlcv_csv("AAPL", "2099-01-01")
        assert path == ohlcv / "AAPL.csv"


class TestNetworkFetch:
    def _fetch(self, monkeypatch, tmp_path, body, status=200):
        ohlcv, _ = _sandbox(monkeypatch, tmp_path)
        monkeypatch.delenv("DATA_FETCH_DISABLED", raising=False)

        resp = MagicMock()
        resp.text = body
        resp.status_code = status
        resp.raise_for_status.return_value = None
        mock_httpx = MagicMock()
        mock_httpx.get.return_value = resp
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            path = fetcher.get_ohlcv_csv("NVDA", "2020-03-03")
        return path, ohlcv, mock_httpx

    def test_successful_fetch_writes_cache(self, monkeypatch, tmp_path):
        path, ohlcv, mock_httpx = self._fetch(monkeypatch, tmp_path, _CSV)
        assert path == ohlcv / "NVDA.csv"
        assert path.read_text() == _CSV
        url = mock_httpx.get.call_args.args[0]
        assert "nvda.us" in url

    def test_provider_no_data_returns_none(self, monkeypatch, tmp_path):
        path, _, _ = self._fetch(monkeypatch, tmp_path, "No data")
        assert path is None

    def test_html_error_page_rejected(self, monkeypatch, tmp_path):
        path, _, _ = self._fetch(
            monkeypatch, tmp_path, "<html><body>rate limited</body></html>"
        )
        assert path is None

    def test_network_error_returns_none(self, monkeypatch, tmp_path):
        _sandbox(monkeypatch, tmp_path)
        monkeypatch.delenv("DATA_FETCH_DISABLED", raising=False)
        mock_httpx = MagicMock()
        mock_httpx.get.side_effect = RuntimeError("connection refused")
        with patch.dict(sys.modules, {"httpx": mock_httpx}):
            assert fetcher.get_ohlcv_csv("NVDA", "2020-03-03") is None

    def test_dotted_ticker_maps_to_dashed_stooq_symbol(self, monkeypatch, tmp_path):
        assert fetcher._stooq_symbol("BRK.B") == "brk-b.us"
        assert fetcher._stooq_symbol("AAPL") == "aapl.us"


class TestCsvValidation:
    def test_valid_csv_counts_rows(self):
        assert fetcher._validate_csv(_CSV) == 3

    def test_missing_header_rejected(self):
        assert fetcher._validate_csv("a,b,c\n1,2,3\n") is None

    def test_non_date_first_column_rejected(self):
        bad = "Date,Open,High,Low,Close,Volume\nnot-a-date,1,2,3,4,5\n"
        assert fetcher._validate_csv(bad) is None

    def test_empty_body_rejected(self):
        assert fetcher._validate_csv("") is None
        assert fetcher._validate_csv("Date,Open,High,Low,Close,Volume\n") is None
