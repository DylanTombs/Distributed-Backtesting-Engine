"""
walk_forward.py — Expanding-window walk-forward validator.

Splits a feature CSV into N non-overlapping test folds (each 63 bars),
trains a fresh model on all data before each test window, and returns
per-fold MSE/RMSE/MAPE metrics.

Usage (CLI):
    python research/validation/walk_forward.py \
        --feature-csv features/AAPL_features.csv \
        --symbol AAPL \
        --output-dir output/

Usage (import):
    from validation.walk_forward import WalkForwardValidator, WalkForwardConfig
    cfg = WalkForwardConfig()
    validator = WalkForwardValidator(cfg)
    result = validator.run("AAPL", "features/AAPL_features.csv")
"""
from __future__ import annotations

import argparse
import dataclasses
import logging
import math
import os
from dataclasses import dataclass, field
from typing import List

import numpy as np
import pandas as pd

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

@dataclass
class WalkForwardConfig:
    """Parameters that control fold construction."""
    min_train_bars: int = 252    # minimum training window (1 trading year)
    val_bars: int = 63           # validation window (early stopping only)
    test_bars: int = 63          # out-of-sample test window per fold
    step_bars: int = 63          # step between fold test windows
    n_folds: int = 5             # number of folds to run
    seed: int = 42


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class FoldBoundary:
    """Row-index boundaries for one fold (all indices are inclusive start, exclusive end)."""
    fold: int
    train_start: int
    train_end: int     # exclusive
    val_start: int
    val_end: int       # exclusive
    test_start: int
    test_end: int      # exclusive


@dataclass
class FoldMetrics:
    fold: int
    test_mse: float
    test_rmse: float
    test_mape: float
    n_test_windows: int


@dataclass
class WalkForwardResult:
    symbol: str
    per_fold: List[FoldMetrics] = field(default_factory=list)
    predictions: pd.DataFrame = field(default_factory=pd.DataFrame)

    def summary(self) -> dict:
        """Return mean ± std across folds for each metric."""
        if not self.per_fold:
            return {}
        mses = [f.test_mse for f in self.per_fold]
        rmses = [f.test_rmse for f in self.per_fold]
        mapes = [f.test_mape for f in self.per_fold]
        return {
            "mean_mse": float(np.mean(mses)),
            "std_mse": float(np.std(mses)),
            "mean_rmse": float(np.mean(rmses)),
            "std_rmse": float(np.std(rmses)),
            "mean_mape": float(np.mean(mapes)),
            "std_mape": float(np.std(mapes)),
            "n_folds": len(self.per_fold),
        }


# ---------------------------------------------------------------------------
# Core validator
# ---------------------------------------------------------------------------

