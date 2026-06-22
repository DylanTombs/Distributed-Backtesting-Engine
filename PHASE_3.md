# Phase 3 — Observability & Production Output

**Status:** In Progress — code complete on `feat/phase-3-observability`, pending C++ build verification  
**Prerequisites:** Phase 1 complete  
**Can run in parallel with:** Phase 2, Phase 4  

---

## Objective

Make the system's runtime behaviour inspectable without reading source code, and make backtest results consumable without opening a spreadsheet. Every backtest run should produce a rich, self-contained HTML performance tearsheet alongside the existing CSV outputs.

**Why Phase 1 must precede Phase 3:** Structured logging and the tearsheet need to reflect trustworthy metrics. Integrating `spdlog` into a system with unvalidated components (no slippage tests, no metrics tests) would log numbers that cannot be trusted. The tearsheet would visualise incorrect results.

**Exit criteria (all must be satisfied before Phase 3 is closed):**
- [x] All `std::cout` replaced with levelled `spdlog` calls in every C++ component
- [x] Log level and log file path configurable via `backtest_config.yaml`
- [x] HTML tearsheet generated automatically at end of every `run_pipeline.py` invocation
- [x] Tearsheet contains ≥ 5 distinct panels (6 figure panels + summary stats)
- [x] `buy_threshold` and `exit_threshold` exposed in `backtest_config.yaml`
- [x] `tests/test_tearsheet.py` — 23 tests covering pure helpers + integration + performance (< 10 s)
- [x] C++ threshold config tests added to `backtester/tests/test_data_handler.cpp`
- [x] Tearsheet generation completes in < 10 seconds for a 2-year backtest (verified in CI)
- [ ] Tearsheet generation test run against real backtest CSVs (requires `./ml_backtest` run)
- [ ] Signal threshold behavioural test: `buy_threshold=0.10` → fewer trades than `buy_threshold=0.001`
- [ ] All tasks in this document marked complete

---

## Task Breakdown

### 3.1 Structured Logging via spdlog in C++ Engine (F-03)

**Files affected:**  
- `backtester/include/engine/BacktestEngine.hpp` / `.cpp`  
- `backtester/include/portfolio/Portfolio.hpp` / `.cpp`  
- `backtester/include/strategy/MLStrategy.hpp` / `.cpp`  
- `backtester/include/market/FeatureCSVDataHandler.hpp` / `.cpp`  
- `backtester/include/market/MultiAssetDataHandler.hpp` / `.cpp`  
- `backtester/include/execution/SimulatedExecution.hpp` / `.cpp`  
- `backtester/include/config/BacktestConfig.hpp`  
- `backtester/CMakeLists.txt`  
- `backtest_config.yaml`

#### 3.1.1 Dependency Integration

Add `spdlog` via CMake `FetchContent` (header-only mode, same pattern as GTest):

```cmake
FetchContent_Declare(
    spdlog
    GIT_REPOSITORY https://github.com/gabime/spdlog.git
    GIT_TAG        v1.14.1
)
FetchContent_MakeAvailable(spdlog)
target_link_libraries(ml_backtest PRIVATE spdlog::spdlog_header_only)
target_link_libraries(backtester_tests PRIVATE spdlog::spdlog_header_only)
```

Use header-only to keep the Docker production image build lightweight.

#### 3.1.2 Logger Initialisation

Create `backtester/include/logging/Logger.hpp` (header-only, initialised once from `ml_main.cpp`):

```cpp
// Logger.hpp
#pragma once
#include <spdlog/spdlog.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <spdlog/sinks/basic_file_sink.h>

inline void initLogger(const std::string& logFile, spdlog::level::level_enum level) {
    auto consoleSink = std::make_shared<spdlog::sinks::stdout_color_sink_mt>();
    auto fileSink    = std::make_shared<spdlog::sinks::basic_file_sink_mt>(logFile, true);

    auto logger = std::make_shared<spdlog::logger>(
        "backtester", spdlog::sinks_init_list{consoleSink, fileSink}
    );
    logger->set_level(level);
    logger->set_pattern("[%Y-%m-%d %H:%M:%S.%e] [%l] [%n] %v");
    spdlog::set_default_logger(logger);
}
```

Add to `BacktestConfig`:
```yaml
log_level:  info     # trace | debug | info | warn | error | critical
log_file:   output/backtest.log
```

#### 3.1.3 Log Call Specifications per Component

**BacktestEngine:**
```cpp
spdlog::info("Backtest started — {} symbols, {} bars",  nSymbols, nBars);
spdlog::info("Bar {}/{} — {}",  barIndex, totalBars, timestamp);
spdlog::info("Backtest complete — {} trades executed",  nTrades);
```

**MLStrategy (per signal):**
```cpp
spdlog::debug("Symbol {} | predicted={:.4f} current={:.4f} → LONG signal", symbol, pred, price);
spdlog::debug("Symbol {} | predicted={:.4f} current={:.4f} → EXIT signal", symbol, pred, price);
spdlog::debug("Symbol {} | buffer not full ({}/{}), no signal",  symbol, bufSize, seqLen);
```

