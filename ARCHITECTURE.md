# Architecture

## Table of Contents

- [High-Level Design](#high-level-design)
- [Component Responsibilities](#component-responsibilities)
- [Data Flow](#data-flow)
- [Backtesting Design](#backtesting-design)
- [Portfolio Model](#portfolio-model)
- [Performance Measurement](#performance-measurement)
- [Reliability and Edge Cases](#reliability-and-edge-cases)
- [Scalability Considerations](#scalability-considerations)

---

## High-Level Design

The system is split into two independent layers with a well-defined contract at the boundary of the two.

**Research Layer (Python)**

- StockDataPD.py    -> pipeline.py
- Interface.py      -> checkpoint.pth  (training entry point; `Train.py` removed)
- sweep.py          -> models/best_config.yaml  (Optuna hyperparameter search)
- walk_forward.py   -> output/wf_<symbol>.csv   (walk-forward validation)
- exportModel.py    -> transformer.pt, feature_scaler.csv, target_scaler.csv

**Execution Layer (C++)**

- FeatureCSVDataHandler
- MLStrategy + ScalerParams
- BacktestEngine (event loop)
- Portfolio + RiskManager + Execution

 ---

### Component Responsibilities

| Component | Layer | Responsibility |
|---|---|---|
| `pipeline.py` | Python | Produce enriched feature CSVs from raw OHLCV; identical to training path |
| `Interface.py` | Python | Train and validate the Transformer; checkpoint best model; exposes `set_seed()` for reproducible runs |
| `sweep.py` | Python | Optuna hyperparameter sweep (TPE sampler, Hyperband pruner, SQLite-persistent study) |
| `walk_forward.py` | Python | Expanding-window walk-forward validator; exports per-fold metrics CSV per symbol |
| `wf_report.py` | Python | Aggregates per-symbol fold CSVs into a multi-symbol summary table (mean ± std) |
| `exportModel.py` | Python | Trace model to TorchScript; export scaler parameters to CSV |
| `convert_scalers.py` | Python | Merge `featureScaler.pkl` + `targetScaler.pkl` into 34-entry `feature_scaler.csv` |
| `run_pipeline.py` | Python | Three-stage orchestrator: pipeline → train → export, with `--skip-train` flag |
| `BacktestConfig` | C++ | Header-only YAML parser; single source of truth for all execution parameters |
| `MultiAssetDataHandler` | C++ | Wraps N `FeatureCSVDataHandler`s; synchronises by timestamp; emits all same-date events per tick |
| `FeatureCSVDataHandler` | C++ | Reads feature CSVs bar-by-bar; emits `FeatureMarketEvent` per bar |
| `MLStrategy` | C++ | Buffers a rolling 30-bar window; runs LibTorch inference; emits `SignalEvent` |
| `MultiSymbolStrategy` | C++ | Routes `MarketEvent`s to per-symbol `MLStrategy` instances; single `Strategy&` entry point |
| `ScalerParams` | C++ | Loads and applies `sklearn.StandardScaler` parameters without Python |
| `BacktestEngine` | C++ | Dispatches events through the MARKET→SIGNAL→ORDER→FILL pipeline |
| `Portfolio` | C++ | Risk-based sizing; exposure caps; correlation discount; benchmark tracking; equity curve |
| `RiskManager` | C++ | Pre-trade gate: enforces absolute and relative position limits |
| `SimulatedExecution` | C++ | Converts approved orders to fills; applies half-spread, slippage, market impact, commission |
| `PerformanceMetrics` | C++ | Computes Sharpe, Information Ratio, max drawdown, alpha; exports to CSV |

---

## Data Flow

### 1. Data Acquisition

`StockDataPD.py` fetches OHLCV bars from Yahoo Finance and writes per-symbol CSVs to `data/`. Each file has the columns: `date`, `open`, `high`, `low`, `close`, `volume`, `adj close`.

### 2. Feature Engineering

`pipeline.py` processes each raw CSV and writes an enriched CSV to `features/`. Feature computation is **bar-by-bar** using a backtrader-compatible adapter layer (`_Line`, `_DataView`, `_BarCtx`) that wraps the pandas DataFrame. This adapter calls the same functions in `technicalIndicators.py` that were used during model training, making train/serve feature parity a structural property rather than a convention.

Output columns (34 total, fixed order):

- `timestamp`, `high`, `low`, `volume`, `adj close`
- `P`, `R1`, `R2`, `R3`, `S1`, `S2`, `S3` — pivot points
- `obv`, `volume_zscore` — volume
- `rsi`, `macd`, `macds`, `macdh` — momentum
- `sma`, `lma`, `sema`, `lema` — moving averages
- `overnight_gap`, `return_lag_1`, `return_lag_3`, `return_lag_5`, `volatility` — return features
- `SR_K`, `SR_D`, `SR_RSI_K`, `SR_RSI_D` — stochastic oscillators
- `ATR`, `HL_PCT`, `PCT_CHG` — volatility
- `close` — prediction target (always last)

### 3. Scaler Export

`convert_scalers.py` merges the two pickle files produced by training:

```
featureScaler.pkl  (33 features)  ┐
                                  ├──► feature_scaler.csv  (34 rows)
targetScaler.pkl   (close)        ┘

target_scaler.csv  (1 row — close, used for inverse-scaling predictions)
```

The 34-entry scaler matches the feature vector passed to `MLStrategy`: 33 OHLCV + indicator features followed by `close`. `ScalerParams::transform()` applies the scaler to the entire 34-element vector in one pass.

### 4. Model Export

`exportModel.py` performs two operations:

**TorchScript export:**
```
TransformerInferenceWrapper(model)
        │
        │  torch.jit.trace(dummy_xEnc, dummy_xMarkEnc)
        ▼
models/transformer.pt          ← loaded in C++ via torch::jit::load()
```

The wrapper fuses encoder and decoder into a single `forward(xEnc, xMarkEnc)` call, matching the signature expected by `MLStrategy::runInference()`.

### 5. C++ Backtest Execution

`ml_main.cpp` loads `backtest_config.yaml` and wires the engine:

```
BacktestConfig::loadFromYAML(argv[1])
        │
        ├─► for each symbol: FeatureCSVDataHandler → MultiAssetDataHandler
        └─► for each symbol: MLStrategy → MultiSymbolStrategy
        │
        ▼
BacktestEngine::run()
        │
        │  [only fetch next bar when event queue is fully drained]
        │
        ├─ MARKET  → Portfolio::updateMarket(price, timestamp)
        │              └─ update per-symbol benchmark equity
        │            MLStrategy::onMarketEvent()
        │              ├─ dynamic_cast<FeatureMarketEvent*>
        │              ├─ ScalerParams::transform(features[34])
        │              ├─ featureBuffer_.push_back()
        │              └─ runInference()  →  predicted close (inverse-scaled)
        │                     └─ emit SignalEvent(LONG | EXIT)
        │
        ├─ SIGNAL  → Portfolio::generateOrder()
        │              ├─ LONG:  qty = floor(equity * riskFraction / price)
        │              │         apply exposure cap (symbol + total)
        │              │         apply correlation discount (up to 50%)
        │              └─ EXIT:  qty = full held position
        │            emit OrderEvent
        │
        ├─ ORDER   → RiskManager::approveOrder()  (absolute size cap)
        │            SimulatedExecution::executeOrder()
        │              └─ fillPrice = price × (1 ± halfSpread ± slippage) ± impact × qty
        │            emit FillEvent
        │
        └─ FILL    → Portfolio::updateFill()
                       └─ update cash, positions, trade log
        │
        ▼
PerformanceMetrics::compute(equityCurve, riskFreeRate)
        │
        ├─ dailyReturns[i] = (equity[i] / equity[i-1]) - 1
        ├─ sharpeRatio     = mean(excess) / stddev(excess) × sqrt(252)
        ├─ informationRatio = mean(activeReturns) / stddev(activeReturns) × sqrt(252)
        └─ maxDrawdown, totalReturn, annualisedReturn, alpha
        │
        ▼
ml_equity.csv  /  ml_trades.csv  /  ml_metrics.csv
```

---

## Backtesting Design

### Simulation assumptions

| Assumption | Current behaviour |
|---|---|
| Fill price | `price × (1 ± halfSpread ± slippageFraction) ± marketImpact × qty` |
| Commission | Fixed per trade (configurable, default $1.00) |
| Position sizing | `floor(equity × riskFraction / price)`, minimum 1 share |
| Exposure cap (symbol) | `maxSymbolExposure × equity` — enforced in `generateOrder` |
| Exposure cap (total) | `maxTotalExposure × equity` — enforced in `generateOrder` |
| Correlation discount | Rolling 60-day Pearson; `\|ρ\| > threshold` reduces qty up to 50% |
| EXIT signal | Sells the full held quantity, closing the position entirely |
| Short selling | Not implemented (`SignalType::SHORT` present but not handled) |
| Latency | Zero (signal and fill occur on the same bar — standard for EOD simulation) |

### Event queue ordering

The engine only calls `dataHandler.streamNext()` when the event queue is empty. This ensures the full MARKET→SIGNAL→ORDER→FILL chain for bar *t* completes before bar *t+1* is fetched. Without this invariant, an EXIT signal arriving on bar *t+1* would see a stale (zero) position if the BUY fill from bar *t* had not yet been applied.

```cpp
if (queue.empty())
    dataHandler.streamNext(queue);   // fetch next bar only when queue is drained

if (queue.empty())
    break;                           // streamNext produced nothing → data exhausted
```

### Multi-asset synchronisation

`MultiAssetDataHandler` pre-fetches one event from each symbol's handler. On each `streamNext()` call it finds the earliest timestamp across all non-exhausted channels and emits all channels at that timestamp. This guarantees:

1. The portfolio sees all symbols at a given date in a single iteration, not spread across multiple iterations.
2. Symbols with missing bars at a given date are naturally skipped — the next available bar from that symbol is emitted at its own timestamp.

---

## Portfolio Model

### Risk-based sizing

```
qty = max(1, floor(equity × riskFraction / price))
qty = min(qty, floor(maxSymbolExposure × equity / price) - existing_position)
qty = min(qty, floor((maxTotalExposure - current_exposure) × equity / price))
```

### Correlation-aware discount

Before finalising `qty` for a LONG signal on symbol *s*:

1. Compute rolling Pearson correlation between *s*'s daily return series and each currently-held symbol's return series over the last `correlationWindow` days.
2. For any held symbol where `|ρ| > correlationThreshold`, apply a discount: `qty *= (1 - |ρ| × 0.5)`.
3. Use the minimum discounted quantity across all correlated symbols.

This reduces unintentional concentration risk when multiple signals fire simultaneously on highly correlated instruments.

### Buy-and-hold benchmark

At the first bar each symbol appears, the portfolio allocates `initialCash / nSymbols` to that symbol's benchmark position:

```
benchmarkUnits[sym] = (initialCash / nSymbols) / initialPrice
```

At each subsequent bar, `benchmarkEquity = sum over all seen symbols of (benchmarkUnits[sym] × currentPrice[sym])`. This is tracked in `EquityPoint::benchmarkEquity` alongside the strategy equity.

---

## Performance Measurement

`PerformanceMetrics::compute()` is header-only and operates on the completed equity curve:

```
dailyExcessReturn[i] = (equity[i]/equity[i-1] - 1) - riskFreeRate/252
sharpeRatio          = mean(dailyExcessReturn) / stddev_bessel(dailyExcessReturn) × sqrt(252)

activeReturn[i]      = strategyDailyReturn[i] - benchmarkDailyReturn[i]
informationRatio     = mean(activeReturn) / stddev_bessel(activeReturn) × sqrt(252)

maxDrawdown          = max over all i of (peak[i] - equity[i]) / peak[i]
alpha                = totalReturn - benchmarkReturn
```

Bessel correction (`n-1` denominator) is used for both Sharpe and IR to avoid overstating precision on short histories.

---

## Reliability and Edge Cases

### Missing data

`FeatureCSVDataHandler` reads the CSV sequentially and emits one event per non-empty row. It does not detect gaps in the date sequence. If a symbol has missing bars (weekends, trading halts), the engine processes them as adjacent bars. The rolling window in `MLStrategy` is not aware of a gap, which can cause the time mark encoding to be inconsistent.

**Mitigation:** `pipeline.py` produces output only for rows where all 34 features can be computed. Rows within the warm-up period (e.g. the first 26 bars needed for a 26-period EMA) are dropped, so the feature CSV starts at a bar where all values are valid.

### Data leakage prevention

The training/validation split in `DataFrameDataset` is performed by index before the scaler is fit. The scaler is fit on training windows only and applied to both splits. This is validated by `test_dataset.py::test_scaler_fit_on_train_only`.

Ticker-boundary leakage was fixed in Phase 1. `DataFrameDataset.__getitem__` now indexes via `self.valid_indices[index]`, which only contains window start positions that fall entirely within a single symbol's data range. This is validated by `test_dataset.py::test_windows_do_not_cross_ticker_boundaries`.

### Feature consistency

1. `technicalIndicators.py` is the single source of all indicator logic.
2. `pipeline.py` calls those functions through an adapter; it does not reimplement them.
3. `exportModel.py::load_args()` defines the canonical feature list and column order.
4. `ml_main.cpp` passes that same list (`MODEL_FEATURE_COLUMNS`) to `FeatureCSVDataHandler` at construction.
5. `convert_scalers.py` constructs `feature_scaler.csv` using the same 34-column order.

Any divergence in feature count causes `ScalerParams::transform` to throw a size-mismatch error at startup — an explicit failure rather than silent corruption.

### Reproducibility

Training reproducibility was addressed in Phase 2. `Interface.py` exposes `set_seed(seed)` which fixes `random`, `numpy`, `torch`, and CUDA seeds, and sets `cudnn.deterministic = True`. All `DataLoader` instances use `num_workers=0` to eliminate inter-process non-determinism. For full GPU determinism, set `CUBLAS_WORKSPACE_CONFIG=:4096:8`. The exported `transformer.pt` is a fixed artefact — backtests against the same feature CSVs are fully deterministic regardless of seed.

---

## Scalability Considerations

| Concern | Current state | Path to improvement |
|---|---|---|
| Multi-symbol execution | `MultiAssetDataHandler` handles N symbols in a single process | Shard symbols across processes; aggregate fills via a message queue |
| CSV in memory | `FeatureCSVDataHandler` streams row-by-row but loads the full file at construction | Switch to memory-mapped reads or a DuckDB backend for large symbol sets |
| Model loading | `torch::jit::load()` called once per process; all per-symbol strategies share the same model file | Already shared via `MultiSymbolStrategy`; could further share the loaded `torch::jit::Module` reference |
| Feature pipeline | Per-symbol serial processing in `pipeline.py` | Embarrassingly parallel over symbols; trivially parallelisable with `multiprocessing.Pool` |
| Configuration | Flat YAML file | Replace with a schema-validated config (e.g. JSON Schema) with a registry for multi-environment deployments |
