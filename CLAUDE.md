# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## What this project is

A contextual backtesting engine driven from the browser. The user reads a news
article, clicks the extension, and gets a backtest of a transparent rule
strategy over the window that article describes.

The flow is one line:

```
Chrome extension → FastAPI (research/api) → C++ backtester/backtest → equity curve
```

Everything else in the repo exists to serve that path, or is explicitly marked
legacy.

## Source of Truth Hierarchy

Before proposing any change, read in this order:
1. `DECISIONS.md` — architectural decisions already made; do not relitigate them
2. `ARCHITECTURE.md` — system design and component contracts

Phase files (`PHASE_1.md`–`PHASE_10.md`), `PDR.md`, and `LEARN.md` were removed
in the Phase 10 cleanup. They are recoverable from git history if a decision's
rationale is ever needed.

## Workflow Conventions

### During a session
- `DECISIONS.md` holds ten decisions, each one sentence of decision and one of
  rationale. It is a fixed-size document, not a log. Add to it only when a
  decision genuinely reshapes the system (why C++, how strategies are
  expressed) — and if you add one, say which of the ten it displaces.
  Implementation-level choices (library versions, file layout, build wiring)
  belong in the commit message, not here.

### Commits
- One logical change per commit
- Format: `type(scope): description` — e.g. `fix(extension): surface API timeout errors in popup UI`
- Types: `feat`, `fix`, `refactor`, `test`, `docs`, `chore`
- Run tests before committing; do not commit a red CI state

### Before writing any code
- State what you are going to change and why
- Identify which tests cover the affected code
- Flag any invariants at risk (see Key Invariants below)

---

## Commands

### Python

```bash
pytest tests/ -v --tb=short --cov=research --cov-report=term-missing --cov-fail-under=80
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
uvicorn research.api.app:app --port 8502 --reload
```

### C++ Backtester

```bash
cmake -S backtester -B build
cmake --build build --parallel
ctest --test-dir build --output-on-failure
./backtester/backtest <spec_json> <ohlcv_csv> <symbol>
```

### Deploy

```bash
./scripts/deploy_cloudrun.sh     # builds root Dockerfile, deploys to Cloud Run
./scripts/pack_extension.sh      # zips extension/ for Chrome Web Store
```

---

## Architecture

### FastAPI Bridge (`research/api/`)
Bridges Chrome extension → C++ binary. Runner prepends 60 lookback bars, writes
a temp CSV, invokes the binary from `PROJECT_ROOT`, caches up to 50 results
(LRU, pre-warmed at startup for all curated events).
Endpoints: `POST /api/context`, `POST /api/backtest`, `GET /api/events`, `GET /api/health`

Modules: `app.py` (routes), `auth.py` (API key), `cors.py`, `rate_limit.py`,
`runner.py` (binary invocation + cache), `schemas.py`, `strategies.py` (rule
spec resolution).

### Market data (`research/data/fetcher.py`)
On-demand OHLCV fetch. Disabled in tests via `DATA_FETCH_DISABLED=1`
(autouse fixture in `tests/conftest.py`).

### Context Extraction (`research/context/`)
Two-pass: rule-based (keyword match, 41 curated events, confidence score) →
Claude Haiku 4.5 fallback only when confidence < 0.6.

### Execution Layer (C++)
Event order is a hard invariant: `MARKET → SIGNAL → ORDER → FILL`. The queue
must fully drain before the next bar is fetched. Violating this causes
fill/signal race conditions.

Key components in `backtester/include/`: `market/`, `strategy/`, `portfolio/`
(risk sizing, correlation discount, benchmark), `execution/` (slippage model),
`config/` (YAML parser).

Two binaries, both tracked as pre-built — rebuild only when C++ strategy or
execution code changes:
- `backtester/backtest` — transparent rule strategies (RuleStrategy), no
  LibTorch. **This is the product path.**
- `backtester/ml_backtest` — experimental ML strategy (LibTorch +
  `models/transformer.pt`, AAPL-trained). Opt-in via
  `{"template": "ml_transformer"}`, flagged `experimental` end-to-end. Runs off
  the checked-in `backtester/data/AAPL_features.csv`.

### Chrome Extension (`extension/`)
Manifest V3. All API calls routed through the service worker
(`background.js`). FAB injected by `content.js`. Popup renders equity curve on
canvas.

---

## Legacy code

`research/transformer/` is the transformer training implementation, retained as
a record of how `models/transformer.pt` was built. It is **not** on the product
path, has no tests, and is omitted from coverage. Its dependencies (torch,
scikit-learn) live in `requirements-legacy.txt` and are not installed by CI.

Do not extend it, wire it into the API, or treat it as a maintained module. If
it needs to change, that is a signal the ML path is being revived — raise that
as a decision first.

The feature pipeline that fed it (`research/features/pipeline.py`), the Optuna
sweep, walk-forward validation, the Streamlit dashboard, and the training
corpora were removed in the Phase 10 cleanup.

---

## Key Invariants

**Feature column order is a hard contract** between
`backtester/data/AAPL_features.csv`, `backtester/include/strategy/MLStrategy.hpp`
(`MODEL_FEATURE_COLUMNS`), and `models/feature_scaler.csv`. A mismatch throws a
size-mismatch error at startup — explicit failure, not silent corruption.

**`research/features/technicalIndicators.py` is the reference oracle** for the
C++ indicator implementations. `tests/test_indicator_crossval.py` pins the two
against each other via `tests/fixtures/indicator_crossval.csv` (regenerate with
`scripts/gen_indicator_fixture.py`). Never reimplement indicators elsewhere.

---

## Test Coverage

`.coveragerc` omits legacy transformer code, the indicator oracle, and
`research/api/runner.py`. The 80% gate applies to the rest; current coverage is
~94%. C++ tests exclude LibTorch paths via missing `ML_STRATEGY_ENABLED`.

---

## Agents

Two reusable workflow agents live in `.claude/agents/`.

### `audit`
Pre-merge audit. Reads `DECISIONS.md` and `ARCHITECTURE.md`, then spawns three
parallel subagents (extension, API, context layers) to find silent failures,
race conditions, security gaps, and coverage holes. Produces a prioritised
P0/P1/P2 report. **Never writes code** — report must be signed off before any
fix work begins.

### `hardening`
Post-audit fix execution. Takes a signed-off audit report and executes all
findings using parallel worktree subagents (one per layer). Merges results,
resolves `DECISIONS.md` conflicts, adds regression tests, and generates a PR
description. Requires a green baseline before starting.

---

## Current State

Phases 1–10 complete. The repo was cleaned up in Phase 10: the research and
training layers were removed, leaving the browser-driven backtesting product
plus the legacy transformer reference.

Outstanding: Chrome Web Store submission (was 7.6).
