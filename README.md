# Distributed-Backtesting-Engine

A modular, event-driven backtesting system for evaluating machine learning trading strategies. A custom encoder-decoder Transformer is trained on 34 engineered features, exported to a C++ runtime via LibTorch, and executed inside a strongly-typed event pipeline — keeping the research layer and execution layer independently deployable and testable.

[![Python Tests](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/python-app.yml/badge.svg)](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/python-app.yml)
[![Build & Test C++](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/build.yml/badge.svg)](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/build.yml)
[![CodeQL](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/codeql.yml/badge.svg)](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/codeql.yml)

---

## System Overview

The system is structured as an end-to-end pipeline that transforms raw market data into evaluated trading performance.

**Data ingestion**

The process begins with raw OHLCV market data stored in CSV format.

**Feature engineering**

A Python-based feature pipeline generates a set of technical indicators on a per-bar basis. This logic is consistent between training and inference to ensure no training–serving skew.

**Model training**

A transformer-based model is trained using PyTorch, learning temporal patterns from sequences of historical data (e.g. 30 timesteps input, predicting 5 ahead).

**Model export**

The trained model is exported using TorchScript for efficient inference in a non-Python environment. Feature scaling parameters are also exported to ensure consistency during live evaluation.

**Backtesting engine**

A high-performance C++ backtesting engine simulates trading using an event-driven architecture. The system processes market data, generates signals via the ML strategy, executes trades, and tracks portfolio performance.

**Key components include:**


- _**Data handler:**_ streams feature-engineered data into the system
- _**ML strategy:**_ buffers input sequences and performs inference using LibTorch
- _**Portfolio:**_ manages positions and computes the equity curve
- _**Risk manager:**_ enforces constraints before order execution
- _**Execution handler:**_ simulates trade fills at market prices

**Output**

The system produces structured outputs including equity curves and trade logs for performance evaluation.

---

## Key Features

- **No Python at inference time.** The model runs inside the C++ engine via `torch::jit::load()`. No subprocess calls, no shared memory, no FFI boundary overhead.
- **Train/serve feature parity.** `pipeline.py` wraps a pandas DataFrame in a backtrader-compatible adapter and calls the same indicator functions used during training. Feature drift is structurally prevented, not convention-guarded.
- **Multi-asset execution.** `MultiAssetDataHandler` synchronises N `FeatureCSVDataHandler` instances by timestamp. All symbols sharing the earliest date are emitted as a single atomic batch, so the portfolio sees a consistent cross-sectional snapshot at every bar.
- **Risk-based position sizing.** Position quantity is `floor(equity × riskFraction / price)`, with a minimum of 1 share. Exposure is additionally capped at `maxSymbolExposure` (per symbol) and `maxTotalExposure` (portfolio-wide).
- **Correlation-aware sizing.** Before sizing a new position, the portfolio computes a 60-day rolling Pearson correlation between the candidate symbol's return series and all currently-held symbols. If `|ρ| > threshold`, the order quantity is discounted up to 50%, reducing unintentional concentration.
- **Realistic slippage model.** Fill price is `rawPrice × (1 ± halfSpread ± slippageFraction) ± marketImpact × qty`. All three components are independently configurable in `backtest_config.yaml`.
- **Buy-and-hold benchmark.** Each `EquityPoint` carries a `benchmarkEquity` field tracking an equal-weight buy-and-hold portfolio initialised at the first bar of each symbol. Alpha is reported directly in the performance summary.
- **Production-grade metrics.** `PerformanceMetrics` computes annualised Sharpe using Bessel-corrected daily portfolio returns (not per-trade returns), Information Ratio over active returns, max drawdown, and annualised total return — all exported to `ml_metrics.csv`.
- **YAML-driven configuration.** Every execution parameter — capital, risk fraction, slippage, exposure caps, symbol list, model paths — is driven by `backtest_config.yaml`. No recompilation is needed to change parameters.
- **Typed event hierarchy.** `FeatureMarketEvent` inherits from `MarketEvent`. The engine's `static_pointer_cast<MarketEvent>` is valid without modification. `MLStrategy` recovers the feature payload via `dynamic_cast` — backward-compatible with non-ML strategies.
- **Portable scaler.** `ScalerParams` is a header-only struct that mirrors `sklearn.StandardScaler`. Parameters are loaded from a CSV at startup — no Python dependency in the C++ build.
- **Optional LibTorch.** The `ml_backtest` target is only built when `find_package(Torch)` succeeds. All other targets — including `backtester_tests` — compile and pass without LibTorch installed.
- **CI coverage across both layers.** GitHub Actions runs `pytest` on the Python layer and `ctest` on the C++ layer on every push. CodeQL performs static analysis on both.

---

## Quick Start

