"""Feature engineering pipeline.

Reuses the exact indicator functions from technicalIndicators.py by wrapping
a pandas DataFrame in a backtrader-compatible data view, then walking bar-by-bar.
This guarantees that inference features match training features precisely.

Usage (single file):
    python pipeline.py AAPL.csv                  # writes AAPL_features.csv
    python pipeline.py AAPL.csv -o data/features/

Usage (directory):
    python pipeline.py raw_data/ -o features/    # every *.csv in the directory

Input CSV must have columns: date, open, high, low, close, volume
'adj close' is optional; falls back to 'close' when absent.

Output CSV columns (model feature order matching exportModel.py::load_args()):
    date, high, low, volume, adj close, P, R1, R2, R3, S1, S2, S3,
    obv, volume_zscore, rsi, macd, macds, macdh, sma, lma, sema, lema,
    overnight_gap, return_lag_1, return_lag_3, return_lag_5, volatility,
    SR_K, SR_D, SR_RSI_K, SR_RSI_D, ATR, HL_PCT, PCT_CHG, close
"""

import argparse
import os
import sys

import numpy as np
import pandas as pd
from joblib import Parallel, delayed

from technicalIndicators import (
    calculateRsi, calculateMacd, calculateMACDSignal,
    calculateVolatility, calculateVolumeZscore,
    calculateOvernightGap, calculateReturn,
    calculateSMA, calculateEMA,
    calculateOBV, calculateStochastic, calculateStochRSI,
    calculateATR, calculatePctChange,
)


# ---------------------------------------------------------------------------
# Backtrader-compatible data view wrapping a pandas DataFrame
# ---------------------------------------------------------------------------

class _Line:
    """Wraps a numpy array and exposes backtrader's line indexing convention.

    Indexing:  [0]  → current bar  (idx)
               [-n] → n bars ago   (idx - n)
    get(size)  → last `size` values in chronological order
    """

    def __init__(self, values: np.ndarray, idx: int):
        self._v = values
        self._i = idx

    def __getitem__(self, offset: int):
        return float(self._v[self._i + offset])

    def get(self, size: int) -> list:
        start = max(0, self._i - size + 1)
        return list(self._v[start: self._i + 1])


class _DataView:
    def __init__(self, df: pd.DataFrame, idx: int):
        self._idx = idx
        self.close  = _Line(df["close"].values,  idx)
        self.open   = _Line(df["open"].values,   idx)
        self.high   = _Line(df["high"].values,   idx)
        self.low    = _Line(df["low"].values,    idx)
        self.volume = _Line(df["volume"].values, idx)

    def __len__(self) -> int:
        return self._idx + 1   # number of bars available up to and including idx


class _BarCtx:
    """Single persistent strategy context advanced bar by bar.

    Stateful indicators (calculateEMA, calculateOBV, etc.) store their
    running values on `self` via hasattr/getattr/setattr — this works
    correctly because we reuse the same instance across all bars.
    """

    def __init__(self, df: pd.DataFrame):
        self._df = df
        self.data: _DataView = None  # set by advance()

    def advance(self, idx: int) -> None:
        self.data = _DataView(self._df, idx)


# ---------------------------------------------------------------------------
# Pivot points (not in technicalIndicators.py, computed directly)
# ---------------------------------------------------------------------------

def _pivot_points(high, low, close):
    P  = (high + low + close) / 3.0
    return {
        "P":  P,
        "R1": 2.0 * P - low,
        "R2": P + (high - low),
        "R3": high + 2.0 * (P - low),
        "S1": 2.0 * P - high,
        "S2": P - (high - low),
        "S3": low - 2.0 * (high - P),
    }


# ---------------------------------------------------------------------------
# Core transform
# ---------------------------------------------------------------------------