**Portfolio (per order/fill):**
```cpp
spdlog::info("ORDER {} {} × {} @ {:.2f} | equity={:.2f}", direction, qty, symbol, price, equity);
spdlog::info("FILL  {} {} × {} @ {:.2f} | cash={:.2f}",  direction, qty, symbol, fill, cash);
spdlog::warn("Order rejected: {} exposure cap exceeded for {}",  capType, symbol);
spdlog::warn("Correlation discount applied: {}  ρ={:.3f}  qty {} → {}",  symbol, corr, origQty, newQty);
```

**FeatureCSVDataHandler (on gap detection from Phase 1):**
```cpp
spdlog::warn("Date gap detected in {}: expected ≤{}d after {}, got {}",  symbol, maxGap, prev, curr);
```

**SimulatedExecution:**
```cpp
spdlog::debug("Slippage: rawPrice={:.4f} halfSpread={:.4f} slipFrac={:.4f} impact={:.6f} × {} → fillPrice={:.4f}",
              rawPrice, halfSpread_, slippageFrac_, marketImpact_, qty, fillPrice);
```

**BacktestConfig (schema violations from Phase 1):**
```cpp
spdlog::error("[CONFIG] {}: {}", fieldName, violationReason);
```

#### 3.1.4 Log Level Guidelines

| Level | Usage |
|-------|-------|
| `trace` | Feature vector values per bar (very verbose — for debugging MLStrategy) |
| `debug` | Per-signal decisions, per-bar slippage breakdown |
| `info` | Trade executions, key lifecycle events (start, end, milestones) |
| `warn` | Correlation discounts, date gaps, order rejections, config edge cases |
| `error` | Config validation failures, file not found, feature size mismatch |
| `critical` | Unrecoverable errors before `std::terminate` |

#### 3.1.5 Test requirement

- In test files, redirect `spdlog` to a `ostream_sink` so log output can be captured and asserted on.
- Test: correlation discount triggers a `warn`-level log.
- Test: order rejection (exposure cap) triggers a `warn`-level log.
- Test: config schema violation triggers an `error`-level log.

---

### 3.2 Configurable Signal Thresholds (B-04)

**Files:** `backtester/include/strategy/MLStrategy.hpp`, `backtester/include/config/BacktestConfig.hpp`, `backtest_config.yaml`

**Issue:** `buyThreshold_` (0.005 = 0.5% upside required to trigger LONG) and `exitThreshold_` (0.0 = any decline triggers EXIT) are hardcoded constants in `MLStrategy`. They cannot be tuned without recompiling.

**Required change:**

Add to `BacktestConfig`:
```yaml
# Signal thresholds
buy_threshold:   0.005    # Fractional predicted upside required for LONG signal
exit_threshold:  0.000    # Fractional predicted decline required for EXIT signal
```

Validate in schema (Phase 1 pattern):
- `buy_threshold`: 0.0 ≤ x ≤ 0.1 (0–10% upside requirement is a sane range)
- `exit_threshold`: 0.0 ≤ x ≤ 0.1

Pass through `BacktestConfig` → `MLStrategy` constructor. Remove hardcoded constants.

**Test requirement:**
- Unit test: `buy_threshold=0.01` — signal fires only when predicted > price × 1.01.
- Unit test: `buy_threshold=0.0` — signal fires for any positive prediction.
- Unit test: `exit_threshold=0.005` — EXIT only when predicted < price × 0.995.
- Integration test: config with `buy_threshold=0.10` produces fewer trades than `buy_threshold=0.001` on same data.

**Acceptance criteria:**
- Changing `buy_threshold` in `backtest_config.yaml` changes strategy behaviour without recompilation.

---

### 3.3 HTML Performance Tearsheet (F-04)

**New file:** `research/analysis/tearsheet.py`  
**Updated file:** `run_pipeline.py`  
**Dependencies:** `plotly>=5.0`, `jinja2>=3.0` (add to `requirements.txt`)

#### 3.3.1 Tearsheet Panels

The tearsheet is a single self-contained HTML file (all assets inlined — no external CDN required). It is generated from `ml_equity.csv`, `ml_trades.csv`, and `ml_metrics.csv`.

| Panel | Chart Type | Data Source | Description |
|-------|-----------|-------------|-------------|
| **Equity Curve** | Line (strategy + benchmark) | `ml_equity.csv` | Strategy equity vs. equal-weight buy-and-hold over full backtest period |
| **Underwater Plot** | Area (filled below zero) | `ml_equity.csv` | Drawdown from peak at every bar; highlights drawdown periods in red |
| **Rolling 60-Day Sharpe** | Line | `ml_equity.csv` | Rolling annualised Sharpe computed over a 60-bar window |
| **Monthly Returns Heatmap** | Heatmap (months × years) | `ml_equity.csv` | Monthly return % per cell; green/red diverging colour scale; total per column |
| **Trade Distribution** | Histogram | `ml_trades.csv` | Distribution of per-trade P&L; vertical lines at mean and median |
| **Per-Symbol Contribution** | Bar chart | `ml_trades.csv` | Total realised P&L contributed by each symbol |
| **Summary Statistics** | Table | `ml_metrics.csv` | Sharpe, IR, max drawdown, alpha, win rate, profit factor, annualised return |

