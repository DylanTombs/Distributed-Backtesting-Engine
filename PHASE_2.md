# Phase 2 — Validation Methodology

**Status:** Code Complete — branch `feat/validation-and-hparam-sweep` (pending data run on real symbols)  
**Prerequisites:** Phase 1 complete (all bugs fixed, 80%+ coverage, schema validation in place)  
**Unlocks:** Credible out-of-sample performance claims

---

## Objective

Replace the current fixed-window single-split evaluation with a rigorous walk-forward validation framework and an automated hyperparameter search. Results produced after Phase 2 will be defensible to the standard expected in quantitative research: out-of-sample, multi-fold, with reproducible model configuration.

**Why Phase 1 must precede Phase 2:** Walk-forward validation requires the training pipeline to be bug-free (B-01 ticker leakage fixed), reproducible (seed exposed), and correctly splitting data. Running 50 Optuna trials on a pipeline with a known leakage bug produces an optimistic bias that cannot be untangled after the fact.

**Exit criteria (all must be satisfied before Phase 2 is closed):**
- [ ] Walk-forward report generated for ≥ 3 symbols, each with ≥ 5 folds — infrastructure built (`walk_forward.py`), run requires real feature CSVs
- [ ] Per-fold metrics CSV exported for every symbol (`wf_<symbol>.csv`) — `export_fold_csv()` implemented and tested
- [ ] Walk-forward summary table showing mean ± std of Sharpe, IR, max drawdown across folds — `wf_report.py` implemented and tested
- [ ] Optuna sweep completed: ≥ 50 trials, best config exported to `models/best_config.yaml` — `sweep.py` implemented; run requires real feature CSVs
- [x] Three consecutive training runs with the same seed produce identical `val_mse` to 6 decimal places — `set_seed()` + `num_workers=0` implemented and tested
- [x] `Train.py` consolidated into `Interface.py`; `backtrader` removed from `requirements.txt`
- [x] All code tasks in this document marked complete (data-run criteria require real symbol data)

---

## Task Breakdown

### 2.1 Consolidate Training Entry Points (TD-13, TD-12)

**Files:** `research/training/Train.py`, `research/transformer/Interface.py`, `requirements.txt`  
**Issue:** Two overlapping training entry points exist. `Train.py` sets hyperparameters and delegates to `Interface.py`. `Interface.py` contains the actual loop. This creates maintenance confusion about which file is canonical.

**Required change:**
- Move all hyperparameter defaults from `Train.py` into `Interface.py`'s argument parser.
- Delete `research/training/Train.py`.
- Update `run_pipeline.py` to invoke `Interface.py` directly.
- Remove `backtrader>=1.9.0` from `requirements.txt` — it is only used in `tests/test_metrics.py` via the `_IsolatedEvaluator` workaround. Migrate those metric computations to direct function calls against the C++ output CSVs or a standalone implementation.

**Test requirement:**
- `run_pipeline.py` still runs end-to-end after the consolidation.
- `test_metrics.py` continues to pass without `backtrader`.

---

### 2.2 Reproducible Training (TD-07)

**Files:** `research/transformer/Interface.py`, `run_pipeline.py`, `backtest_config.yaml`

**Issue:** Training is non-deterministic due to PyTorch's multi-threaded operations and non-deterministic CUDA kernels. Re-running the pipeline produces different model weights and metrics, making it impossible to confirm whether a performance change is due to code or random variation.

**Required change:**

Add a `seed` parameter to the training config and apply it at the start of `Interface.py`:

```python
import random, numpy as np, torch

def set_seed(seed: int) -> None:
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.cuda.manual_seed_all(seed)
    torch.backends.cudnn.deterministic = True
    torch.backends.cudnn.benchmark = False
```

- Add `seed: int = 42` to `PipelineConfig` (from Phase 1 schema).
- Call `set_seed(config.seed)` as the first operation in the training entry point.
- Add `num_workers=0` to all `DataLoader` instances to eliminate inter-process non-determinism.
- Document that full GPU determinism requires `CUBLAS_WORKSPACE_CONFIG=:4096:8` environment variable.

**Test requirement:**
- New test `tests/test_training_reproducibility.py`:
  - Run two forward passes with the same seed on the same batch → identical outputs.
  - Run two forward passes with different seeds → outputs should differ (with high probability).
  - Run full training for 2 epochs with seed=42 twice → identical `val_mse` to 6 decimal places.

