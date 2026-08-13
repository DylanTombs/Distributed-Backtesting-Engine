# Architecture

Three layers, with a contract at each boundary.

```
Chrome extension  (any financial news page)
      │  article text
      ▼
Bridge  (Python — research/)        context extraction · OHLCV fetch · spec resolution
      │  spec file + OHLCV CSV
      ▼
Engine  (C++ — backtester/)         event-driven simulation
      │
      ▼
Equity curve + metrics → popup
```

---

## Client — `extension/`

Manifest V3. `content.js` injects the floating button and reads page text.
`background.js` is the service worker and the only component holding host
permissions — all API calls route through it. `popup/` renders the equity curve
on canvas with metric cards.

## Bridge — `research/`

| Module | Responsibility |
|---|---|
| `context/scraper.py` | Article body extraction (trafilatura, httpx fallback) |
| `context/events.py` | 41 curated events with canonical date windows |
| `context/entities.py` | Ticker regex + `dateparser` date extraction |
| `context/extractor.py` | Two-pass: rule match, then Claude Haiku below 0.6 confidence |
| `data/fetcher.py` | On-demand OHLCV; disabled in tests via `DATA_FETCH_DISABLED` |
| `api/strategies.py` | Validates a request into a rule spec; writes the spec file |
| `api/runner.py` | Invokes the binary; parses results; 50-entry LRU cache |
| `api/app.py` | `/api/context` `/api/backtest` `/api/events` `/api/health` |
| `api/auth.py` `rate_limit.py` `cors.py` | API key, per-route limits, `chrome-extension://` CORS |

`runner.py` prepends 60 lookback bars ahead of the requested window so
indicators are warm at the first scored bar, writes a temp CSV under a per-run
directory, and invokes the binary from `PROJECT_ROOT` under a bounded semaphore
and a timeout. The cache is pre-warmed at startup for every curated event.

## Engine — `backtester/`

| Component | Responsibility |
|---|---|
| `BacktestConfig` | YAML parser; single source of truth for execution parameters |
| `MultiAssetDataHandler` | Synchronises N handlers by timestamp; emits all same-date events per tick |
| `RuleStrategy` | Evaluates transparent indicator conditions; emits `SignalEvent`. **Product path.** |
| `MLStrategy` | *Legacy.* 30-bar window → LibTorch inference → `SignalEvent` |
| `BacktestEngine` | Dispatches `MARKET → SIGNAL → ORDER → FILL` |
| `Portfolio` | Risk sizing, exposure caps, correlation discount, benchmark, equity curve |
| `RiskManager` | Pre-trade gate enforcing absolute and relative position limits |
| `SimulatedExecution` | Orders → fills; half-spread, slippage, market impact, commission |
| `PerformanceMetrics` | Sharpe, Information Ratio, max drawdown, alpha |

Two binaries, both tracked pre-built: `backtest` (rules, no LibTorch — the
product) and `ml_backtest` (legacy, opt-in via `template: "ml_transformer"`).

---

## Event loop

```
if (queue.empty()) dataHandler.streamNext(queue);   // only when fully drained
if (queue.empty()) break;                           // data exhausted

MARKET → Portfolio::updateMarket()  ·  Strategy::onMarketEvent() → SignalEvent
SIGNAL → Portfolio::generateOrder()                             → OrderEvent
         LONG: qty = floor(equity × riskFraction / price)
               capped by symbol + total exposure, discounted by correlation
         EXIT: qty = full held position
ORDER  → RiskManager::approveOrder()  ·  SimulatedExecution::executeOrder()
         fill = price × (1 ± halfSpread ± slippage) ± impact × qty
                                                                → FillEvent
FILL   → Portfolio::updateFill()      // cash, positions, trade log
```

Fetching only on a drained queue is the engine's hardest invariant: it
guarantees bar *t* fully resolves before bar *t+1* arrives, so an EXIT never
observes a position whose BUY fill is still queued.

## Portfolio model

Sizing is `floor(equity × riskFraction / price)`, then capped by
`maxSymbolExposure` and `maxTotalExposure`. Before a LONG finalises, rolling
Spearman correlation against each held symbol applies a discount of up to 50%
where `|ρ| > threshold`, using the minimum across correlated symbols. Each
symbol's first bar allocates `initialCash / nSymbols` to a buy-and-hold
benchmark tracked alongside strategy equity.

Metrics use Bessel-corrected daily returns — the `n-1` denominator avoids
overstating precision on the short windows event-driven backtests produce.

Short selling is version-gated behind spec version 2. Latency is zero: signal
and fill occur on the same bar, standard for EOD simulation.

---

## Invariants and edge cases

**Indicator parity.** The Python oracle
(`research/features/technicalIndicators.py`) and the C++ implementations are
independent code, pinned against `tests/fixtures/indicator_crossval.csv` by
`tests/test_indicator_crossval.py`. Regenerate the fixture with
`scripts/gen_indicator_fixture.py`. Never reimplement indicators elsewhere.

**Feature column order (legacy ML path).** `backtester/data/AAPL_features.csv`,
`MODEL_FEATURE_COLUMNS` in `MLStrategy.hpp`, and `models/feature_scaler.csv`
must agree on all 34 columns. A mismatch throws at startup — explicit failure,
not silent corruption.

**Missing bars.** Data handlers emit one event per non-empty row and do not
detect date gaps; the 60-bar lookback prefix ensures indicators are warm
regardless.

**Determinism.** The rule path has no stochastic component — same OHLCV plus
same spec yields the same result. `transformer.pt` is a fixed traced artefact,
so the legacy path is deterministic too. The only run-to-run variation is the
upstream fetch, which is why results are cached per event and strategy.

---

## Scaling

| Concern | Current state | Path forward |
|---|---|---|
| Multi-symbol | N symbols in one process via `MultiAssetDataHandler` | Shard across processes; aggregate fills via a queue |
| Concurrency | Bounded semaphore caps parallel runs to available cores | Move to a work queue if request volume outgrows one instance |
| CSV in memory | Streams row-by-row but loads the full file at construction | Memory-mapped reads or a DuckDB backend |
| OHLCV fetch | Serial per request, cached only in the runner's result LRU | Durable bar cache keyed by symbol and window |
