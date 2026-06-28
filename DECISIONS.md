# Decision Log

A record of key architectural and implementation decisions. Ordered by subsystem.

---

## System Architecture

### ADR-001: Separate research (Python) and execution (C++) layers

**Decision:** All model development, feature engineering, and training runs in Python. All backtest simulation runs in C++. The boundary is a CSV contract (feature files + scaler parameters) and a TorchScript model artefact.

**Rationale:** Keeping the two layers separate makes each independently testable and deployable. The Python layer benefits from the scientific Python ecosystem (pandas, sklearn, PyTorch). The C++ layer benefits from deterministic memory management, no GIL, and a build system that can produce a self-contained binary with no runtime dependencies other than LibTorch.

**Trade-offs:**
- Adds a serialisation step (export) that must be re-run whenever the model or feature set changes.
- The feature column order is a shared assumption maintained by convention (`exportModel.py::load_args()` is the canonical source). A schema registry would be more robust at scale.

---

## Feature Engineering

### ADR-002: Bar-by-bar adapter over batch pandas operations

**Decision:** `pipeline.py` wraps the DataFrame in a `_BarCtx` / `_DataView` / `_Line` adapter and computes indicators bar-by-bar rather than using vectorised pandas operations.

**Rationale:** The indicator functions in `technicalIndicators.py` are written for backtrader's API (`self.data.close[-1]`, etc.). Re-implementing them in pandas would create two code paths that could diverge silently. The adapter calls the existing functions unchanged, making training and inference feature parity a structural guarantee rather than a tested assumption.

**Trade-offs:**
- Slower than vectorised pandas (O(n) Python loop instead of C-accelerated array ops).
- For daily bar data over a few years per symbol, the performance difference is negligible.

---

## Model

### ADR-003: Encoder-decoder Transformer over LSTM

**Decision:** Use a custom encoder-decoder Transformer (`seqLen=30`, `predLen=5`, `encIn=34`) rather than an LSTM or simpler model.

**Rationale:** The multi-head attention mechanism can learn arbitrary long-range temporal dependencies without the vanishing gradient problem. For financial time series — where regime context from 20+ bars ago may be more relevant than recent noise — this is a meaningful advantage. The encoder-decoder structure naturally separates historical context from the prediction horizon.

**Trade-offs:**
- Requires `seqLen=30` bars before the first signal can be emitted. The first 30 bars of every backtest are skipped.
- Larger model (~23 MB) with more parameters. Needs more data and regularisation than an LSTM to avoid overfitting.

### ADR-004: `torch.jit.trace` over `torch.jit.script`

**Decision:** Export the model using `torch.jit.trace` with fixed dummy inputs of shape `(1, 30, 34)`.

**Rationale:** `trace` requires no source-level annotation and works for any model that has no data-dependent control flow. The model uses standard `nn.Module` composition — `trace` is sufficient and simpler.

**Trade-offs:**
- The traced graph is frozen to the input shapes used at trace time. Changing `seqLen` or `encIn` requires a re-export. This is acceptable because these are fixed hyperparameters.
- Dynamic batching is not possible with the traced model. Inference runs one sample at a time (`batch=1`), which is correct for a streaming bar-by-bar strategy.

---

## C++ Engine

### ADR-005: `FeatureMarketEvent` inherits `MarketEvent`

**Decision:** `FeatureMarketEvent` is a subclass of `MarketEvent` rather than a parallel event type.

**Rationale:** `BacktestEngine` dispatches on `EventType::MARKET` and upcasts to `MarketEvent`. A parallel type would require modifying the engine switch statement — a change unrelated to ML. Inheritance preserves the open/closed principle: the engine is unchanged, and `MLStrategy` recovers the feature payload with `dynamic_cast`. If the cast returns `nullptr` (plain `MarketEvent`), the strategy produces no signal — a safe, explicit failure mode.

**Trade-offs:**
- `dynamic_cast` at runtime has a small cost per bar.
- Any strategy that needs feature data must know to cast. Documented in `FeatureMarketEvent.hpp`.

### ADR-006: Header-only `ScalerParams`

**Decision:** `ScalerParams` is implemented entirely in `ScalerParams.hpp` with no corresponding `.cpp`.

**Rationale:** The struct has no mutable state, no virtual methods, and fits in ~60 lines. Header-only removes a compilation unit and allows the struct to be included by any target without linking.

