# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Source of Truth Hierarchy

Before proposing any change, read in this order:
1. `DECISIONS.md` — architectural decisions already made; do not relitigate them
2. `ARCHITECTURE.md` — system design and component contracts
3. The active phase file (currently `PHASE_6.md`) — what is in scope right now

## Workflow Conventions

### During a session
- When you make an architectural decision (library choice, interface design, tradeoff), append it to `DECISIONS.md` in the existing format before ending the task
- When a Phase 6 task is completed, mark it done in `PHASE_6.md` and note any follow-on work discovered
- Never modify a completed phase file (PHASE_1–5.md) without explicit instruction

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
pytest tests/test_dataset.py::test_windows_do_not_cross_ticker_boundaries -v
flake8 . --count --select=E9,F63,F7,F82 --show-source --statistics
streamlit run research/dashboard/app.py        # → http://localhost:8501
uvicorn research.api.app:app --port 8502 --reload
python run_pipeline.py --data-dir data/ --archive-run
python run_pipeline.py --data-dir data/ --skip-train
```

### C++ Backtester

```bash
cmake -S backtester -B build
cmake --build build --parallel
ctest --test-dir build --output-on-failure
cmake -S backtester -B build -DENABLE_COVERAGE=ON
./backtester/ml_backtest <feature_csv> <symbol> [model_pt] [feature_scaler_csv] [target_scaler_csv]
```

---

## Architecture

Two independent layers with a strict contract at their boundary.

### Research Layer (Python)
`research/features/pipeline.py` → 34-column feature CSVs  
`research/transformer/Interface.py` → training → `checkpoints/`  
`research/exportModel.py` → TorchScript export → `models/transformer.pt`  
`research/training/sweep.py` → Optuna TPE+Hyperband (SQLite-persistent)  
`research/validation/walk_forward.py` → expanding-window cross-validation  
`run_pipeline.py` → pipeline orchestrator  

### Execution Layer (C++)
Event order is a hard invariant: `MARKET → SIGNAL → ORDER → FILL`. The queue must fully drain before the next bar is fetched. Violating this causes fill/signal race conditions.

Key components in `backtester/include/`: `market/`, `strategy/` (MLStrategy buffers 30-bar window → LibTorch), `portfolio/` (risk sizing, correlation discount, benchmark), `execution/` (slippage model), `config/` (YAML parser).

`ml_backtest` compiles only when LibTorch is found. Tests run without it via missing `ML_STRATEGY_ENABLED`.

### FastAPI Bridge (`research/api/`)
Bridges Chrome extension → C++ binary. Runner prepends 60 lookback bars, writes temp CSV, invokes binary from `PROJECT_ROOT`, caches up to 50 results (LRU, pre-warmed at startup for all curated events).  
Endpoints: `POST /api/context`, `POST /api/backtest`, `GET /api/events`, `GET /api/health`

### Context Extraction (`research/context/`)
Two-pass: rule-based (keyword match, 41 curated events, confidence score) → Claude Haiku 4.5 fallback only when confidence < 0.6.

### Chrome Extension (`extension/`)
Manifest V3. All API calls routed through service worker (`background.js`). FAB injected by `content.js`. Popup renders equity curve on canvas.

---

## Key Invariants

**Feature column order is a hard contract** across:
- `research/features/pipeline.py` output columns
- `backtester/include/strategy/MLStrategy.hpp` (`MODEL_FEATURE_COLUMNS`)
- `scripts/convert_scalers.py` row order

A mismatch throws a size-mismatch error at startup — explicit failure, not silent corruption.

**`technicalIndicators.py` is the single source of indicator logic.** Never reimplement indicators elsewhere.

---

## Test Coverage

`.coveragerc` excludes training scripts, validation scripts, dashboard pages, and `research/api/runner.py`. 80% gate applies to the rest. C++ tests exclude LibTorch paths via missing `ML_STRATEGY_ENABLED`.

---

## Agents

Two reusable workflow agents live in `.claude/agents/`. Invoke them by
name at the start of phase closeout:

### `audit`
Pre-merge audit. Reads `DECISIONS.md`, `ARCHITECTURE.md`, and the active
phase file, then spawns three parallel subagents (extension, API, context
layers) to find silent failures, race conditions, security gaps, and
coverage holes. Produces a prioritised P0/P1/P2 report. **Never writes
code** — report must be signed off before any fix work begins.

Run at: start of every phase closeout, before opening a merge PR.

### `hardening`
Post-audit fix execution. Takes a signed-off audit report and executes
all findings using parallel worktree subagents (one per layer). Merges
results, resolves `DECISIONS.md` conflicts, adds regression tests, and
generates a PR description. Requires a green baseline before starting.

Run at: immediately after the user signs off on an audit report.

---

## Current State

Active branch: `feat/post-audit-hardening`  
Active phase: `PHASE_7.md` — Web Store submission and hosted deployment  
Phase 6: complete (browser extension + post-audit hardening)  
Phases 1–5: complete, do not modify their phase files  
Pre-compiled binary: `backtester/ml_backtest` — rebuild only when C++ strategy or execution code changes
