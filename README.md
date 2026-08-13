# TradingTransformer

A contextual backtesting engine that runs from your browser. Read a market
news article, click one button, and get a backtest of a transparent rule
strategy over the window that article describes.

A Chrome extension sits in the corner of every financial news page. It extracts
the event and date window from the article text, hands them to a FastAPI
bridge, which invokes a strongly-typed C++ event-driven backtester and returns
an equity curve in seconds.

[![Python Tests](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/python-app.yml/badge.svg)](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/python-app.yml)
[![Build & Test C++](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/build.yml/badge.svg)](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/build.yml)
[![CodeQL](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/codeql.yml/badge.svg)](https://github.com/DylanTombs/Distributed-Backtesting-Engine/actions/workflows/codeql.yml)

---

## System Overview

```
Chrome extension  (any financial news page)
      │  article text
      ▼
Context extraction  (Python)      rule-based match → Claude Haiku fallback
      │  ticker + date window
      ▼
FastAPI bridge  (research/api)    on-demand OHLCV fetch, LRU result cache
      │  strategy spec + OHLCV CSV
      ▼
C++ backtesting engine            event-driven, multi-asset, rule strategies
      │
      ▼
Equity curve + metrics → popup    Sharpe · drawdown · return · win rate
```

---

## Key Features

**Execution engine (C++)**
- **Strict event ordering.** `MARKET → SIGNAL → ORDER → FILL`, with the queue
  fully drained before the next bar is fetched — no fill/signal races.
- **Transparent rule strategies.** Every signal traces to a stated indicator
  condition. No black box on the product path.
- **Multi-asset execution.** `MultiAssetDataHandler` synchronises N data
  handlers by timestamp for consistent cross-sectional snapshots.
- **Risk-based sizing.** `floor(equity × riskFraction / price)`, capped by
  `maxSymbolExposure` and `maxTotalExposure`.
- **Correlation-aware sizing.** A 60-day rolling Pearson correlation discounts
  new positions when `|ρ| > threshold`.
- **Realistic slippage.** `fill = rawPrice × (1 ± halfSpread ± slippage) ± marketImpact × qty`.
- **Indicator cross-validation.** The C++ indicators are pinned against a
  Python reference oracle by `tests/test_indicator_crossval.py`.

**API bridge (Python)**
- Four endpoints: `/api/context`, `/api/backtest`, `/api/events`, `/api/health`
- API-key auth, per-route rate limiting, CORS scoped to `chrome-extension://`
- On-demand OHLCV fetch — no checked-in market data corpus
- LRU cache (50 entries), pre-warmed at startup for all curated events

**Browser extension**
- Floating candlestick button on every page
- Auto-detects market events from page text (rule-based + Claude Haiku fallback)
- 41 curated events with canonical date windows (COVID crash, GFC, dot-com,
  SVB, DeepSeek shock, …)
- Popup renders the equity curve on canvas with metric cards

---

## Quick Start

### Run the API

```bash
pip install -r requirements.txt
uvicorn research.api.app:app --port 8502 --reload
```

### Load the extension

`chrome://extensions` → Developer mode → Load unpacked → select `extension/`.
A candlestick icon appears in the bottom-right of every page.

### Run the backtester directly

```bash
cmake -S backtester -B build && cmake --build build --parallel
./backtester/backtest <spec_json> <ohlcv_csv> <symbol>
```

### Deploy

```bash
./scripts/deploy_cloudrun.sh      # root Dockerfile → Google Cloud Run
./scripts/pack_extension.sh       # zip extension/ for the Chrome Web Store
```

---

## Configuration

`backtest_config.yaml` controls the strategy without recompiling:

```yaml
# Capital & sizing
initial_cash:        100000.0
risk_fraction:       0.10        # fraction of equity per trade
max_symbol_exposure: 0.20
max_total_exposure:  0.80

# Execution friction
half_spread:       0.0005
slippage_fraction: 0.0005
commission:        1.0           # $/trade

# Signal thresholds
buy_threshold:  0.005
exit_threshold: 0.000

# Portfolio analytics
risk_free_rate:        0.0
correlation_window:    60
correlation_threshold: 0.7
```

---

## Project Structure

```
TradingTransformer/
├── .github/workflows/
│   ├── python-app.yml              pytest + coverage gate (≥ 80%)
│   ├── build.yml                   cmake + ctest + lcov
│   └── codeql.yml                  static analysis (Python + C++)
│
├── backtester/
│   ├── include/config/             BacktestConfig.hpp — YAML parser
│   ├── include/engine/             BacktestEngine.hpp
│   ├── include/events/             MarketEvent, SignalEvent, OrderEvent, FillEvent
│   ├── include/execution/          SimulatedExecution — slippage + commission
│   ├── include/market/             CSV + multi-asset data handlers
│   ├── include/portfolio/          Portfolio, PerformanceMetrics (header-only)
│   ├── include/strategy/           RuleStrategy (product), MLStrategy (legacy)
│   ├── tests/                      GTest suite
│   ├── backtest                    Compiled rule-strategy binary  ← product path
│   └── ml_backtest                 Compiled legacy ML binary (opt-in)
│
├── research/
│   ├── api/                        FastAPI bridge for the browser extension
│   │   ├── app.py                  4 endpoints
│   │   ├── runner.py               Binary invocation + LRU cache
│   │   ├── strategies.py           Rule-spec resolution
│   │   ├── auth.py  rate_limit.py  cors.py  schemas.py
│   ├── context/                    Event extraction pipeline
│   │   ├── events.py               41 curated events with date windows
│   │   ├── entities.py             Ticker regex + dateparser date extraction
│   │   ├── scraper.py              trafilatura extraction + httpx fallback
│   │   └── extractor.py            Two-pass: rule-based → Claude Haiku fallback
│   ├── data/fetcher.py             On-demand OHLCV fetch
│   ├── features/
│   │   └── technicalIndicators.py  Reference oracle for the C++ indicators
│   └── transformer/                LEGACY — see below
│
├── extension/                      Chrome extension (Manifest V3)
│   ├── manifest.json               activeTab + storage
│   ├── background.js               Service worker — all API calls routed here
│   ├── content.js                  Floating FAB injected on every page
│   └── popup/                      400×600 dark-theme popup
│
├── scripts/
│   ├── deploy_cloudrun.sh          Build + deploy to Cloud Run
│   ├── pack_extension.sh           Chrome Web Store bundle
│   └── gen_indicator_fixture.py    Regenerate the cross-validation fixture
├── tests/                          pytest suite (260 tests, ~94% coverage)
├── models/                         transformer.pt + scaler CSVs (legacy ML path)
├── Dockerfile                      Cloud Run image
├── backtest_config.yaml
├── ARCHITECTURE.md
└── DECISIONS.md
```

---

## Legacy: the transformer

The project began as an ML research system — a custom encoder-decoder
Transformer trained on 34 engineered features, exported to TorchScript, and run
inside the C++ engine via LibTorch. That path still works and is reachable by
opting in with `{"template": "ml_transformer"}`, where it is flagged
`experimental` end-to-end.

`research/transformer/` is retained as a record of how `models/transformer.pt`
was built. It is not on the product path, has no tests, and is excluded from
coverage. Its dependencies live in `requirements-legacy.txt`. The feature
pipeline, Optuna sweep, walk-forward validation, and Streamlit research
dashboard that supported it were removed once the project settled on
transparent rule strategies as the product.

Results from that era, backtesting `MLStrategy` across five large-cap equities
with a single model and no per-symbol fine-tuning:

| Symbol | Total Return | Sharpe Ratio | Max Drawdown | Win Rate | Profit Factor |
|--------|-------------|--------------|--------------|----------|---------------|
| BX     | +70.81%     | 0.31         | 18.36%       | 65.91%   | 2.69          |
| KDP    | +70.31%     | 0.71         | 9.67%        | 82.61%   | 6.35          |
| PEP    | +85.07%     | 0.43         | 25.91%       | 65.91%   | 2.82          |
| ASML   | +182.14%    | 0.62         | 37.46%       | 78.38%   | 3.53          |
| UNH    | +512.22%    | 0.95         | 27.39%       | 92.86%   | 11.70         |

> Produced with an earlier execution model, prior to the realistic slippage
> model and risk-based sizing. Treat as historical, not as current performance.