**Trade-offs:**
- If the implementation grows (e.g. adding RobustScaler support), the header-only approach will increase binary size from inlining. At that point it should be split.

### ADR-007: `RiskManager` as a pre-trade gate

**Decision:** A `RiskManager` sits between `Portfolio::generateOrder` and `SimulatedExecution::executeOrder`. Orders are only executed if `approveOrder` returns true.

**Rationale:** Separating position-limit logic from portfolio accounting keeps `Portfolio` focused on state tracking. The risk manager can be extended to enforce drawdown limits, position concentration caps, or sector exposure rules without modifying the portfolio class.

**Trade-offs:**
- Currently `RiskManager` enforces only an absolute share cap (`maxPositionSize`). Percentage-of-equity exposure caps are enforced earlier in `Portfolio::generateOrder`. The split allows each concern to evolve independently.

### ADR-008: Engine fetches new bar only when event queue is empty

**Decision:** `BacktestEngine::run()` calls `dataHandler.streamNext()` only when the event queue is fully empty, not unconditionally at the top of each loop iteration.

**Rationale:** Without this guard, a MARKET event for bar *t+1* can arrive before the FILL from bar *t*'s BUY has been applied. A subsequent EXIT signal on bar *t+1* would then see a stale (zero) position and be silently suppressed — the position would never close. Draining the queue before fetching the next bar makes the MARKET→SIGNAL→ORDER→FILL chain atomic per bar.

**Trade-offs:**
- None. The previous behaviour was incorrect. The new behaviour is semantically equivalent for strategies that generate at most one signal per bar (true here).

### ADR-009: `MultiSymbolStrategy` as a routing facade

**Decision:** `MultiSymbolStrategy` holds a map of `symbol → unique_ptr<MLStrategy>` and dispatches each `MarketEvent` to the correct per-symbol strategy. It satisfies the `Strategy&` interface expected by `BacktestEngine`.

**Rationale:** `BacktestEngine` takes a single `Strategy&`. Supporting multiple symbols without changing the engine API requires a composite that routes internally. This keeps the engine unchanged and makes the multi-asset extension entirely additive.

**Trade-offs:**
- Each symbol's `MLStrategy` loads the same model file independently at startup. A shared `torch::jit::Module` reference would reduce memory if many symbols are used.

### ADR-010: `MultiAssetDataHandler` synchronises by timestamp

**Decision:** `MultiAssetDataHandler` pre-fetches one event per handler and, on each `streamNext()` call, emits all handlers whose buffered event shares the earliest timestamp.

**Rationale:** The portfolio must see all symbols at a given date as a single consistent snapshot, not interleaved across multiple ticks. Synchronising by timestamp also naturally handles symbols with different trading calendars — a symbol that has no bar on a given date simply does not emit.

**Trade-offs:**
- The synchronisation assumes timestamps are comparable strings (ISO 8601). Mixed timestamp formats across CSV files would cause incorrect ordering.
- A symbol that falls behind (e.g. due to data gaps) will not block progress — it emits its next available bar at its own timestamp.

---

## Portfolio

### ADR-011: Risk-based position sizing with exposure caps

**Decision:** Replace the previous hardcoded 10-share quantity with `floor(equity × riskFraction / price)`, capped by per-symbol and portfolio-wide exposure limits defined in `backtest_config.yaml`.

**Rationale:** Hardcoded share counts make capital allocation non-comparable across symbols at different price levels and across equity curve states. Fixed-fractional sizing is the industry-standard minimum for a meaningful backtest. Exposure caps prevent the strategy from concentrating capital in a single symbol or exceeding a configurable total invested fraction.

**Trade-offs:**
- `riskFraction` must be tuned. Too high and drawdowns are severe; too low and returns are negligible relative to transaction costs.

### ADR-012: Correlation-aware position sizing

**Decision:** Before finalising a new long position quantity, compute the rolling Pearson correlation between the new symbol's return series and each currently-held symbol. If `|ρ| > correlationThreshold`, discount the quantity: `qty *= (1 - |ρ| × 0.5)`.

**Rationale:** Holding two highly correlated instruments is economically equivalent to doubling a single position — it compounds drawdown without adding diversification. The discount is proportional to correlation strength and bounded at 50% to avoid fully suppressing valid signals.

