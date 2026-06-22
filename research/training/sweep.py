"""
sweep.py — Optuna hyperparameter sweep for the TradingTransformer.

Searches over d_model, n_heads, e_layers, d_layers, d_ff, dropout,
learning_rate, and batch_size.  Uses walk-forward fold 1 as the fixed
train/val split and minimises val_mse.

Usage:
    python research/training/sweep.py \
        --feature-csv features/AAPL_features.csv \
        --n-trials 50 \
        --max-epochs 30

Outputs:
    models/best_config.yaml   — best trial hyperparameters
    models/sweep_results.csv  — all trial params + metrics
    models/optuna_study.db    — persistent SQLite study (resumable)
"""
from __future__ import annotations

import argparse
import logging
import math
import os

import numpy as np
import pandas as pd
import yaml

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Search space
# ---------------------------------------------------------------------------

D_MODEL_CHOICES = list(range(64, 513, 64))   # 64, 128, ..., 512
N_HEADS_CHOICES = list(range(2, 17, 2))       # 2, 4, ..., 16


def _suggest_params(trial) -> dict:
    """Sample a hyperparameter configuration from the search space."""
    import optuna

    d_model = trial.suggest_categorical("d_model", D_MODEL_CHOICES)
    # n_heads must divide d_model — restrict to valid divisors
    valid_heads = [h for h in N_HEADS_CHOICES if d_model % h == 0]
    if not valid_heads:
        raise optuna.TrialPruned()
    n_heads = trial.suggest_categorical("n_heads", valid_heads)

    return dict(
        d_model=d_model,
        n_heads=n_heads,
        e_layers=trial.suggest_int("e_layers", 1, 6),
        d_layers=trial.suggest_int("d_layers", 1, 4),
        d_ff=d_model * trial.suggest_categorical("d_ff_multiplier", [2, 4, 8]),
        dropout=trial.suggest_float("dropout", 0.0, 0.5, step=0.05),
        learning_rate=trial.suggest_float("lr", 1e-5, 1e-2, log=True),
        batch_size=trial.suggest_int("batch_size", 32, 256, step=32),
    )


# ---------------------------------------------------------------------------
# Objective
# ---------------------------------------------------------------------------

def make_objective(feature_csv: str, max_epochs: int, seed: int):
    """Return an Optuna objective function closed over dataset + settings."""

    df = pd.read_csv(feature_csv, parse_dates=['date'])
    df = df.sort_values('date').reset_index(drop=True)

    def objective(trial) -> float:
        import optuna
        from transformer.Interface import Model_Interface, build_args, set_seed
        from validation.walk_forward import WalkForwardConfig, WalkForwardValidator

        params = _suggest_params(trial)

        # Use fold 1 boundaries as fixed train/val split
        cfg = WalkForwardConfig(n_folds=1, seed=seed)
        validator = WalkForwardValidator(cfg)
        folds = validator.get_fold_boundaries(len(df))
        if not folds:
            raise optuna.TrialPruned()

        fold = folds[0]
        train_df = df.iloc[fold.train_start:fold.val_end].copy()  # train+val
        val_df = df.iloc[fold.val_start:fold.val_end].copy()

        symbol = 'sweep'
        for part in (train_df, val_df):
            if 'ticker' not in part.columns:
                part['ticker'] = symbol

        aux_features = [c for c in df.columns if c not in ('date', 'ticker', 'close')]
        n_features = len(aux_features) + 1

        args = build_args(dict(
            target='close',
            auxilFeatures=aux_features,
            checkpoints=f'./checkpoints/sweep_trial_{trial.number}/',
            encIn=n_features,
            decIn=n_features,
            dModel=params['d_model'],
            nHeads=params['n_heads'],
            eLayers=params['e_layers'],
            dLayers=params['d_layers'],
            dFf=params['d_ff'],
            dropout=params['dropout'],
            learningRate=params['learning_rate'],
            batchSize=params['batch_size'],
            trainEpochs=max_epochs,
            patience=5,
            seed=seed,
        ))

        set_seed(seed)
        iface = Model_Interface(args)

        try:
            val_mse = iface.train(train_df)
        except Exception as exc:
            logger.warning("Trial %d failed: %s", trial.number, exc)
            raise optuna.TrialPruned()

        if not math.isfinite(val_mse):
            raise optuna.TrialPruned()

        return val_mse

    return objective


# ---------------------------------------------------------------------------
# Export helpers
# ---------------------------------------------------------------------------

def export_best_config(study, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    best = study.best_trial
    config = {**best.params, "val_mse": best.value}
    path = os.path.join(output_dir, "best_config.yaml")
    with open(path, "w") as f:
        yaml.dump(config, f, default_flow_style=False, sort_keys=True)
    logger.info("Best config → %s (val_mse=%.6f)", path, best.value)
    return path


def export_sweep_results(study, output_dir: str) -> str:
    os.makedirs(output_dir, exist_ok=True)
    rows = []
    for t in study.trials:
        row = {"trial": t.number, "val_mse": t.value, "state": str(t.state)}
        row.update(t.params)
        rows.append(row)
    df = pd.DataFrame(rows).sort_values("val_mse")
    path = os.path.join(output_dir, "sweep_results.csv")
    df.to_csv(path, index=False)
    logger.info("Sweep results (%d trials) → %s", len(rows), path)
    return path


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _parse_cli():
    p = argparse.ArgumentParser(
        description="Optuna hyperparameter sweep for TradingTransformer",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument('--feature-csv', required=True,
                   help='Feature CSV (date-sorted, all tickers or single symbol)')
    p.add_argument('--n-trials', type=int, default=50)
    p.add_argument('--max-epochs', type=int, default=30,
                   help='Epochs per trial (shorter = faster sweep)')
    p.add_argument('--output-dir', default='models/')
    p.add_argument('--seed', type=int, default=42)
    p.add_argument('--study-name', default='transformer_hparam_sweep')
    return p.parse_args()


def main():
    logging.basicConfig(level=logging.INFO,
                        format='%(asctime)s %(levelname)s %(message)s')
    args = _parse_cli()

    import optuna
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    os.makedirs(args.output_dir, exist_ok=True)
    storage = f"sqlite:///{os.path.join(args.output_dir, 'optuna_study.db')}"

    study = optuna.create_study(
        direction="minimize",
        sampler=optuna.samplers.TPESampler(seed=args.seed),
        pruner=optuna.pruners.HyperbandPruner(min_resource=5,
                                               max_resource=args.max_epochs),
        study_name=args.study_name,
        storage=storage,
        load_if_exists=True,
    )

    objective = make_objective(args.feature_csv, args.max_epochs, args.seed)
    study.optimize(objective, n_trials=args.n_trials)

    print(f"\nSweep complete: {len(study.trials)} trials")
    print(f"Best val_mse: {study.best_value:.6f}")
    print(f"Best params: {study.best_params}")

    export_best_config(study, args.output_dir)
    export_sweep_results(study, args.output_dir)

    top5 = sorted(
        [t for t in study.trials if t.value is not None],
        key=lambda t: t.value
    )[:5]
    print("\nTop-5 trials:")
    for t in top5:
        print(f"  Trial {t.number}: val_mse={t.value:.6f} params={t.params}")


if __name__ == '__main__':
    main()