**Acceptance criteria:**
- Three consecutive `run_pipeline.py --seed 42` runs produce identical `val_mse` in `Interface.py` output.

---

### 2.3 Walk-Forward Validation Framework

**New file:** `research/validation/walk_forward.py`  
**New file:** `research/validation/wf_report.py`

**Design:**

Walk-forward validation (also called time-series cross-validation) evaluates model performance on a sequence of non-overlapping out-of-sample windows, each trained on all data available before that window. This is the minimum standard for claiming a strategy "works" on unseen data.

#### 2.3.1 Expanding-Window Walk-Forward

```
Total data: [═══════════════════════════════════════════════]
Fold 1:     [TRAIN══════════════] [VALIDATION] [TEST══════]
Fold 2:     [TRAIN══════════════════════] [VALIDATION] [TEST]
...
Fold N:     [TRAIN═══════════════════════════] [VALIDATION] [TEST]
```

- Minimum training window: 252 bars (1 trading year)
- Validation window: 63 bars (3 months) — used for early stopping only
- Test window: 63 bars (3 months) — held out, never seen during training or stopping
- Step size between folds: 63 bars (non-overlapping test windows)
- Minimum folds: 5 (requires approximately 2.5 years of data total)

#### 2.3.2 WalkForwardValidator class

```python
class WalkForwardValidator:
    def __init__(self, config: PipelineConfig, n_folds: int = 5):
        ...

    def run(self, symbol: str, feature_csv: str) -> WalkForwardResult:
        """
        Returns a WalkForwardResult containing:
        - per_fold_metrics: List[FoldMetrics]  (test MSE, RMSE, MAPE per fold)
        - summary: WFSummary  (mean/std Sharpe, drawdown, win rate across folds)
        - predictions: pd.DataFrame  (stitched out-of-sample predictions)
        """
        ...
```

#### 2.3.3 Metrics collected per fold

For each fold, collect:

| Metric | Source | Why |
|--------|--------|-----|
| `test_mse` | Model prediction vs. actual close | Raw model accuracy |
| `test_rmse` | Derived | Interpretable in price units |
| `test_mape` | Derived | Scale-invariant error |
| `sharpe_ratio` | Backtest of fold's test window | Strategy performance |
| `max_drawdown` | Backtest of fold's test window | Risk metric |
| `win_rate` | Backtest of fold's test window | Signal quality |
| `total_return` | Backtest of fold's test window | Absolute performance |

**Backtest per fold:** For each fold's test window, run the C++ backtester on the fold's feature CSV slice using the fold's trained model. This requires `run_pipeline.py` to support a `--fold-start` / `--fold-end` parameter for subsetting the data.

#### 2.3.4 Output files

For each symbol:
- `output/wf_<symbol>.csv` — per-fold metrics table
- `output/wf_<symbol>_predictions.csv` — stitched out-of-sample predicted vs. actual close
- `output/wf_summary.csv` — cross-symbol summary: mean ± std per metric

**Test requirement:**
- Unit tests for `WalkForwardValidator`:
  - Correct fold boundaries for 500-bar dataset with step=63, min_train=252.
  - No fold's test window overlaps another fold's test window.
  - Fold N's training window contains all data before fold N's test window.
  - Result contains exactly `n_folds` entries.
- Integration test: run one fold end-to-end on synthetic data; assert metrics are finite.

---

### 2.4 Hyperparameter Optimisation via Optuna (F-05)

**New file:** `research/training/sweep.py`  
**New file:** `models/best_config.yaml` (output)

**Issue:** All transformer hyperparameters are currently set manually with no principled search. It is unknown whether the current `d_model=256, n_heads=8, e_layers=3` configuration is near-optimal or arbitrarily chosen.

#### 2.4.1 Search Space

```python
trial.suggest_int("d_model",    64,  512, step=64)   # must be divisible by n_heads
trial.suggest_int("n_heads",    2,   16,  step=2)    # must divide d_model
trial.suggest_int("e_layers",   1,   6)
trial.suggest_int("d_layers",   1,   4)
trial.suggest_categorical("d_ff_multiplier", [2, 4, 8])  # d_ff = d_model * multiplier
trial.suggest_float("dropout",  0.0, 0.5, step=0.05)
trial.suggest_float("lr",       1e-5, 1e-2, log=True)
trial.suggest_int("batch_size", 32, 256, step=32)
```