class WalkForwardValidator:
    """Expanding-window walk-forward validator.

    Fold layout (bars are rows in the feature CSV, sorted by date):

        |<-- min_train -->|<-- val -->|<-- test -->|
        |<-- min_train + step -->|<-- val -->|<-- test -->|
        ...

    The training window expands by `step_bars` each fold; val and test
    windows are fixed-width and immediately follow the training window.
    """

    def __init__(self, config: WalkForwardConfig | None = None):
        self.config = config or WalkForwardConfig()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_fold_boundaries(self, n_rows: int) -> List[FoldBoundary]:
        """Compute fold boundaries for a dataset of `n_rows` rows.

        Returns an empty list if there is not enough data for even one fold.
        """
        cfg = self.config
        folds: List[FoldBoundary] = []

        for fold_idx in range(cfg.n_folds):
            train_end = cfg.min_train_bars + fold_idx * cfg.step_bars
            val_end = train_end + cfg.val_bars
            test_end = val_end + cfg.test_bars

            if test_end > n_rows:
                logger.warning(
                    "Fold %d requires %d rows but dataset has %d — stopping at %d folds",
                    fold_idx + 1, test_end, n_rows, len(folds),
                )
                break

            folds.append(FoldBoundary(
                fold=fold_idx + 1,
                train_start=0,
                train_end=train_end,
                val_start=train_end,
                val_end=val_end,
                test_start=val_end,
                test_end=test_end,
            ))

        return folds

    def run(self, symbol: str, feature_csv: str) -> WalkForwardResult:
        """Train + evaluate one fold at a time, return stitched results.

        The model import is deferred so the validator can be imported and
        unit-tested without a GPU / full PyTorch environment.
        """
        df = pd.read_csv(feature_csv, parse_dates=['date'])
        df = df.sort_values('date').reset_index(drop=True)

        folds = self.get_fold_boundaries(len(df))
        if not folds:
            raise ValueError(
                f"Dataset too small for walk-forward validation "
                f"({len(df)} rows, need at least "
                f"{self.config.min_train_bars + self.config.val_bars + self.config.test_bars})"
            )

        result = WalkForwardResult(symbol=symbol)
        all_preds: List[pd.DataFrame] = []

        for fold in folds:
            logger.info(
                "Fold %d/%d: train [0:%d] val [%d:%d] test [%d:%d]",
                fold.fold, len(folds),
                fold.train_end,
                fold.val_start, fold.val_end,
                fold.test_start, fold.test_end,
            )

            fold_metrics, fold_preds = self._run_fold(df, fold, symbol)
            result.per_fold.append(fold_metrics)
            all_preds.append(fold_preds)

        result.predictions = pd.concat(all_preds, ignore_index=True)
        return result

    # ------------------------------------------------------------------
    # Per-fold training + evaluation
    # ------------------------------------------------------------------

    def _run_fold(
        self, df: pd.DataFrame, fold: FoldBoundary, symbol: str
    ) -> tuple[FoldMetrics, pd.DataFrame]:
        from transformer.Interface import Model_Interface, build_args, set_seed
        from transformer.DataFrame import DataFrameDataset
        from torch.utils.data import DataLoader
        import torch

        set_seed(self.config.seed)

        train_df = df.iloc[fold.train_start:fold.train_end].copy()
        val_df = df.iloc[fold.val_start:fold.val_end].copy()
        test_df = df.iloc[fold.test_start:fold.test_end].copy()

        # Add a dummy ticker column if not present (single-symbol CSVs)
        for part in (train_df, val_df, test_df):
            if 'ticker' not in part.columns:
                part['ticker'] = symbol

        # Derive feature list from columns (all except date, ticker, close)
        aux_features = [
            c for c in df.columns
            if c not in ('date', 'ticker', 'close')
        ]

        args = build_args(dict(
            target='close',
            auxilFeatures=aux_features,
            checkpoints=f'./checkpoints/wf_{symbol}_fold{fold.fold}/',
            encIn=len(aux_features) + 1,
            decIn=len(aux_features) + 1,
            trainEpochs=30,   # fast per-fold training
            seed=self.config.seed,
        ))

        iface = Model_Interface(args)

        # Train on train split, use val for early stopping
        combined_train = pd.concat([train_df, val_df], ignore_index=True)
        iface.train(combined_train)

        # Evaluate on test window
        test_mse, test_rmse, test_mape, preds_df = self._evaluate_fold(
            iface, test_df, aux_features, symbol, fold.fold
        )

        return (
            FoldMetrics(
                fold=fold.fold,
                test_mse=test_mse,
                test_rmse=test_rmse,
                test_mape=test_mape,
                n_test_windows=len(preds_df),
            ),
            preds_df,
        )

    def _evaluate_fold(
        self,
        iface,
        test_df: pd.DataFrame,
        aux_features: list,
        symbol: str,
        fold_idx: int,
    ) -> tuple[float, float, float, pd.DataFrame]:
        import torch
        from transformer.DataFrame import DataFrameDataset
        from torch.utils.data import DataLoader

        args = iface.args
        size = (args.seqLen, args.labelLen, args.predLen)

        if 'ticker' not in test_df.columns:
            test_df = test_df.copy()
            test_df['ticker'] = symbol

        dataset = DataFrameDataset(
            df=test_df,
            flag='test',
            size=size,
            target='close',
            auxilFeatures=aux_features,
            featureScaler=iface.get_data('val', test_df)[0].featureScaler
            if False else _load_scaler(args.checkpoints, 'featureScaler'),
            targetScaler=_load_scaler(args.checkpoints, 'targetScaler'),
            stockColumn='ticker',
        )

        loader = DataLoader(dataset, batch_size=64, shuffle=False,
                            num_workers=0)

        mses, rmses, mapes = [], [], []
        iface.model.eval()
        device = iface.device

        with torch.no_grad():
            for batch_x, batch_y, batch_x_mark, batch_y_mark in loader:
                batch_x = batch_x.float().to(device)
                batch_y = batch_y.float()
                batch_x_mark = batch_x_mark.float().to(device)
                batch_y_mark = batch_y_mark.float().to(device)

                dec_inp = torch.zeros_like(
                    batch_y[:, -args.predLen:, :]).float()
                dec_inp = torch.cat(
                    [batch_y[:, :args.labelLen, :], dec_inp],
                    dim=1).float().to(device)

                out = iface.model(batch_x, batch_x_mark, dec_inp, batch_y_mark)[0]
                out = out[:, -args.predLen:, -1:]
                tgt = batch_y[:, -args.predLen:, -1:].to(device)

                mse = float(torch.mean((out - tgt) ** 2).item())
                rmse = float(math.sqrt(mse))
                mape = float(torch.mean(
                    torch.abs((tgt - out) / (tgt + 1e-8))).item() * 100)
                mses.append(mse)
                rmses.append(rmse)
                mapes.append(mape)

        preds_df = pd.DataFrame({
            'fold': fold_idx,
            'symbol': symbol,
            'test_mse': mses,
        })

        return float(np.mean(mses)), float(np.mean(rmses)), float(np.mean(mapes)), preds_df


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_scaler(checkpoints_dir: str, name: str):
    import joblib
    path = os.path.join(checkpoints_dir, f'{name}.pkl')
    if os.path.exists(path):
        return joblib.load(path)
    return None