Minimum required panels: 5. All 7 above should be implemented.

#### 3.3.2 Tearsheet Layout

```html
<header>
  TradingTransformer | Run: 2026-04-01 | Symbols: AAPL, MSFT, ... | Config: backtest_config.yaml
</header>

<section id="summary">   Summary statistics table  </section>
<section id="equity">    Equity curve panel         </section>
<section id="drawdown">  Underwater plot            </section>
<section id="rolling">   Rolling Sharpe             </section>
<section id="monthly">   Monthly heatmap            </section>
<section id="trades">    Trade distribution         </section>
<section id="symbols">   Per-symbol contribution    </section>
```

#### 3.3.3 Tearsheet Generator API

```python
class Tearsheet:
    def __init__(self, equity_csv: str, trades_csv: str, metrics_csv: str,
                 config_path: str, output_path: str):
        ...

    def generate(self) -> None:
        """
        Reads CSV inputs, generates all panels as Plotly figures,
        serialises to HTML via Jinja2 template, writes to output_path.
        Must complete in < 10 seconds for a 2-year daily backtest (~500 bars).
        """
        ...
```

#### 3.3.4 Integration with run_pipeline.py

Add a `--tearsheet` flag to `run_pipeline.py` (enabled by default):

```bash
python run_pipeline.py              # runs pipeline + generates tearsheet
python run_pipeline.py --no-tearsheet  # skips tearsheet (CI/headless environments)
```

Tearsheet output: `output/tearsheet_<timestamp>.html`

#### 3.3.5 Test requirement

- Unit tests for each panel's data preparation function:
  - `compute_drawdown_series(equity)` — peak-trough series correct for known input.
  - `compute_rolling_sharpe(equity, window=60)` — first 59 values are NaN; value at bar 60 matches hand computation.
  - `compute_monthly_returns(equity)` — aggregates daily returns to monthly; handles month-end correctly.
  - `compute_per_symbol_pnl(trades)` — groups by symbol and sums realised profit.
- Integration test: `tearsheet.generate()` completes without error on synthetic CSV data.
- Integration test: output file is valid HTML with all 7 panel `id` attributes present.
- Performance test: `tearsheet.generate()` completes in < 10 seconds on a 500-row equity CSV.

**Acceptance criteria:**
- `output/tearsheet_<timestamp>.html` created automatically at end of every `run_pipeline.py` run.
- File opens in a browser without external network requests (self-contained).
- All 7 panels render with correct data.

---

## Files Changed Summary

| File | Change Type |
|------|-------------|
| `backtester/CMakeLists.txt` | Add: `spdlog` via `FetchContent` |
| `backtester/include/logging/Logger.hpp` | New: logger initialisation |
| `backtester/include/config/BacktestConfig.hpp` | Add: `log_level`, `log_file`, `buy_threshold`, `exit_threshold` |
| `backtester/include/strategy/MLStrategy.hpp` | Remove hardcoded thresholds; accept from config |
| `backtester/src/strategy/MLStrategy.cpp` | Use `spdlog` for signal decisions |
| `backtester/src/engine/BacktestEngine.cpp` | Replace `std::cout` with `spdlog` |
| `backtester/src/portfolio/Portfolio.cpp` | Replace `std::cout` with `spdlog` |
| `backtester/src/execution/SimulatedExecution.cpp` | Replace `std::cout` with `spdlog` |
| `backtester/src/market/FeatureCSVDataHandler.cpp` | Replace `std::cout` with `spdlog` |
| `backtester/src/market/MultiAssetDataHandler.cpp` | Replace `std::cout` with `spdlog` |
| `backtester/ml_main.cpp` | Call `initLogger()` at startup |
| `backtest_config.yaml` | Add: `log_level`, `log_file`, `buy_threshold`, `exit_threshold` |
| `research/analysis/tearsheet.py` | New: HTML tearsheet generator |
| `run_pipeline.py` | Add: tearsheet step, `--no-tearsheet` flag |
| `requirements.txt` | Add: `plotly>=5.0`, `jinja2>=3.0` |
| `tests/test_tearsheet.py` | New: panel data and integration tests |

---

## Definition of Done

Phase 3 is complete when:
1. `grep -r "std::cout" backtester/src/` returns zero results
2. `./build/ml_backtest backtest_config.yaml` produces `output/backtest.log` with timestamped, levelled entries
3. `python run_pipeline.py` produces `output/tearsheet_<timestamp>.html` without `--tearsheet` flag needed
4. The tearsheet HTML opens in Chrome/Firefox without console errors and with all 7 panels visible
5. Changing `buy_threshold: 0.0` vs `buy_threshold: 0.05` in YAML produces measurably different trade counts in the backtest
6. `pytest tests/test_tearsheet.py` passes in < 30 seconds