**Constraint:** `d_model % n_heads == 0` — enforce via `trial.suggest_int` with a post-sample check and `trial.set_user_attr("skipped", True)` + `raise optuna.TrialPruned()` if violated.

#### 2.4.2 Objective Function

- Train for `max_epochs=30` (reduced from 100 for sweep efficiency).
- Use walk-forward fold 1 only (fixed train/val split).
- Objective: minimise `val_mse` at the epoch where early stopping fires.
- Pruning: use `optuna.integration.PyTorchLightningPruningCallback` or manual intermediate reporting to prune unpromising trials early (Hyperband pruner).

#### 2.4.3 Study Configuration

```python
study = optuna.create_study(
    direction="minimize",
    sampler=optuna.samplers.TPESampler(seed=42),
    pruner=optuna.pruners.HyperbandPruner(min_resource=5, max_resource=30),
    study_name="transformer_hparam_sweep",
    storage="sqlite:///models/optuna_study.db",  # persistent across runs
)
study.optimize(objective, n_trials=50, timeout=None)
```

#### 2.4.4 Output

After the sweep:
- Print top-5 trials sorted by `val_mse`.
- Export best trial's params to `models/best_config.yaml`.
- Produce `models/sweep_results.csv` with all trial params and metrics.

**Update `run_pipeline.py`** to accept `--config models/best_config.yaml` and use it for the full 100-epoch training run.

**Test requirement:**
- Unit test: `objective()` returns a finite float for valid trial params.
- Unit test: invalid `d_model/n_heads` combination is pruned without exception.
- Integration test: `sweep.py --n-trials 3 --max-epochs 2` completes and writes `sweep_results.csv`.

**Acceptance criteria:**
- `models/optuna_study.db` exists after sweep.
- `models/best_config.yaml` contains the best trial's hyperparameters.
- `models/sweep_results.csv` has ≥ 50 rows.

---

### 2.5 Walk-Forward Summary Report

**New file:** `research/validation/wf_report.py`

After walk-forward validation runs for all symbols, generate a summary:

```
Walk-Forward Validation Summary
================================
Symbol    Folds    Mean Sharpe    Std Sharpe    Mean MaxDD    Mean Return    OOS Win Rate
------    -----    -----------    ----------    ----------    -----------    ------------
BX          5         0.28          0.12          17.4%          +8.3%         61.2%
KDP         5         0.54          0.09          11.2%         +14.7%         73.4%
...
```

Rules:
- Mean and standard deviation computed across folds, not across all bars.
- If any fold produces `NaN` metrics (e.g. due to insufficient data), exclude that fold from the mean and log a warning.
- Report is written to `output/wf_summary.txt` (human-readable) and `output/wf_summary.csv` (machine-readable).

---

## Files Changed Summary

| File | Change Type |
|------|-------------|
| `research/transformer/Interface.py` | Enhancement: seed, consolidated entry point |
| `research/training/Train.py` | Deleted |
| `research/validation/walk_forward.py` | New: walk-forward validator |
| `research/validation/wf_report.py` | New: summary report generator |
| `research/training/sweep.py` | New: Optuna hyperparameter sweep |
| `run_pipeline.py` | Enhancement: seed param, fold params, best_config support |
| `requirements.txt` | Add: `optuna>=3.0`, `pytest-cov` | Remove: `backtrader>=1.9.0` |
| `tests/test_training_reproducibility.py` | New: determinism tests |
| `tests/test_walk_forward.py` | New: fold boundary and overlap tests |
| `models/best_config.yaml` | New: generated output |
| `models/sweep_results.csv` | New: generated output |
| `output/wf_<symbol>.csv` | New: generated output per symbol |
| `output/wf_summary.csv` | New: generated output |

---

## Definition of Done

Phase 2 is complete when:
1. `python research/validation/walk_forward.py --symbol AAPL` completes without error and writes `output/wf_AAPL.csv`
2. `output/wf_AAPL.csv` contains exactly 5 rows, each with finite Sharpe and drawdown values
3. `python research/training/sweep.py --n-trials 50` completes and writes `models/best_config.yaml`
4. Three runs of `python run_pipeline.py --seed 42` produce identical `val_mse` to 6 decimal places
5. `pytest tests/test_training_reproducibility.py` passes
6. `pytest tests/test_walk_forward.py` passes
7. `backtrader` is not in `requirements.txt`
8. `research/training/Train.py` does not exist