**Trade-offs:**
- Requires at least `correlationWindow` bars of history to produce a meaningful estimate. New symbols with short histories receive no discount (correlation is undefined).
- Pearson correlation is sensitive to outliers. A more robust estimator (e.g. Spearman) could be used at the cost of additional implementation complexity.

### ADR-013: Slippage model: half-spread + linear impact

**Decision:** Fill price for BUY orders is `rawPrice × (1 + halfSpread + slippageFraction) + marketImpact × qty`. SELL orders use the opposite signs.

**Rationale:** A realistic execution model must account for the bid-ask spread (captured by `halfSpread`), additional market-order slippage (`slippageFraction`), and a size-proportional impact term (`marketImpact`). All three components are independently configurable — research workflows can set all to zero; production-calibrated runs use empirically estimated values.

**Trade-offs:**
- Linear market impact is a simplification. A square-root impact model is more commonly used for large orders, but is excessive for the order sizes in this system.
- All parameters are specified as fractions of price, not in absolute dollar terms, making them robust across different price levels.

### ADR-014: Equal-weight buy-and-hold benchmark

**Decision:** At the first bar each symbol appears, allocate `initialCash / nSymbols` to that symbol's benchmark position. Track the total benchmark equity at every subsequent bar.

**Rationale:** A meaningful benchmark must be defined before any trades occur to avoid look-ahead. Equal-weight allocation is the simplest defensible assumption when no prior information about symbol relative merit is available.