def transform(df: pd.DataFrame,
              sma_short: int = 20, sma_long: int = 50,
              ema_short: int = 20, ema_long: int = 50) -> pd.DataFrame:
    """Walk every bar and compute all model features.

    Rows in the warm-up window (where any feature is still NaN / 0 due to
    insufficient history) are kept with their 0.0 defaults — consistent with
    how the backtrader strategy handles warm-up during training.

    Returns a DataFrame with columns in the exact model feature order.
    """
    df = df.copy()
    df.columns = [c.lower() for c in df.columns]

    required = {"timestamp", "open", "high", "low", "close", "volume"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"pipeline: missing columns {missing}")

    if "adj close" not in df.columns:
        df["adj close"] = df["close"]

    n = len(df)
    ctx = _BarCtx(df)

    rows = []
    for i in range(n):
        ctx.advance(i)

        h = df["high"].iloc[i]
        l = df["low"].iloc[i]
        c = df["close"].iloc[i]
        pp = _pivot_points(h, l, c)

        rsi   = calculateRsi(ctx)
        macd  = calculateMacd(ctx)
        macds = calculateMACDSignal(ctx) or 0.0
        macdh = macd - macds

        SR_K, SR_D         = calculateStochastic(ctx)
        SR_RSI_K, SR_RSI_D = calculateStochRSI(ctx, rsi)

        row = {
            "timestamp":          df["timestamp"].iloc[i],
            "high":          h,
            "low":           l,
            "volume":        df["volume"].iloc[i],
            "adj close":     df["adj close"].iloc[i],
            **pp,
            "obv":           calculateOBV(ctx),
            "volume_zscore": calculateVolumeZscore(ctx),
            "rsi":           rsi,
            "macd":          macd,
            "macds":         macds,
            "macdh":         macdh,
            "sma":           calculateSMA(ctx, sma_short),
            "lma":           calculateSMA(ctx, sma_long),
            "sema":          calculateEMA(ctx, ema_short),
            "lema":          calculateEMA(ctx, ema_long),
            "overnight_gap": calculateOvernightGap(ctx),
            "return_lag_1":  calculateReturn(ctx, 1),
            "return_lag_3":  calculateReturn(ctx, 3),
            "return_lag_5":  calculateReturn(ctx, 5),
            "volatility":    calculateVolatility(ctx),
            "SR_K":          SR_K,
            "SR_D":          SR_D,
            "SR_RSI_K":      SR_RSI_K,
            "SR_RSI_D":      SR_RSI_D,
            "ATR":           calculateATR(ctx),
            "HL_PCT":        (h - l) / c * 100.0 if c != 0 else 0.0,
            "PCT_CHG":       calculatePctChange(ctx),
            "close":         c,
        }
        rows.append(row)

    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def process_file(input_path: str, output_dir: str) -> str:
    df = pd.read_csv(input_path, parse_dates=["timestamp"])
    enriched = transform(df)

    os.makedirs(output_dir, exist_ok=True)
    base = os.path.splitext(os.path.basename(input_path))[0]
    out_path = os.path.join(output_dir, f"{base}_features.csv")
    enriched.to_csv(out_path, index=False)
    print(f"  {input_path}  →  {out_path}  ({len(enriched)} bars, "
          f"{len(enriched.columns) - 1} features)")
    return out_path


def main():
    parser = argparse.ArgumentParser(
        description="Compute transformer model features from raw OHLCV CSVs")
    parser.add_argument("input",
                        help="A single CSV file or a directory of CSV files")
    parser.add_argument("-o", "--output", default="features",
                        help="Output directory (default: ./features)")
    parser.add_argument("--workers", type=int, default=1,
                        help="Number of parallel worker processes (default: 1). "
                             "Use -1 for all available CPUs.")
    args = parser.parse_args()

    if os.path.isdir(args.input):
        files = sorted(
            os.path.join(args.input, f)
            for f in os.listdir(args.input)
            if f.endswith(".csv")
        )
        if not files:
            print(f"No CSV files found in {args.input}", file=sys.stderr)
            sys.exit(1)
        print(f"Processing {len(files)} file(s) → {args.output}/ "
              f"(workers={args.workers})")
        Parallel(n_jobs=args.workers)(
            delayed(process_file)(f, args.output) for f in files
        )

    elif os.path.isfile(args.input):
        out_dir = (args.output if args.output != "features"
                   else os.path.dirname(args.input) or ".")
        process_file(args.input, out_dir)

    else:
        print(f"Not found: {args.input}", file=sys.stderr)
        sys.exit(1)


if __name__ == "__main__":
    main()
