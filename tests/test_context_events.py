"""Tests for research/context/events.py."""
import sys
import os

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from research.context.events import EVENTS, EventRecord, search_events


class TestEventDatabase:
    def test_events_is_not_empty(self):
        assert len(EVENTS) >= 10

    def test_all_records_have_required_fields(self):
        for key, record in EVENTS.items():
            assert record.label, f"{key} missing label"
            assert record.date_start, f"{key} missing date_start"
            assert record.date_end, f"{key} missing date_end"
            assert record.keywords, f"{key} missing keywords"
            assert record.tickers, f"{key} missing tickers"

    def test_dates_are_iso_format(self):
        import re
        iso_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
        for key, record in EVENTS.items():
            assert iso_re.match(record.date_start), f"{key}.date_start not ISO"
            assert iso_re.match(record.date_end), f"{key}.date_end not ISO"

    def test_start_not_after_end(self):
        for key, record in EVENTS.items():
            assert record.date_start <= record.date_end, (
                f"{key}: date_start {record.date_start} is after date_end {record.date_end}"
            )

    def test_known_events_present(self):
        assert "covid_crash" in EVENTS
        assert "gfc_2008" in EVENTS
        assert "dot_com_crash" in EVENTS

    def test_covid_crash_dates(self):
        r = EVENTS["covid_crash"]
        assert r.date_start == "2020-02-19"
        assert r.date_end == "2020-03-23"

    def test_records_are_frozen(self):
        with pytest.raises((AttributeError, TypeError)):
            EVENTS["covid_crash"].label = "mutated"  # type: ignore[misc]


class TestSearchEvents:
    def test_finds_covid_from_keyword(self):
        matches = search_events("The COVID-19 crash wiped out travel stocks")
        keys = [m[0] for m in matches]
        assert "covid_crash" in keys

    def test_no_match_returns_empty(self):
        matches = search_events("The cat sat on the mat")
        assert matches == []

    def test_sorted_by_match_count_descending(self):
        # Use text with many GFC keywords
        text = "The financial crisis began with lehman subprime mortgage crisis great recession"
        matches = search_events(text)
        if len(matches) >= 2:
            counts = [m[2] for m in matches]
            assert counts == sorted(counts, reverse=True)

    def test_returns_tuple_of_key_record_count(self):
        matches = search_events("lehman brothers financial crisis")
        assert len(matches) >= 1
        key, record, count = matches[0]
        assert isinstance(key, str)
        assert isinstance(record, EventRecord)
        assert isinstance(count, int)
        assert count >= 1

    def test_case_insensitive(self):
        matches_lower = search_events("covid crash pandemic")
        matches_upper = search_events("COVID CRASH PANDEMIC")
        assert len(matches_lower) == len(matches_upper)
