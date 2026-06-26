# Phase 4 — Strategy Extension

**Branch:** `feat/phase-4-strategy-extension`  
**Status:** Complete

## Goal

Extend the multi-asset backtester with four hardening improvements that close the gap between the research prototype and a production-grade system.

---

## Tasks

### Task 4.1 — Short Selling  ✅
Allow the engine to open and manage short positions.

**Changes:**
- `BacktestConfig`: `allow_short` (bool, default `false`) + `short_margin_rate` (float, default `1.0`) with YAML parsing and validation
- `Portfolio`: constructor extended with `allowShort`/`shortMarginRate`; `positions_[sym]` naturally goes negative for shorts; `shortEntryPrices_` tracks entry for cover P&L; `generateOrder` handles SHORT signal and EXIT-on-short (cover); `updateFill` infers BUY / SELL / SHORT / COVER from `prevPos`
- `MLStrategy`: `hasPosition_` bool replaced with `PositionDirection` enum (`FLAT`, `LONG`, `SHORT`); 3-state switch emits `LONG`, `SHORT`, or `EXIT`; SHORT only emitted when `allowShort_=true` and state is FLAT
- `BacktestEngine`: passes `allowShort` and `shortMarginRate` to Portfolio
- `backtest_config.yaml`: new short selling section (disabled by default — backward compatible)
- **Tests:** 6 `ShortPortfolioTest` cases

### Task 4.2 — Shared TorchScript Model  ✅
Prevent redundant model loads when running multiple symbols.

**Changes:**
- `MLStrategy`: static `modelCache_` map (`path → shared_ptr<Module>`); first instance for a path loads and caches; subsequent instances reuse the pointer
- `model_` changed from value type to `shared_ptr<torch::jit::script::Module>`; no API change
- **Tests:** 8 `MLStrategyTest` cases

### Task 4.3 — Feature Schema Contract  ✅
Single source of truth for the 34 feature columns shared by training and backtesting.

**Changes:**
- `feature_schema.json`: ordered list of all 34 features with dtype and description
- `FeatureSchema.hpp`: header-only class; `loadFromJSON()` parses names via regex; `validate()` checks count + order, throws `std::invalid_argument` with index and names on mismatch
- `BacktestConfig`: `featureSchemaPath` field (optional, defaults to `"feature_schema.json"`)
- `ml_main.cpp`: validates schema against `MODEL_FEATURE_COLUMNS` at startup; fails fast on mismatch
- **Tests:** 10 `FeatureSchema` cases

### Task 4.4 — Parallel Feature Pipeline  ✅
Speed up feature engineering when processing many symbols.

**Changes:**
- `research/features/pipeline.py`: added `--workers` CLI flag (default `1`, accepts `-1` for all CPUs); directory processing uses `joblib.Parallel` + `delayed`; single-file path is unchanged
- `joblib>=1.2.0` already in `requirements.txt` (added in Phase 3)

### Task 4.5 — Spearman Correlation  ✅
Replace Pearson with Spearman in `Portfolio`'s correlation discount for robustness against fat-tailed return distributions.

**Changes (landed in Commit 1):**
- `Portfolio::correlationDiscount`: calls `spearmanCorr` instead of `pearsonCorr`
- `Portfolio::rankVector` + `Portfolio::spearmanCorr`: new public static methods; Spearman reduces to Pearson on rank-transformed inputs
- **Tests:** 5 `RankVector.*` + 6 `SpearmanCorr.*` cases

---

## Exit Criteria

- [x] All 137 C++ tests pass (`ctest`)
- [x] `allow_short: false` is the default; backward-compatible with all existing configs
- [x] Model cache: second instance for the same path skips `torch::jit::load`
- [x] Schema validation fails fast on column mismatch before any data is read
- [x] `--workers N` processes N files concurrently; `-1` uses all CPUs
- [x] Spearman replaces Pearson in correlation discount

---

## Test Coverage

| Suite | Tests |
|-------|-------|
| ShortPortfolioTest | 6 |
| MLStrategyTest | 8 |
| FeatureSchemaValidate / Load | 10 |
| RankVector / SpearmanCorr | 11 |
| All other suites (unchanged) | 102 |
| **Total** | **137** |
