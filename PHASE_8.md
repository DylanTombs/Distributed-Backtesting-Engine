# Phase 8 — ML Effectiveness Deep Dive: Data Breadth & Model Rethink

**Status:** Planning
**Prerequisites:** Phase 7 code complete (may run in parallel with the procedural 7.6 submission)
**Ambition level:** High — the phase where the product's *answers* get good, not just its plumbing

---

## Objective

Phases 6–7 built and hardened the delivery pipeline: article → context → backtest →
results, hosted and installable. Phase 8 attacks the two things that limit how *useful*
those results are:

**Gap A — data breadth.** The extension scrapes and understands arbitrary articles, but
the backtest itself only runs on pre-generated feature CSVs. Today `backtester/data/`
holds features for **one symbol (AAPL)**. Read an NVDA earnings story and the backtest
quietly substitutes AAPL (ADR-028 warns, but a warning is not a result). 58 tickers of
raw OHLCV sit unused in `data/`, and nothing can fetch a ticker we've never stored.

**Gap B — model weight vs. evidence.** The current model is an encoder-decoder
transformer (`d_model=256`, 8 heads, 3 encoder + 2 decoder layers, seq_len=30) exported
as a 22 MB TorchScript artefact — Informer-style seq2seq machinery used to predict a
single next step. It has never been benchmarked against cheap baselines, and the time-
series literature (e.g. the DLinear line of work) repeatedly shows that for short-horizon
forecasting, simple models match or beat heavy transformers. On a shared-cpu-1x VM,
every unnecessary megabyte and matmul is latency and cold-start cost.

The phase principle: **measure first, then earn complexity.** No architecture change
ships unless it beats the baselines out-of-sample under the evaluation harness built in
8.1.

---

## Task Breakdown

### 8.1 Honest Evaluation Harness (gate for everything else)

Build the yardstick before touching the model.

- `research/evaluation/benchmark.py` — walk-forward evaluation (reusing
  `research/validation/walk_forward.py`) of any candidate over multiple tickers and
  regimes (trend, crash, chop — the curated events give ready-made windows).
- Baselines, all sharing the same 34-feature input where applicable:
  1. buy-and-hold
  2. MA-crossover momentum (no ML)
  3. ridge / logistic regression on the engineered features
  4. LightGBM on the engineered features (optional, if the dep is acceptable)
  5. the current transformer (frozen artefact)
- Metrics per window: directional accuracy, strategy Sharpe (via the C++ engine for
  final candidates; a vectorised Python approximation for cheap iteration), max
  drawdown, turnover, and **inference ms/window on CPU**.
- Output: a scoreboard CSV + a short markdown report checked into `research/evaluation/`.

**Deliverable:** a table answering "does the transformer currently earn its 22 MB?" —
whatever the answer is.

### 8.2 Feature Coverage for the Existing Universe (cheap, immediate)

The stopgap that fixes most real extension sessions without new ML:

- Run the feature pipeline across all 58 tickers already in `data/`, populating
  `backtester/data/*_features.csv` (single source of indicator truth:
  `technicalIndicators.py` — unchanged).
- Add the generated set to the Docker image / Fly volume story (they are inputs, like
  the model).
- Cross-check: every curated event's primary ticker that is still listed has a feature
  CSV; extend `test_context_ticker_hygiene.py` to assert it.

**Deliverable:** curated Quick-Pick events stop falling back to AAPL.

### 8.3 On-Demand Data Ingestion (arbitrary tickers)

Close Gap A for tickers we've never seen:

- `research/data/fetcher.py` — fetch daily OHLCV for a requested ticker/date range
  (primary: Stooq via pandas-datareader or yfinance; pick after a reliability/ToS
  check — record as an ADR), then run the existing feature pipeline and cache the
  result into `backtester/data/`.
- Runner order becomes: exact CSV → **fetch-and-build** → ADR-028 fallback (now rare).
- Constraints carried over from the hardening work: ticker regex already enforced at
  the boundary; fetch gets a timeout + retry cap; per-run semaphore already bounds
  concurrency; failures degrade to the existing fallback with the existing warning.
- Cache policy: fetched features persist (they are reusable public data, not user
  data — distinct from the ADR-040 run-archive TTL); a size cap keeps the Fly volume
  bounded. Offline/dev mode: fetcher disabled via env, tests never hit the network.

**Deliverable:** an article about any listed US ticker produces a backtest of *that
ticker* within the 15 s budget.

### 8.4 Model Rethink Experiments

The deep dive proper. Candidates, cheapest first, all judged by the 8.1 harness:

