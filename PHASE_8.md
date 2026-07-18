# Phase 8 — On-the-Fly Backtesting: Real Tickers, Custom Strategies

**Status:** In progress — 8.1–8.6 core implemented; deploy verification and follow-ups pending
**Prerequisites:** Phase 7 code complete (7.6 submission may proceed in parallel)

| Task | State | Notes |
|------|-------|-------|
| 8.1 On-demand OHLCV ingestion | ✅ Done | Stooq keyless CSV (ADR-045), cache-forever + daily re-fetch guard, `DATA_FETCH_DISABLED` kill switch, network-proof tests |
| 8.2 Strategy schema & rule engine | ✅ Done | C++ `RuleStrategy` (15 unit tests, suite 152/152), flat spec handoff (ADR-046); C++/Python indicator cross-validation fixture is follow-up |
| 8.3 Strategy-aware API | ✅ Done | `strategy` field (templates + custom rules), strategy-hashed cache keys, honest 422 (ADR-047), ML path experimental-flagged, model gate ML-only |
| 8.4 Extension builder UI | ✅ Done | Template picker + params, custom rule rows, saved strategies in `storage.sync`, user ticker input, experimental divider; 34 Node tests |
| 8.5 Speed budgets | ✅ Done | `pytest -m timing`: full-history engine run ≪ budget; runner overhead bounded. End-to-end cold-fetch budget verified at deploy time |
| 8.6 Transformer containment | ✅ Done | Torch-free `requirements-api.txt` + `Dockerfile.api` ships `backtest` only; `CMAKE_TARGET` build arg; ML scoreboard/experiments remain backlog |
**Ambition level:** High — turns the extension from a demo of one model into a
general-purpose "backtest what you're reading" tool

---

## Objective

The article is the entry point; the answer must be about **the stock the user is
actually reading about**, driven by **a strategy the user chose or built**, delivered
in **seconds**. Three shifts from the Phase 6/7 architecture follow:

1. **Real tickers, on demand.** Backtests fetch raw daily OHLCV for the article's
   ticker (or one the user types) instead of requiring pre-generated feature CSVs.
   Today `backtester/data/` covers one symbol; every other request silently substitutes
   AAPL. That substitution path ends for the product flow.

2. **User-defined strategies.** The C++ engine already has an abstract `Strategy`
   interface, a working `MovingAverageStrategy`, and a benchmark column. Phase 8
   exposes that: users pick a strategy template and set its parameters, or compose
   simple entry/exit rules, from the popup — and re-run instantly against the event
   window they're reading about. **Arbitrary user code is explicitly out of scope**
   (hosted code execution is a security non-starter); expressiveness comes from a
   bounded rule schema, not eval.

3. **The transformer becomes an experimental exhibit.** `ml_backtest`, the 22 MB model,
   and the 34-feature contract remain intact but move behind an "experimental" label,
   honest about their AAPL-only training data. **No feature-engineering work happens in
   the extension path** — the transparent strategies consume raw OHLCV only. The
   default hosted image drops LibTorch and the model artefact entirely, which also
   buys cold-start speed.

Portability is the through-line: the LibTorch-free engine — config-driven, fast,
dependency-light — is the shippable core for both the extension and standalone users.

---

## Task Breakdown

### 8.1 On-Demand OHLCV Ingestion

**New module:** `research/data/fetcher.py`

- Fetch daily OHLCV for `{ticker, date_start, date_end}` from a public provider.
  Provider choice (Stooq via pandas-datareader vs. yfinance) decided by a
  reliability/ToS spike and recorded as an ADR before implementation.
- Results cached as `backtester/data/ohlcv/{TICKER}.csv` — public market data, cached
  indefinitely with a volume size cap (distinct from the ADR-040 user-run TTL);
  incremental re-fetch only for missing date ranges.
