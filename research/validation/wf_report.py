"""
wf_report.py — Walk-forward summary report generator.

Reads per-symbol wf_<symbol>.csv files written by walk_forward.py and
produces a cross-symbol summary in both human-readable text and CSV form.

Usage:
    python research/validation/wf_report.py \
        --input-dir output/ \
        --output-dir output/
"""
from __future__ import annotations

import argparse
import glob
import logging
import os
from dataclasses import dataclass
from typing import List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class SymbolSummary:
    symbol: str
    n_folds: int
    mean_mse: float
    std_mse: float
    mean_rmse: float
    std_rmse: float
    mean_mape: float
    std_mape: float


# ---------------------------------------------------------------------------
# Core report builder
# ---------------------------------------------------------------------------

class WalkForwardReport:
    """Aggregates per-fold CSV files into a cross-symbol summary."""

    def __init__(self, input_dir: str):
        self.input_dir = input_dir
        self.summaries: List[SymbolSummary] = []

    def load(self) -> "WalkForwardReport":
        """Scan input_dir for wf_*.csv files and compute per-symbol summaries."""
        pattern = os.path.join(self.input_dir, "wf_*.csv")
        paths = sorted(glob.glob(pattern))

        if not paths:
            raise FileNotFoundError(
                f"No wf_*.csv files found in {self.input_dir}"
            )

        for path in paths:
            symbol = _symbol_from_path(path)
            df = pd.read_csv(path)

            # Drop folds with NaN metrics and warn
            before = len(df)
            df = df.dropna(subset=['test_mse', 'test_rmse', 'test_mape'])
            dropped = before - len(df)
            if dropped:
                logger.warning(
                    "%s: dropped %d fold(s) with NaN metrics", symbol, dropped
                )

            if df.empty:
                logger.warning("%s: no valid folds — skipping", symbol)
                continue

            self.summaries.append(SymbolSummary(
                symbol=symbol,
                n_folds=len(df),
                mean_mse=float(np.mean(df['test_mse'])),
                std_mse=float(np.std(df['test_mse'])),
                mean_rmse=float(np.mean(df['test_rmse'])),
                std_rmse=float(np.std(df['test_rmse'])),
                mean_mape=float(np.mean(df['test_mape'])),
                std_mape=float(np.std(df['test_mape'])),
            ))

        return self

    def to_dataframe(self) -> pd.DataFrame:
        if not self.summaries:
            return pd.DataFrame()
        rows = [
            {
                'symbol': s.symbol,
                'folds': s.n_folds,
                'mean_mse': s.mean_mse,
                'std_mse': s.std_mse,
                'mean_rmse': s.mean_rmse,
                'std_rmse': s.std_rmse,
                'mean_mape_%': s.mean_mape,
                'std_mape_%': s.std_mape,
            }
            for s in self.summaries
        ]
        return pd.DataFrame(rows).sort_values('mean_mse')

    def format_text(self) -> str:
        """Return a human-readable summary table."""
        df = self.to_dataframe()
        if df.empty:
            return "No walk-forward results to report."

        lines = [
            "",
            "Walk-Forward Validation Summary",
            "=" * 78,
            f"{'Symbol':<10} {'Folds':>5}  "
            f"{'Mean MSE':>10} {'±':>2} {'Std':>8}  "
            f"{'Mean RMSE':>10} {'±':>2} {'Std':>8}  "
            f"{'Mean MAPE%':>10} {'±':>2} {'Std':>8}",
            "-" * 78,
        ]
        for _, row in df.iterrows():
            lines.append(
                f"{row['symbol']:<10} {int(row['folds']):>5}  "
                f"{row['mean_mse']:>10.5f} {'±':>2} {row['std_mse']:>8.5f}  "
                f"{row['mean_rmse']:>10.5f} {'±':>2} {row['std_rmse']:>8.5f}  "
                f"{row['mean_mape_%']:>10.3f} {'±':>2} {row['std_mape_%']:>8.3f}"
            )
        lines.append("=" * 78)
        return "\n".join(lines)

    def export(self, output_dir: str) -> tuple[str, str]:
        """Write wf_summary.txt and wf_summary.csv; return their paths."""
        os.makedirs(output_dir, exist_ok=True)

        txt_path = os.path.join(output_dir, "wf_summary.txt")
        with open(txt_path, "w") as f:
            f.write(self.format_text())
            f.write("\n")
        logger.info("Text summary → %s", txt_path)

        csv_path = os.path.join(output_dir, "wf_summary.csv")
        self.to_dataframe().to_csv(csv_path, index=False)
        logger.info("CSV summary → %s", csv_path)

        return txt_path, csv_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _symbol_from_path(path: str) -> str:
    """Extract symbol name from wf_<symbol>.csv."""
    basename = os.path.basename(path)           # wf_AAPL.csv
    return basename[len("wf_"):-len(".csv")]    # AAPL


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_cli():
    p = argparse.ArgumentParser(
        description="Generate walk-forward summary report",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('--input-dir', default='output/',
                   help='Directory containing wf_*.csv files')
    p.add_argument('--output-dir', default='output/',
                   help='Directory to write wf_summary.txt and wf_summary.csv')
    return p.parse_args()


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    args = _parse_cli()
    report = WalkForwardReport(args.input_dir).load()
    txt_path, csv_path = report.export(args.output_dir)
    print(report.format_text())
    print(f"\nText report → {txt_path}")
    print(f"CSV  report → {csv_path}")


if __name__ == '__main__':
    main()