```bash
git clone https://github.com/DylanTombs/Distributed-Backtesting-Engine.git
cd Distributed-Backtesting-Engine

python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

---

## Running a Backtest

### Option A — Docker (recommended)

```bash
# Build images
docker compose build

# Run the Python pipeline (feature engineering + training + export)
docker compose run --rm pipeline

# Run the C++ backtester
docker compose run --rm backtester /app/backtest_config.yaml
```

Output artefacts are written to `./output/` on the host via bind mount.

### Option B — Local

#### Step 1 — Build feature CSVs

```bash
python research/features/pipeline.py data/ -o features/
```

#### Step 2 — Train and export

```bash
python run_pipeline.py
# → models/transformer.pt
# → models/feature_scaler.csv   (34 entries: 33 features + close)
# → models/target_scaler.csv

# Or step by step:
python research/transformer/Interface.py --data-path features/
python research/exportModel.py
python scripts/convert_scalers.py   # converts .pkl → .csv for C++
```

#### Step 3 — Build the C++ engine

```bash
# Without LibTorch (tests only)
cmake -S backtester -B build
cmake --build build --parallel $(nproc)

# With LibTorch (ML backtest)
cmake -S backtester -B build -DCMAKE_PREFIX_PATH=/path/to/libtorch
cmake --build build --parallel $(nproc)
```

#### Step 4 — Configure and run

Edit `backtest_config.yaml` to set your symbols, file paths, and execution parameters, then:

```bash
./build/ml_backtest backtest_config.yaml
```

Output:

```
ml_equity.csv    — timestamped equity curve with benchmark column
ml_trades.csv    — per-trade log with symbol, price, quantity, direction, profit
ml_metrics.csv   — Sharpe, IR, max drawdown, alpha, annualised return
```

---

## Configuration Reference

```yaml
# backtest_config.yaml

# Symbols (supports up to 20 via symbol_0..symbol_19 / feature_csv_0..19)
symbol:      AAPL
feature_csv: /backtester/data/AAPL_features.csv

model_pt:          /models/transformer.pt
feature_scaler_csv: /models/feature_scaler.csv
target_scaler_csv:  /models/target_scaler.csv
output_dir:        /output

# Capital and sizing
initial_cash:        100000.0
risk_fraction:       0.10        # fraction of equity risked per trade
max_symbol_exposure: 0.20        # max % of equity in any one symbol
max_total_exposure:  0.80        # max % of equity deployed across all symbols
max_position_size:   10000       # absolute share cap enforced by RiskManager

# Execution friction
half_spread:       0.0005        # one-way bid-ask half-spread
slippage_fraction: 0.0005        # additional market-order slippage
market_impact:     0.0           # price impact per share ($/share)
commission:        1.0           # flat commission per trade ($)