**Trade-offs:**
- If `nSymbols` changes mid-run (e.g. if a symbol's data starts later), the per-symbol allocation is based on the total count from config, not the number of symbols seen so far. This is a documented simplification.

---

## Performance Metrics

### ADR-015: Daily-return Sharpe with Bessel correction

**Decision:** Sharpe ratio is computed from daily portfolio returns using Bessel-corrected sample standard deviation, annualised by `sqrt(252)`.

**Rationale:** Per-trade returns are not a valid input for Sharpe — they conflate return magnitude with trade frequency and duration. Daily returns match the industry-standard definition (e.g. as used by Bloomberg, FactSet). Bessel correction (`n-1` denominator) avoids overstating precision on short histories. Annualisation factor of 252 matches US equity trading days.

**Trade-offs:**
- Annualisation assumes constant-variance returns, which financial returns do not satisfy. This is a universal simplification in the industry.
- Short backtests (< 60 days) produce Sharpe estimates with wide confidence intervals regardless of correction method.

### ADR-016: Information Ratio over active daily returns

**Decision:** IR = `mean(strategy_daily_return - benchmark_daily_return) / stddev(active_returns) × sqrt(252)`.

**Rationale:** IR measures excess return per unit of active risk — a more relevant metric than raw Sharpe for a strategy whose goal is to outperform a passive baseline. Using the benchmark embedded in each `EquityPoint` avoids the need to reconstruct benchmark returns separately.

**Trade-offs:**
- IR is only meaningful if the benchmark is sensible. An equal-weight buy-and-hold is a minimal baseline; a factor-adjusted benchmark would be more rigorous.

### ADR-017: Header-only `PerformanceMetrics`

**Decision:** `PerformanceMetrics` is implemented entirely in `PerformanceMetrics.hpp`.

**Rationale:** The struct performs a single stateless computation over a vector. Header-only removes a compilation unit and keeps all metric logic in one place, making it easy to add or modify formulas without updating a build system.

**Trade-offs:**
- If metric computation becomes expensive (e.g. bootstrap confidence intervals), it should be moved to a `.cpp` to avoid binary bloat.

---

## Testing

### ADR-018: `_IsolatedEvaluator` test double for `StrategyEvaluator`

**Decision:** `test_metrics.py` does not instantiate `StrategyEvaluator` directly. Instead, an `_IsolatedEvaluator` class is created and the metric methods from `StrategyEvaluator` are bound onto it.

**Rationale:** `StrategyEvaluator` inherits from `bt.Analyzer`. Backtrader's metaclass assigns `datas` and `_obj` attributes at instantiation that require a live `cerebro` environment. Instantiating the class in a test context throws immediately. The test double bypasses the metaclass by binding the methods onto a plain class that supplies the minimum attributes the methods actually access.

**Trade-offs:**
- The test double must be updated if `StrategyEvaluator` adds methods that access new backtrader-specific attributes.
- The technique is not obvious to a new contributor — it is documented in `tests/test_metrics.py`.

### ADR-019: Google Test via CMake `FetchContent`, disabled in Docker

**Decision:** GTest is fetched via `FetchContent_Declare` at configure time. The entire `FetchContent` block is wrapped in `if(BUILD_TESTING)`. Docker production builds pass `-DBUILD_TESTING=OFF` to skip the download.

**Rationale:** Requiring contributors to install GTest manually is a friction point. `FetchContent` downloads and builds GTest as part of the CMake configure step. However, Docker build containers have no internet access, and downloading during `docker build` would be fragile. `BUILD_TESTING=OFF` allows production image builds to skip GTest entirely without requiring a vendored copy.

**Trade-offs:**
- First local configure requires a network connection.
- Separating test-enabled and test-disabled builds means the Docker image is never test-validated directly — CI handles this in a separate job.

---

## CI/CD

### ADR-020: Three separate workflow files

**Decision:** Python tests, C++ build/test, and CodeQL are in separate workflow files rather than a single multi-job workflow.

**Rationale:** Separate files make individual workflow failures immediately identifiable by badge. A Python test failure does not block the C++ build result from being reported. Jobs within a single workflow have an implied dependency hierarchy that would require careful use of `needs:` to achieve the same isolation.

**Trade-offs:**
- Three files to maintain instead of one.
- No shared secrets or artefacts between workflows without using the GitHub Actions artefact store.

---

## Docker

### ADR-021: Multi-stage Docker build with `--platform=linux/amd64`

**Decision:** `Dockerfile.backtester` uses a two-stage build (builder + runtime). Both `FROM` statements are pinned to `--platform=linux/amd64`. The backtester service in `docker-compose.yml` also sets `platform: linux/amd64`.

**Rationale:** LibTorch CPU pre-built binaries are only available for x86_64. Apple Silicon (M-series) Mac Docker defaults to ARM64, which causes a linker error (`file in wrong format`) when trying to use the x86_64 LibTorch `.so` files. Pinning the platform ensures Rosetta 2 emulation is used transparently on Apple Silicon without requiring users to manually specify `--platform`.

**Trade-offs:**
- Rosetta 2 emulation adds build time overhead on Apple Silicon (~20–30% slower).
- The production image is x86_64 only, which matches typical cloud deployment targets (x86-based EC2, GKE nodes).

### ADR-022: Bind mounts over named volumes for local workflow

**Decision:** `docker-compose.yml` uses bind mounts (`./models:/models:ro`, `./backtester/data:/backtester/data:ro`, `./output:/output`) rather than named Docker volumes.

**Rationale:** Named volumes are opaque — files placed in `./models/` on the host are not visible to a named volume unless explicitly copied in. Bind mounts make the host filesystem directly available to the container, which matches the local development workflow where `transformer.pt` is exported to `./models/` and immediately consumed by the backtester.

**Trade-offs:**
- Bind mounts are host-path-dependent. The compose file assumes a specific directory layout relative to the project root.
- In a CI or remote environment, artefacts must be placed at the expected paths before `docker compose run` is called.

---

## Phase 4 — Strategy Extension

### ADR-023: Spearman over Pearson for correlation-based position sizing discount

**Decision:** Replace `pearsonCorr` with `spearmanCorr` in `Portfolio::correlationDiscount()`.

**Rationale:** Pearson correlation measures linear association and is sensitive to outliers. A single extreme-return day (earnings surprise, index rebalance, flash crash) can swing the Pearson estimate substantially, causing the position sizing discount to be applied too aggressively or not at all. Spearman rank correlation measures monotonic association on the rank-transformed series — the influence of any individual bar is bounded by its rank position, making the discount stable under fat-tailed return distributions. Implementation cost is minimal: Spearman reduces to Pearson on rank-transformed inputs, so `pearsonCorr` is reused rather than duplicated. Complexity is O(n log n) for the ranking step, identical in practice to the O(n) Pearson computation for window sizes used here (n ≤ 60).

**Trade-offs:**
- Backtest output values differ from pre-Phase-4 runs due to changed correlation estimates. The change is intentional and desirable — Pearson values were unreliable on this data.
- Spearman is slightly more expensive (rank sort), but negligible for a 60-element window.

### ADR-024: Path-keyed static model cache in MLStrategy

**Decision:** `MLStrategy` maintains a static `modelCache_` (`path → shared_ptr<torch::jit::Module>`). The first instance for a given path loads the model; all subsequent instances reuse the shared pointer.

**Rationale:** In a multi-symbol backtest, `MultiSymbolStrategy` creates one `MLStrategy` per symbol. Without the cache, `torch::jit::load()` is called N times for the same `.pt` file, loading N identical copies of the model into heap memory (~23 MB each). The cache reduces this to one load and one heap allocation regardless of symbol count. Thread safety is preserved: `torch::jit::Module::forward()` is safe for concurrent reads with `NoGradGuard`; each strategy owns its own input tensors.

**Trade-offs:**
- The static cache lives for the process lifetime. If a test creates strategies with different model paths, entries accumulate. Unit tests that need a clean state should not rely on the absence of cached models from prior test cases.
- If the `.pt` file changes on disk between runs (e.g., retrain during a long process), the cache will serve the stale model. This is acceptable: the binary is designed for a single-run workflow, not hot-reloading.

### ADR-025: Joblib over multiprocessing for parallel feature pipeline

**Decision:** `pipeline.py` uses `joblib.Parallel` + `delayed` for the `--workers` flag rather than `multiprocessing.Pool`.

**Rationale:** `joblib` is already in `requirements.txt` (added in Phase 3 for Optuna). `joblib.Parallel` handles edge cases better than raw `multiprocessing.Pool` for I/O-bound embarrassingly parallel workloads: it uses `loky` as the default backend (robust process reuse, cleaner shutdown), supports `n_jobs=-1` for all CPUs, and provides progress output. The sequential path (`n_jobs=1`) goes through the same code path, ensuring parity.

**Trade-offs:**
- Adds a `joblib` dependency for a relatively simple use case. Justified by the existing dependency and the reduction in boilerplate.
- `loky` spawns a new process pool per `Parallel(...)` call. For a CLI tool this is fine; for a long-running service it would be inefficient.

### ADR-030: Financial page detection via domain allowlist + ticker regex

**Decision:** `content.js` checks `isFinancialPage()` before injecting the FAB. Detection is: domain in a curated allowlist OR ≥2 ticker matches from a 30-ticker sample regex.

**Rationale:** Injecting on all pages creates noise and broadens the attack surface. The dual check (domain OR ticker count) catches well-known financial sites instantly and also works on non-listed sites discussing markets.

**Trade-offs:** The 30-ticker sample will miss pages about less common stocks. Users can still open the popup via the toolbar icon on any page — the FAB is just the shortcut. The domain list is a hardcoded constant; it could be made user-configurable in a future settings page.

---

### ADR-029: tabId passed in message payload for session caching

**Decision:** `popup.js` includes `tabId: currentTabId` in the `RUN_BACKTEST` message payload. `background.js` uses `msg.payload.tabId ?? sender.tab?.id` as the cache key.

**Rationale:** Messages from extension popup pages arrive with `sender.tab === undefined` because the popup is not a content script. Without an explicit tabId in the payload, the cache key was always undefined and no results were ever stored.

**Trade-offs:** The popup must correctly obtain `currentTabId` from `chrome.tabs.query` before sending. This is already done at init time.

---

### ADR-026: Regex-based JSON parser for feature schema (no nlohmann/json dependency)

**Decision:** `FeatureSchema::loadFromJSON()` extracts `"name"` values from the schema file using a single `std::regex` rather than adding a JSON library dependency.

**Rationale:** The schema file is a project artefact we own and control. Its structure is stable: a flat array of objects, each with a `"name"` string field. A line-by-line regex extractor (`"name":\s*"([^"]+)"`) is sufficient and avoids adding `nlohmann/json` (or a `FetchContent` download) to the build. Adding a JSON library would be the right call if the schema needed to carry machine-read data beyond feature names — for now it does not.

**Trade-offs:**
- The parser is brittle to non-standard JSON formatting (e.g., multi-line string values, escaped quotes in names). Acceptable given we generate the file ourselves.
- If the schema evolves to carry structured data (constraints, versioning rules), switch to a proper JSON library at that point.
