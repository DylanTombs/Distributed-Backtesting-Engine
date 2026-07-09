"""Generate the shared indicator cross-validation fixture (Phase 9.1, ADR-048).

Writes tests/fixtures/indicator_crossval.csv: a deterministic 60-bar price
series plus expected indicator values computed with plain reference formulas.
Both implementations are pinned against this one artifact:

  - C++  (backtester/tests/test_indicators_crossval.cpp) asserts
    indicators::sma/emaSeeded/rsi/highestBefore/lowestBefore
  - Python (tests/test_indicator_crossval.py) asserts
    technicalIndicators.calculateSMA/calculateRsi

Expected-value contracts (row i is computed over the full prefix close[0..i]):
  sma_10          mean of close[i-9..i]                      (needs i >= 9)
  ema_seeded_10   SMA(close[0..9]) seed, standard recursion  (needs i >= 9)
  rsi_cutler_14   simple avg gains/losses over last 14 diffs (needs i >= 14)
  high_5 / low_5  max/min of close[i-5..i-1], EXCLUDES bar i (needs i >= 5)

Empty cells mean "insufficient history". Regenerate with:
  python3 scripts/gen_indicator_fixture.py
The output is deterministic (seeded RNG); the CSV is checked in and any
regeneration diff is itself a signal that a formula changed.
"""
from __future__ import annotations

from pathlib import Path

import numpy as np

OUT = Path(__file__).resolve().parents[1] / "tests" / "fixtures" / "indicator_crossval.csv"

N_BARS = 60
SMA_PERIOD = 10
EMA_PERIOD = 10
RSI_PERIOD = 14
CHANNEL_PERIOD = 5


def generate_prices() -> np.ndarray:
    rng = np.random.default_rng(42)
    # Random walk with drift; includes at least one strictly-rising stretch
    # so the RSI loss==0 branch is exercised.
    steps = rng.normal(0.2, 2.0, N_BARS - 1)
    # Forced 15-bar rally: longer than the 14-diff RSI window, so at least
    # one row has zero losses and exercises the RSI loss==0 branch.
    steps[20:35] = np.abs(steps[20:35]) + 0.5
    prices = 100.0 + np.concatenate([[0.0], np.cumsum(steps)])
    return np.round(prices, 4)


def sma(prefix: np.ndarray, period: int) -> float:
    return float(prefix[-period:].mean())


def ema_seeded(prefix: np.ndarray, period: int) -> float:
    k = 2.0 / (period + 1)
    ema = float(prefix[:period].mean())
    for v in prefix[period:]:
        ema = float(v) * k + ema * (1.0 - k)
    return ema


def rsi_cutler(prefix: np.ndarray, period: int) -> float:
    diffs = np.diff(prefix[-(period + 1):])
    gain = float(diffs.clip(min=0).sum())
    loss = float((-diffs.clip(max=0)).sum())
    if loss == 0.0:
        return 100.0
    rs = gain / loss
    return 100.0 - 100.0 / (1.0 + rs)


def main() -> None:
    prices = generate_prices()
    rows = ["close,sma_10,ema_seeded_10,rsi_cutler_14,high_5,low_5"]
    for i in range(N_BARS):
        prefix = prices[: i + 1]
        cells = [f"{prices[i]:.4f}"]
        cells.append(f"{sma(prefix, SMA_PERIOD):.8f}" if i >= SMA_PERIOD - 1 else "")
        cells.append(f"{ema_seeded(prefix, EMA_PERIOD):.8f}" if i >= EMA_PERIOD - 1 else "")
        cells.append(f"{rsi_cutler(prefix, RSI_PERIOD):.8f}" if i >= RSI_PERIOD else "")
        if i >= CHANNEL_PERIOD:
            window = prefix[-(CHANNEL_PERIOD + 1):-1]
            cells.append(f"{window.max():.4f}")
            cells.append(f"{window.min():.4f}")
        else:
            cells.extend(["", ""])
        rows.append(",".join(cells))

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text("\n".join(rows) + "\n")
    print(f"Wrote {OUT} ({N_BARS} bars)")


if __name__ == "__main__":
    main()