1. **Target/loss engineering on the current architecture** — predict next-day *return*
   (stationary) rather than price level; try classification (direction) with a
   probability threshold vs. regression; this alone often dominates architecture
   changes.
2. **Shrink the transformer** — encoder-only + direct regression head (drop the decoder
   and label_len machinery entirely), `d_model` 64–128, 1–2 layers. Expected artefact:
   ≤ 2–3 MB, ~10× less compute for the same seq_len=30 window.
3. **Linear-family challengers** — DLinear-style (per-feature linear over the window)
   and the 8.1 ridge baseline promoted to a real candidate.
4. **Gradient boosting** — LightGBM over window-aggregated features (no sequence model
   at all). Strong tabular prior; trivially CPU-cheap.
5. **Global multi-ticker training** — train one model across the 58-ticker universe
   (ticker embedding or none) vs. today's per-ticker fit; more data per parameter is
   the classic cure for overfit heavy models.

Selection criterion (write into the report): best out-of-sample walk-forward Sharpe
net of the existing slippage/commission model, **per unit of CPU inference cost**, with
a hard budget: artefact ≤ 5 MB, ≤ 50 ms per 30-bar window on shared-cpu-1x. If a
simpler model ties the transformer, the simpler model wins — that is a success, not a
failure, and gets recorded as an ADR.

### 8.5 Retrain, Export, and Contract Preservation

- Winner is tuned with the existing Optuna sweep (`research/training/sweep.py`) and
  validated with `walk_forward.py`; final artefact exported via `exportModel.py`.
- **Hard invariants respected:** the 34-column feature order contract
  (`pipeline.py` ↔ `MODEL_FEATURE_COLUMNS` ↔ `convert_scalers.py`) is unchanged unless
  the experiments justify a feature change — in which case all three touch points move
  in one commit and the C++ binary is rebuilt.
- If the winner is non-torch (LightGBM), export via ONNX → a small inference shim, or
  reconsider scope: keeping TorchScript-compatible candidates avoids C++ churn; decide
  by ADR when the scoreboard is in.
- `GET /api/health` gains a `model_version` field so deployed model provenance is
  visible.

### 8.6 Productise the Improvement

- Extension: surface which symbol/data actually backed the result (the ADR-028 warning
  already exists; promote it from small-print to a visible chip when a fallback
  happened — it should now be rare).
- Update README model section; retire stale claims; publish the 8.1 scoreboard in the
  repo.

---

## Exit Criteria

- [ ] 8.1 scoreboard exists, reproducible via one command, covering ≥ 10 tickers and
  ≥ 3 market regimes
- [ ] Every still-listed curated-event primary ticker backtests on its own data
  (no AAPL fallback), enforced by test
- [ ] An article about an arbitrary S&P 500 ticker not previously on disk completes a
  real-data backtest end-to-end within 15 s (cold fetch) / 5 s (cached)
- [ ] Selected model beats or ties every baseline's out-of-sample Sharpe on the
  majority of walk-forward windows, net of costs — with the decision recorded as an ADR
- [ ] Model artefact ≤ 5 MB and ≤ 50 ms CPU inference per window (measured in 8.1
  harness), or an explicit ADR accepting why not
- [ ] Feature-column contract intact or migrated atomically with a rebuilt binary and
  green C++ tests
- [ ] Python coverage stays ≥ 80 %; fetcher fully tested offline via fixtures

---

## Open Questions / Risks

| Risk | Likelihood | Impact | Mitigation |
|------|-----------|--------|------------|
| Data provider (yfinance/Stooq) rate-limits or breaks ToS for server-side use | Medium | High | Provider decision ADR up front; cache aggressively; Stooq/pandas-datareader fallback; degrade to ADR-028 path |
| One-step-ahead daily returns are ~noise; nothing beats buy-and-hold net of costs | Medium | Medium | That result is itself shippable: pick the cheapest model, publish honest numbers — credibility is the product |
| Lookahead leakage lurking in engineered features inflates all ML results | Medium | High | 8.1 includes a leakage audit of `technicalIndicators.py` (shift checks) before any conclusions |
| Winner requires C++ contract changes (seq_len, features) | Low | Medium | Budgeted in 8.5; MLStrategy buffer + binary rebuild in the same PR |
| Global multi-ticker model degrades AAPL-class liquid names | Low | Low | Scoreboard is per-ticker; keep per-ticker fine-tune as a variant |

---

## Definition of Done

A user reads about any listed US stock, clicks the FAB, and gets a backtest of **that
stock's actual data**, produced by a model whose out-of-sample edge over trivial
baselines is documented in the repo — or, if no edge exists, by the cheapest model that
ties, stated honestly. The 22 MB transformer either earns its place with evidence or is
replaced by something smaller that does.