# Portfolio analytics
risk_free_rate:         0.0      # annualised, for Sharpe / IR calculation
correlation_window:     60       # rolling window for Pearson correlation (days)
correlation_threshold:  0.7      # |ρ| above this discounts new position size
```

---

## Project Structure

```
Distributed-Backtesting-Engine/
├── .github/workflows/
│   ├── python-app.yml                      pytest + flake8 + coverage (≥ 80% gate)
│   ├── build.yml                           cmake + ctest (GTest) + lcov coverage
│   └── codeql.yml                          static analysis (Python + C++)
├── backtester/
│   ├── include/
│   │   ├── config/
│   │   │   └── BacktestConfig.hpp          YAML config parser + startup validation
│   │   ├── engine/
│   │   │   └── BacktestEngine.hpp
│   │   ├── events/                         MarketEvent, FeatureMarketEvent,
│   │   │                                   SignalEvent, OrderEvent, FillEvent
│   │   ├── execution/
│   │   │   └── SimulatedExecution.hpp      slippage + commission model
│   │   ├── market/
│   │   │   ├── FeatureCSVDataHandler.hpp   gap detection + gapCount() accessor
│   │   │   └── MultiAssetDataHandler.hpp   timestamp-synchronised N-symbol handler
│   │   ├── portfolio/
│   │   │   ├── Portfolio.hpp               risk sizing, correlation, benchmark
│   │   │   └── PerformanceMetrics.hpp      Sharpe, IR, drawdown, alpha (header-only)
│   │   ├── risk/
│   │   │   └── RiskManager.hpp
│   │   └── strategy/
│   │       ├── MLStrategy.hpp
│   │       └── Strategy.hpp
│   ├── src/                                Corresponding .cpp implementations
│   ├── tests/
│   │   ├── test_portfolio.cpp              33 unit + integration tests
│   │   ├── test_engine.cpp                 engine-level integration tests
│   │   ├── test_execution.cpp              slippage + commission fill arithmetic
│   │   ├── test_metrics.cpp                Sharpe, IR, drawdown, alpha tests
│   │   └── test_data_handler.cpp           gap detection + config validation tests
│   ├── main.cpp                            MovingAverage strategy entry point
│   └── ml_main.cpp                         ML multi-asset entry point
├── research/
│   ├── features/
│   │   ├── pipeline.py                     Feature engineering (bar-by-bar, 34 features)
│   │   └── technicalIndicators.py          Shared indicator functions
│   ├── transformer/
│   │   └── Interface.py                    Training entry point + set_seed()
│   ├── training/
│   │   └── sweep.py                        Optuna hyperparameter sweep (TPE + Hyperband)
│   ├── validation/
│   │   ├── walk_forward.py                 Expanding-window walk-forward validator
│   │   └── wf_report.py                    Multi-symbol summary report generator
│   └── exportModel.py                      TorchScript export + scaler CSV generation
├── scripts/
│   └── convert_scalers.py                  .pkl → .csv conversion (33 features + close = 34)
├── tests/                                  pytest suite (126 tests, 92% coverage)
├── docker/
│   └── entrypoint.sh                       POSIX shell config validator for Docker
├── data/                                   Raw OHLCV CSVs
├── models/                                 Exported model artefacts (.pt, scaler CSVs)
├── output/                                 Backtest output (equity, trades, metrics CSVs)
├── Dockerfile.python
├── Dockerfile.backtester                   Multi-stage: builder + minimal runtime
├── docker-compose.yml
├── backtest_config.yaml
├── run_pipeline.py                         Three-stage pipeline orchestrator (Pydantic-validated)
├── requirements.txt
├── ARCHITECTURE.md
└── DECISIONS.md
```

---

## Results

Results from backtesting `MLStrategy` across five large-cap equities using the same trained model without per-symbol fine-tuning.

| Symbol | Total Return | Sharpe Ratio | Max Drawdown | Win Rate | Profit Factor |
|--------|-------------|--------------|--------------|----------|---------------|
| BX     | +70.81%     | 0.31         | 18.36%       | 65.91%   | 2.69          |
| KDP    | +70.31%     | 0.71         | 9.67%        | 82.61%   | 6.35          |
| PEP    | +85.07%     | 0.43         | 25.91%       | 65.91%   | 2.82          |
| ASML   | +182.14%    | 0.62         | 37.46%       | 78.38%   | 3.53          |
| UNH    | +512.22%    | 0.95         | 27.39%       | 92.86%   | 11.70         |

> These results were produced with an earlier version of the execution model prior to the introduction of the realistic slippage model (`half_spread: 0.0005`, `slippage_fraction: 0.0005`, `commission: $1.00`) and risk-based position sizing. A re-run against the current engine is in progress as part of Phase 3 (HTML tearsheet generation), after which this table will be updated with auditable, post-slippage figures.

<p align="center">
  <img src="Results/Results2/performance_comparison.png" width="60%" />
</p>

<p align="center">
  <img src="Results/Results2/BX_Equity_Curve.png" width="45%" />
  <img src="Results/Results2/KDP_Equity_Curve.png" width="45%" />
</p>

<p align="center">
  <img src="Results/Results2/PEP_Equity_Curve.png" width="45%" />
  <img src="Results/Results2/UNH_Equity_Curve.png" width="45%" />
</p>

<p align="center">
  <img src="Results/Results2/trade_distributions.png" width="90%" />
</p>

---

## Limitations

- **Zero-latency fills.** Signals generated on bar *t* are filled at bar *t*'s close price. This is standard for end-of-day backtesting but would not be acceptable for intraday simulation.
- **Pearson correlation discount.** The portfolio's correlation-aware position sizing uses rolling Pearson correlation. Equity returns are non-normal (fat tails, skew); Spearman rank correlation is more appropriate and is planned for Phase 4.
- **No HTML tearsheet.** Performance output is CSV-only. A visual tearsheet (monthly returns heatmap, drawdown chart, rolling Sharpe) is the primary deliverable of Phase 3.
- **Serial feature pipeline.** `pipeline.py` processes symbols sequentially. Parallelisation over symbols via `multiprocessing.Pool` is straightforward and planned for Phase 4.
- **No short selling.** `SignalType::SHORT` is declared in the event hierarchy but unimplemented in `Portfolio` and `SimulatedExecution`. Full short-selling support is Phase 4.

---

## Roadmap

See [PDR.md](PDR.md) for the full product development roadmap. Active phases:

| Phase | Focus | Status |
|-------|-------|--------|
| [Phase 1](PHASE_1.md) | Correctness & Data Integrity | Complete — merged to `main` |
| [Phase 2](PHASE_2.md) | Validation Methodology | Code complete — walk-forward + Optuna infrastructure shipped |
| [Phase 3](PHASE_3.md) | Observability & Production Output | Not started — `spdlog`, HTML tearsheet, signal thresholds |
| [Phase 4](PHASE_4.md) | Strategy Extension & Scalability | Not started — short selling, parallel pipeline, Spearman |
