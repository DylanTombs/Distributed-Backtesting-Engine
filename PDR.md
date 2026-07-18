# Product Development Roadmap (PDR)

**Project:** TradingTransformer  
**Last Updated:** 2026-07-19  
**Status:** Pre-Submission — Launch Readiness Review  
**Author:** Dylan Tombs

---

## Table of Contents

1. [Executive Summary](#1-executive-summary)
2. [What Shipped](#2-what-shipped)
3. [Goals & Non-Goals (Final)](#3-goals--non-goals-final)
4. [Success Metrics — Achieved](#4-success-metrics--achieved)
5. [Launch-Readiness Gates](#5-launch-readiness-gates)
6. [Known Issues Register](#6-known-issues-register)
7. [Technical Debt Inventory](#7-technical-debt-inventory)
8. [Risk Register](#8-risk-register)
9. [Deferred Items (Phase 11 Candidates)](#9-deferred-items-phase-11-candidates)

---

## 1. Executive Summary

TradingTransformer set out (PDR of 2026-04-01) as an ML backtesting system whose
reported results could not be trusted: data leakage, no out-of-sample validation,
untested execution components, and no observability. Nine phases later, every item
in that problem statement is resolved — and the product itself pivoted on evidence.

**What it is now:** a Chrome extension that reads a financial news article, detects
the market event and ticker, and backtests a user-chosen strategy — template or
custom-built rules, long or short — against that stock's real price history, in
seconds. The backtesting engine, not the model, is the product: a portable,
LibTorch-free C++ binary driven by a bounded, versioned strategy spec. The
transformer that named the project remains available as an explicitly experimental,
opt-in strategy, honest about its AAPL-only training data.

**The system today:** ~18k LOC across C++17, Python, and JavaScript; 644 automated
tests (160 C++, 447 Python at 93.5% coverage, 37 Node); 49 ADRs across 10 phase
documents; a security posture built by a structured self-audit (22 findings, all
fixed with regression tests) before any public exposure.

This document is the review gate for the final step: packaging the extension and
submitting to the Chrome Web Store (Phase 10.4).

---

## 2. What Shipped

### Execution layer (C++17)
- Event-driven engine — hard invariant `MARKET → SIGNAL → ORDER → FILL`, queue
  drains fully per bar (kills lookahead by construction)
- Realistic execution: half-spread, slippage, market impact, commission; risk-based
  sizing with correlation discount; per-symbol buy-and-hold benchmark
- **Two tracked binaries:** `backtest` (RuleStrategy, no LibTorch — the product
  path) and `ml_backtest` (transformer inference — experimental)
- `RuleStrategy`: whitelisted indicators (PRICE/SMA/EMA/RSI/HIGH_N/LOW_N), four
  operators, ≤8 AND-ed conditions per side, long **and short** (spec v2,
  version-gated so old consumers fail loudly rather than mis-run direction)
- Indicator math extracted to pure functions, pinned by a shared cross-validation
  fixture asserted by both the C++ and Python suites (ADR-048)
- 2,845 daily bars (25 years) processed in ~30 ms; CI-enforced speed budgets

### API layer (FastAPI)
- `POST /api/context` — two-pass extraction: rule-match over 41 curated events,
  Claude Haiku fallback only below confidence 0.6, prompt-injection hardened,
  LLM output validated as untrusted input
- `POST /api/backtest` — strategy-aware (templates + custom rules), per-request
  binary isolation (temp-dir CWD, UUID run-ids, bounded semaphore), strategy-hashed
  LRU cache, honest 422 when data is unavailable (no silent symbol substitution)
- On-demand OHLCV via Stooq's keyless CSV endpoint; cache-forever with atomic
  writes and a `DATA_FETCH_DISABLED` kill switch (test suite is network-proof)
- Hardening: API-key auth (comma-separated env, empty = dev mode), per-IP rate
  limits (10/min backtest, 30/min context), request-size caps, SSRF
  resolve-and-deny, error taxonomy (client 422 / sanitised 500), 7-day run-archive
  TTL enforced in code, thread-safe caching

### Extension (Manifest V3, v1.0.0)
- All network I/O isolated in the service worker; `X-API-Key` attached from
  `chrome.storage.sync` settings (validated URLs, https required for remote hosts)
- Popup: event auto-detection with editable ticker/date inputs, strategy panel
  (template picker, custom rule builder with Long/Short direction, named saved
  strategies), canvas equity chart, experimental divider for the ML option
- Settings page with Test Connection; packaging script verified (manifest at zip
  root, store assets excluded); store listing copy + privacy policy written

### Deployment
- `Dockerfile.api`: torch-free image (GBs smaller) bundling the `backtest` binary
- `fly.toml` + `deploy_api.sh` (binary pinned to deploy SHA); compose `hosted`
  profile runs the exact production image; OHLCV cache on persistent volume

### Resolution of the 2026-04-01 problem statement
| Original issue | Resolution |
|---|---|
| Ticker-boundary data leakage (`xfail`) | Fixed; windows cannot cross tickers (tested) |
| No out-of-sample validation | Expanding-window walk-forward + Optuna sweeps (Phase 5) |
| Untested slippage/metrics/CSV handlers | Dedicated suites; 160 C++ tests |
| `SignalType::SHORT` unimplemented | Full short path: portfolio (Phase 4) → spec v2 → UI (Phase 9) |
| Hardcoded thresholds, no config validation | YAML config with `validate()`; strategy spec versioned |
| `std::cout` logging, no tearsheet | spdlog structured logging; Streamlit dashboard tearsheets |
| Results table produced pre-slippage | README rewritten; stale claims retired |

---

## 3. Goals & Non-Goals (Final)

**Goals (held):** portable transparent engine as the shippable core; sub-15s
article-to-result; user-defined strategies without code execution; honest results
(real ticker data or explicit failure); a system one operator can run and audit.

**Non-goals (held):** live trading or brokerage integration; investment advice;
arbitrary user code; multi-position/mixed-direction books (v3 candidate); beating
the market with the experimental model — its claims are deliberately scoped to
"experimental, AAPL-trained."

---

## 4. Success Metrics — Achieved

| Metric | Target | Actual |
|---|---|---|
| Python coverage | ≥ 80% (CI gate) | 93.5% (447 tests) |
| C++ suite | green | 160/160 |
| Extension tests | exist | 37 (zero-dependency node:vm harness) |
| Engine latency | ≤ 250 ms / 1-yr window | ~30 ms / 25-yr history |
| E2E latency | ≤ 3 s cached / ≤ 10 s cold | met locally; cold-fetch verified at deploy (10.1) |
| Security audit | all P0/P1 fixed pre-launch | 2 P0 + 11 P1 + 9 P2 fixed, regression-tested |
| Decisions documented | — | 49 ADRs, 10 phase files |

---

## 5. Launch-Readiness Gates

Ordered; each gates the next. This PDR is the final commit before Gate 1.

1. **Merge the PR chain** — #21 (phase-7 → main), #22 (phase-8), #23 (phase-9).
   Retarget downstream PRs onto main after each merge.
2. **Deploy the hosted API** (Phase 10.1) — Fly app + volume + secrets, then the
   five-item verification checklist in PHASE_10.md (cold start < 5 s, cold fetch
   < 10 s, volume cache survives redeploy, sanitised wrong-arch failure, remote
   rate-limit/401 behaviour).
3. **Package the extension** — `scripts/pack_extension.sh` from the merged main;
   load the zip in a clean Chrome profile; capture the four 1280×800 screenshots
   against the live hosted API (checklist: `extension/store/screenshots/README.md`).
4. **Host the privacy policy** at a stable public URL.
5. **Submit to CWS** (Phase 10.4) — listing copy from `extension/store/`;
   rejection playbook prepared (PHASE_7.md 7.6: `<all_urls>` justification,
   no-remote-code, privacy URL).
6. **Post-approval:** record the extension ID → set `ALLOWED_EXTENSION_IDS`
   (ADR-043) → README install badge.

---

## 6. Known Issues Register

| # | Issue | Severity | Disposition |
|---|---|---|---|
| 1 | Single OHLCV provider (Stooq); coverage gaps for some delisted/OTC symbols | Medium | Honest 422 by design (ADR-047); second provider is a Phase 11 candidate |
| 2 | Experimental model never benchmarked against baselines | Medium | Contained: opt-in, `experimental: true`, excluded from default image; scoreboard backlogged |
| 3 | Hosted OHLCV cache path shares the model volume (Fly one-volume limit) | Low | Documented in fly.toml; acceptable at current scale |
| 4 | Fixture generator shares reference formulas with the Python test (guards drift, not primordial correctness) | Low | Formulas ~5 lines each, reviewable; documented in ADR-048 |
| 5 | Templates are long-only; short requires the custom builder | Low | Deliberate (ADR-049) — template names encode direction semantics |
| 6 | Observability minimal until Phase 10.2 (structured logs, status endpoint, spend counter) | Medium | Scheduled as the code component of Phase 10 |

---

## 7. Technical Debt Inventory

- **Rules DSL:** AND-only, single position, no stops — spec `version` field is the
  migration path (v3 scoped in PHASE_9/PHASE_10 deferred lists)
- **Extension:** `getSettings()` duplicated between background.js and settings.js;
  popup.js has grown — candidate for module split if the builder expands
- **Engine/Python duplication:** `warmupBars()` logic mirrored in
  `ResolvedStrategy.warmup_bars` — cross-checked by tests, but a shared derivation
  would be cleaner
- **Curated events:** ticker hygiene is test-enforced against a curated denylist,
  not provider-verified; scheduled validation is a Phase 11 idea
- **Docs:** PHASE_1–6 frozen by convention; the 2026-04-01 PDR is superseded by
  this document (history preserved in git)

---

## 8. Risk Register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| CWS rejects `<all_urls>` or review cycles drag | Medium | High | Prepared justification; `optional_host_permissions` fallback |
| Stooq blocks/limits the hosted egress IP | Medium | Medium | Cache-forever = one fetch per ticker; pre-warm via volume; provider fallback ADR |
| Anthropic spend under real traffic | Low | Medium | Rate limits + 40-char floor + 1500-char cap now; `LLM_DAILY_BUDGET` counter in 10.2 |
| Fly shared-cpu-1x undersized | Low | Low | Semaphore degrades to queueing; VM scale-up is a config change |
| Solo operator | Certain | Medium | Phase 10.2/10.3 exist precisely for 15-minute diagnosis; runbook in `docs/operations.md` |

---

## 9. Deferred Items (Phase 11 Candidates)

Triage **after** launch, against real usage (per PHASE_10.md):

1. Rules v3 — OR-groups, stop-loss/take-profit exits, volume/ATR indicators
2. Side-by-side strategy comparison in the popup
3. ML evaluation scoreboard (the original Phase 8 draft's harness)
4. Second OHLCV provider behind the fetcher interface
5. Scheduled provider-verification of curated event tickers

---

*This PDR supersedes the 2026-04-01 roadmap. The system it described — untrusted
results, untested execution, no observability — no longer exists. What remains
between this commit and public users is procedural: merge, deploy, package, submit.*
