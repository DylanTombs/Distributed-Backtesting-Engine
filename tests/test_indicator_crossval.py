"""Python side of the shared indicator cross-validation (Phase 9.1, ADR-048).

Pins technicalIndicators.py against tests/fixtures/indicator_crossval.csv —
the same artifact the C++ suite asserts (test_indicators_crossval.cpp). If
either implementation drifts from its documented contract, exactly one of
the two suites fails and points at the divergence.

Contracts verified here:
  - calculateSMA(10)  == fixture sma_10       (simple mean — same as engine)
  - calculateRsi()    == fixture rsi_cutler_14 (Cutler's simple-average RSI,
    fixed 14-diff window — same family as the engine's indicators::rsi)
  - calculateEMA is intentionally NOT pinned to the engine's ema_seeded_10:
    the pipeline EMA is stream-stateful (seeded from the first bar of the
    feed), the engine EMA is window-seeded. A divergence assertion documents
    that they differ by design.
"""
from __future__ import annotations

import csv
import math
from pathlib import Path

import pytest

FIXTURE = Path(__file__).parent / "fixtures" / "indicator_crossval.csv"

# RSI epsilon: technicalIndicators adds 1e-10 to avgLoss, so its all-gain
# result is 100 - O(1e-8) while the fixture (and engine) say exactly 100.
RSI_TOL = 1e-3


class _CloseSeries:
    """Duck-types the backtrader-style feed technicalIndicators expects:
    close[0] = current bar, close[-i] = i bars back, get(size=n) = last n."""

    def __init__(self, values: list[float]):
        self._values = values

    def __getitem__(self, idx: int) -> float:
        return self._values[len(self._values) - 1 + idx]

    def get(self, size: int) -> list[float]:
        return self._values[-size:]


class _Feed:
    def __init__(self, values: list[float]):
        self.close = _CloseSeries(values)

    def __len__(self) -> int:
        return len(self.close._values)


class _IndicatorHost:
    """Minimal object exposing self.data for the module's methods."""

    def __init__(self, values: list[float]):
        self.data = _Feed(values)


def _load_fixture() -> list[dict]:
    with open(FIXTURE, newline="") as f:
        return list(csv.DictReader(f))


@pytest.fixture(scope="module")
def rows() -> list[dict]:
    assert FIXTURE.exists(), "run scripts/gen_indicator_fixture.py"
    return _load_fixture()


def test_fixture_shape(rows):
    assert len(rows) == 60
    assert sum(1 for r in rows if r["rsi_cutler_14"]) > 40


def test_calculate_sma_matches_fixture(rows):
    from research.features.technicalIndicators import calculateSMA

    closes: list[float] = []
    for i, row in enumerate(rows):
        closes.append(float(row["close"]))
        if not row["sma_10"]:
            continue
        host = _IndicatorHost(closes)
        assert math.isclose(
            calculateSMA(host, 10), float(row["sma_10"]), abs_tol=1e-6
        ), f"SMA mismatch at row {i}"


def test_calculate_rsi_matches_fixture(rows):
    from research.features.technicalIndicators import calculateRsi

    closes: list[float] = []
    for i, row in enumerate(rows):
        closes.append(float(row["close"]))
        if not row["rsi_cutler_14"]:
            continue
        host = _IndicatorHost(closes)
        assert math.isclose(
            calculateRsi(host), float(row["rsi_cutler_14"]), abs_tol=RSI_TOL
        ), f"RSI mismatch at row {i}"


def test_rsi_saturation_rows_are_exercised(rows):
    """The fixture's forced rally must hit the all-gains branch on both sides."""
    saturated = [r for r in rows if r["rsi_cutler_14"] == "100.00000000"]
    assert saturated, "no RSI=100 rows — regenerate the fixture"


def test_pipeline_ema_differs_from_engine_ema_by_design(rows):
    """Documented divergence (ADR-048): pipeline EMA is stream-stateful,
    engine EMA is window-seeded. If this assertion ever fails, the two
    implementations have converged and the fixture columns should merge."""
    from research.features.technicalIndicators import calculateEMA

    closes = [float(r["close"]) for r in rows]
    host = _IndicatorHost(closes[:1])
    # Replay the stream the way the pipeline does: one bar at a time
    stream_ema = None
    for i in range(len(closes)):
        host.data = _Feed(closes[: i + 1])
        stream_ema = calculateEMA(host, 10)

    engine_ema = float(rows[-1]["ema_seeded_10"])
    assert not math.isclose(stream_ema, engine_ema, abs_tol=1e-9), (
        "pipeline and engine EMA now agree — merge the fixture columns and "
        "update ADR-048"
    )
    # Both still in a sane band around the price series
    assert abs(stream_ema - engine_ema) < 5.0