def export_fold_csv(result: WalkForwardResult, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    rows = []
    for fm in result.per_fold:
        rows.append(dataclasses.asdict(fm))
    df = pd.DataFrame(rows)
    path = os.path.join(output_dir, f"wf_{result.symbol}.csv")
    df.to_csv(path, index=False)
    logger.info("Per-fold metrics written to %s", path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_cli():
    p = argparse.ArgumentParser(
        description="Run walk-forward validation for one symbol",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('--feature-csv', required=True,
                   help='Feature CSV for the symbol (date-sorted)')
    p.add_argument('--symbol', required=True)
    p.add_argument('--output-dir', default='output/')
    p.add_argument('--n-folds', type=int, default=5)
    p.add_argument('--min-train-bars', type=int, default=252)
    p.add_argument('--test-bars', type=int, default=63)
    p.add_argument('--seed', type=int, default=42)
    return p.parse_args()


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    args = _parse_cli()
    cfg = WalkForwardConfig(
        n_folds=args.n_folds,
        min_train_bars=args.min_train_bars,
        test_bars=args.test_bars,
        seed=args.seed,
    )
    validator = WalkForwardValidator(cfg)
    result = validator.run(args.symbol, args.feature_csv)
    path = export_fold_csv(result, args.output_dir)
    print(f"\nWalk-forward complete for {args.symbol}")
    print(f"Per-fold metrics → {path}")
    summary = result.summary()
    for k, v in summary.items():
        print(f"  {k}: {v:.4f}" if isinstance(v, float) else f"  {k}: {v}")


if __name__ == '__main__':
    main()
