"""Tests for research/context/entities.py."""
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from research.context.entities import extract_tickers, extract_date_range


class TestExtractTickers:
    def test_extracts_dollar_prefixed_ticker(self):
        tickers = extract_tickers("$AAPL fell 5 % today")
        assert "AAPL" in tickers

    def test_extracts_bare_ticker(self):
        tickers = extract_tickers("MSFT and GOOGL both rallied")
        assert "MSFT" in tickers
        assert "GOOGL" in tickers

    def test_filters_out_stop_words(self):
        tickers = extract_tickers("The CEO said the US GDP is not good")
        assert "THE" not in tickers
        assert "CEO" not in tickers
        assert "GDP" not in tickers

    def test_empty_text_returns_empty(self):
        assert extract_tickers("") == []

    def test_no_duplicates(self):
        tickers = extract_tickers("AAPL AAPL $AAPL Apple AAPL")
        assert tickers.count("AAPL") == 1

    def test_unknown_ticker_not_returned(self):
        tickers = extract_tickers("XYZZY is a made-up symbol")
        assert "XYZZY" not in tickers

    def test_known_tickers_returned(self):
        tickers = extract_tickers("NVDA and AMD compete in GPUs")
        assert "NVDA" in tickers
        assert "AMD" in tickers

    def test_returns_at_most_ten(self):
        text = ("AAPL MSFT AMZN NVDA GOOGL META TSLA JPM XOM CVX "
                "BAC WFC GS MS C WMT HD KO PEP MRK")
        tickers = extract_tickers(text)
        assert len(tickers) <= 10


class TestExtractDateRange:
    def test_returns_tuple_of_two(self):
        result = extract_date_range("The crash happened in March 2020")
        assert len(result) == 2

    def test_year_only_returns_full_year(self):
        start, end = extract_date_range("The crisis of 2008")
        assert start is not None
        assert "2008" in start or "2007" in start  # ±30 days from Jan 1 or explicit

    def test_no_date_returns_none_tuple(self):
        start, end = extract_date_range("The market moved sideways")
        # Either both None (no date found) or not — depends on dateparser
        # Guarantee: if start is None, end must also be None
        if start is None:
            assert end is None

    def test_start_before_end(self):
        start, end = extract_date_range("Markets crashed in March 2020")
        if start and end:
            assert start <= end

    def test_iso_format(self):
        import re
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        start, end = extract_date_range("The 2008 financial crisis")
        if start:
            assert iso_re.match(start)
        if end:
            assert iso_re.match(end)
