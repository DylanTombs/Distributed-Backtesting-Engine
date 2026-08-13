# Decisions

The ten decisions that shape this system. Settled — read before proposing
changes, and do not relitigate them.

Implementation-level ADRs (CMake wiring, header-only helpers, Docker mounts,
workflow layout) were retired in the Phase 10 cleanup and remain in git history.

---

**1 — Execution runs in C++, orchestration in Python.**
The backtest loop touches every bar of every symbol and its event ordering is a
hard invariant, so it gets a strongly-typed pipeline with no interpreter
overhead and no GIL; Python handles HTTP, text, and orchestration, and never
sits inside the inner loop.

**2 — The engine fetches the next bar only when the event queue is empty.**
The full `MARKET → SIGNAL → ORDER → FILL` chain for bar *t* must complete first,
or an EXIT on bar *t+1* observes a stale position.

**3 — Multi-asset data is synchronised by timestamp, not file order.**
The portfolio must see a consistent cross-sectional snapshot per iteration, or
correlation and exposure calculations silently misalign.

**4 — Simulation is pessimistic by default.**
Fills pay half-spread, market impact, and commission, positions are capped and
discounted by correlation, and every run is scored against buy-and-hold —
because a strategy that cannot beat buy-and-hold after friction is not a
strategy.

**5 — Context extraction is rules first, LLM only below 0.6 confidence.**
A curated event match is cheaper, faster, and more predictable than an LLM call,
so the common path stays free and spend stays bounded.

**6 — Custom strategies are a bounded rule schema, never code.**
Accepting code would make arbitrary execution the product's core feature; the
schema is the security boundary and the transparency guarantee at once.

**7 — The transparent path fails honestly.**
Ticker substitution and the model-availability gate are scoped to the
experimental ML path, so a product-path request that cannot be served as asked
errors rather than quietly backtesting a different symbol.

**8 — OHLCV is fetched on demand and cached, never vendored.**
Checking in a market-data corpus bloats the repo and goes stale, while a cache
keyed by symbol and window stays correct and costs nothing when idle.

**9 — Binary runs get per-run directories and a bounded semaphore.**
The binary is CPU-bound and process-isolated, so serialising every invocation
behind one global lock made concurrent requests queue for no reason.

**10 — One shared fixture pins the Python and C++ indicator implementations.**
They are independent code, so they are asserted equal rather than assumed
equal — silent indicator drift would change every signal downstream.

---

**Legacy:** the research layer (feature pipeline, Optuna sweep, walk-forward
validation, Streamlit dashboard, training corpora) was removed in the Phase 10
cleanup. `research/transformer/` is retained unmodified because
`models/transformer.pt` still ships and is still reachable via the opt-in
`ml_transformer` template — code that ships an artefact should not require
archaeology to understand.