- Hardening carried over from Phase 7: ticker regex already enforced at the boundary;
  fetch gets explicit timeout + retry cap; failures return a clear 422 ("no data
  available for TICKER"), **not** a silent substitute. ADR-028's fallback semantics are
  retained only for the experimental ML path where they originated; the transparent
  path prefers an honest error — recorded as a new ADR.
- Offline/dev mode: `DATA_FETCH_DISABLED=1` keeps tests and air-gapped runs off the
  network; all fetcher tests use fixtures.

### 8.2 Strategy Schema & Rule Engine (C++)

The engine work — all in the LibTorch-free build.

- **Parameterise existing templates:** `MovingAverageStrategy` windows, plus two new
  transparent templates: RSI mean-reversion and N-day breakout. Position sizing and
  stop-loss/take-profit as shared optional parameters (portfolio layer already does
  risk sizing).
- **`RuleStrategy`:** a bounded, composable rule evaluator —
  `entry`/`exit` each a list of conditions AND-ed together, a condition being
  `{indicator: SMA|EMA|RSI|PRICE|HIGH_N|LOW_N, period, op: <|>|crosses_above|crosses_below, value | other_indicator}`.
  Capped: ≤ 8 conditions per side, whitelisted indicators and operators only.
- **Strategy spec** is a JSON document; the binary accepts a spec file path alongside
  the existing config. One canonical schema shared by API validation and the engine
  parser (extend the existing no-dependency JSON approach from ADR-026).
- **Invariant note (ADR):** `technicalIndicators.py` remains the single source of the
  *ML feature* indicator logic. Strategy-level indicators computed inside the engine
  (as `MovingAverageStrategy` already does) are a separate concern; the ADR records
  this scope clarification plus cross-validation tests pinning C++ SMA/RSI values to
  the Python implementations on a shared fixture, so the two can never silently drift.
- Event-order invariant (`MARKET → SIGNAL → ORDER → FILL`) untouched; full unit
  coverage for `RuleStrategy` in the existing ctest suite.

### 8.3 API: Strategy-Aware Backtests

- `POST /api/backtest` gains an optional `strategy` field:
  `{ "template": "ma_cross", "params": {...} }` or `{ "rules": {...} }`; omitted →
  buy-and-hold vs. benchmark (the fastest honest default).
- Pydantic schema mirrors the C++ spec exactly (bounded lists, whitelisted enums,
  numeric ranges) — boundary validation per the house rule; spec size capped.
- Response echoes the resolved strategy, always includes the benchmark series, and
  keeps the Phase 7 shape otherwise (cache key extended with a strategy hash).
- The experimental ML path stays reachable via `{ "template": "ml_transformer" }`
  only where the server has the model + AAPL features; clearly labelled in the
  response (`"experimental": true`).

### 8.4 Extension: Editable Inputs & Strategy Builder

- **Editable context:** ticker and date range pre-filled from extraction but editable
  in the popup before running — the "user input to custom test a stock" path. Free-typed
  tickers validated client-side with the same regex.
- **Strategy panel:** template picker with parameter fields; a "custom rules" mode
  rendering condition rows (indicator / period / operator / value) mapped 1:1 to the
  RuleStrategy schema.
- **Saved strategies:** named strategy specs persisted in `chrome.storage.sync`
  (small JSON, well under quota), so a user's "my RSI dip-buyer" is one click on the
  next article.
- Results always render strategy-vs-benchmark; the ML option appears under an
  "Experimental" divider with an inline caveat.
- Node harness extended: schema round-trip, saved-strategy storage, input validation.

### 8.5 Speed Budget (measured, not aspirational)

The "faster the better" requirement gets numbers and a test:

| Path | Budget |
|------|--------|
| Cached ticker, any strategy | ≤ 3 s end-to-end |
| Cold ticker (network fetch) | ≤ 10 s end-to-end |
| Engine execution (daily bars, 1-year window) | ≤ 250 ms |
| Hosted cold start (no LibTorch image) | ≤ 5 s to healthy |

- LRU cache key becomes `(tickers, window, strategy_hash)`; warm-up pre-runs the
  curated events under the default strategy only.
- A timing harness in CI (marker-based, generous thresholds) catches regressions.

### 8.6 Transformer → Experimental Track (containment, not deletion)

- Default hosted image: no LibTorch, no `transformer.pt`, no scaler CSVs; the
  experimental image variant keeps them (compose profile / build arg).
- `ml_backtest`, the export pipeline, sweep, and walk-forward tooling remain for
  research use; CLAUDE.md updated to reflect the two-track split.
- Deferred until the experimental track earns attention: baseline scoreboard,
  model-shrinking experiments, multi-ticker training (the previous Phase 8 draft's
  ML deep-dive lives here as a backlog note, not a commitment).

---

## Exit Criteria

- [ ] Reading an article about any listed US ticker yields a backtest of **that
  ticker's real data**; no silent symbol substitution anywhere in the transparent path
- [ ] User can edit ticker/date in the popup and re-run on the same article
- [ ] User can configure a template strategy, compose a custom rule strategy, save it
  under a name, and run it on a fresh article in one click
- [ ] Strategy spec validated identically at the API boundary and in the engine;
  malformed specs fail with 422, never a 500
- [ ] Speed budgets in 8.5 met and enforced by a CI timing test
- [ ] Default hosted image contains no LibTorch and no model artefact; experimental
  image variant retains the ML path, labelled as such end-to-end
- [ ] C++ suite covers RuleStrategy (all operators, cross conditions, caps); Python
  coverage ≥ 80 %; extension Node tests cover the builder round-trip
- [ ] ADRs recorded: data provider, no-arbitrary-code decision, strategy-indicator
  scope vs. `technicalIndicators.py`, ADR-028 semantics split

---

## Open Questions / Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Data provider rate-limits or blocks server-side fetching | Medium | High | Provider ADR with fallback provider; indefinite OHLCV cache makes each ticker a one-time cost |
| Rule-engine scope creep ("add shorting! add options!") | High | Medium | v1 whitelist is fixed in the ADR; long-only + the listed indicators; extensions are future phases |
| Users expect to paste code as a "custom strategy" | Medium | Low | Explicit out-of-scope ADR; UI copy frames rules as the custom mechanism |
| C++/Python indicator drift (SMA/RSI computed in both) | Medium | Medium | Shared-fixture cross-validation tests pinning both implementations |
| Strategy spec becomes a de-facto API contract that's hard to change | Medium | Medium | Version field in the spec from day one |
| Curated event cards regress while transparent path lands | Low | Medium | Warm-up + quick-picks run the default strategy; ML card only in experimental builds |

---

## Definition of Done

A user reads a news story about any listed US stock, clicks the FAB, adjusts the
detected ticker or dates if they want, picks "MA crossover" — or their own saved
"RSI dip-buyer" built from rule rows — and within a few seconds sees how that strategy
performed against just holding the stock through the event window, on the stock's real
price history. The backtesting engine, not the model, is the product: small, fast,
portable, and honest. The transformer is still there for the curious — clearly marked
experimental, running only where its training data makes the answer meaningful.
