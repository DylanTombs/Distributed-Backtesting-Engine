# Phase 5 — Interactive Research Dashboard

**Status:** Complete  
**Prerequisites:** Phase 1 (validated metrics), Phase 3 (tearsheet + CSV outputs)  
**Can run in parallel with:** Phase 4  

---

## Objective

Replace the static HTML tearsheet with a live, interactive research dashboard that lets you trigger backtests, compare runs, browse walk-forward results, and inspect Optuna sweep trials — all without touching the command line. The dashboard is a read/write UI layer on top of the existing artifacts; the C++ engine and Python pipeline remain unchanged.

**Why Phase 1 and 3 must precede Phase 5:** The dashboard reads `ml_equity.csv`, `ml_trades.csv`, `ml_metrics.csv`, and the tearsheet HTML produced by Phases 1–3. Building a UI before those outputs are trustworthy and consistently formatted would mean building on a moving target.

**Exit criteria (all must be satisfied before Phase 5 is closed):**
- [x] Dashboard serves on `http://localhost:8501` with a single `streamlit run` command
- [x] Run browser: lists all past backtest runs (by timestamp), shows summary metrics in a table
- [x] Run comparison: select 2–4 runs, overlay equity curves and metric deltas side-by-side
- [x] Config editor: edit `backtest_config.yaml` fields through form inputs; validates before saving
- [x] Trigger panel: launch `run_pipeline.py` as a subprocess; stream stdout live into the UI
- [x] Walk-forward panel: visualise in-sample vs out-of-sample Sharpe per fold from `wf_report.py` output
- [x] Sweep panel: load `models/best_config.yaml` and show trial history from Optuna storage
- [x] All dashboard logic is covered by unit tests (mock filesystem, not real backtests)
- [x] Docker Compose wires the dashboard as a third service alongside `python` and `backtester`

---

## Task Breakdown

### 5.1 Technology Choice & Project Structure

**Stack:** Streamlit (pure Python, zero JS build step, native pandas/plotly integration)  
**Backend:** No separate API server — Streamlit runs in-process and reads artifacts directly from the shared `output/` and `models/` directories  
**State:** `st.session_state` for UI state; no database required

**New directory layout:**
```
research/
  dashboard/
    __init__.py
    app.py              ← Streamlit entry point
    pages/
      1_Run_Browser.py
      2_Run_Comparison.py
      3_Config_Editor.py
      4_Trigger_Backtest.py
      5_Walk_Forward.py
      6_Sweep_Results.py
    components/
      equity_chart.py   ← reusable Plotly equity curve component
      metrics_table.py  ← reusable summary stats table
      run_selector.py   ← run-picker widget (sidebar)
    io/
      run_store.py      ← reads/writes run artifacts from output/runs/<timestamp>/
      config_io.py      ← reads/writes backtest_config.yaml
      sweep_io.py       ← reads Optuna storage or best_config.yaml
```

**Run artifact layout (new convention introduced in Phase 5):**
```
output/
  runs/
    20260401_143022/
      ml_equity.csv
      ml_trades.csv
      ml_metrics.csv
      tearsheet.html
      backtest_config.yaml   ← snapshot of config used for this run
    20260402_091155/
      ...
```

`run_pipeline.py` is updated to move outputs into a timestamped subdirectory after each run (opt-in via `--archive-run`; defaults to writing flat into `output/` as before for backward compatibility).

---

### 5.2 Run Browser (Page 1)

**File:** `research/dashboard/pages/1_Run_Browser.py`

Displays a sortable table of all past runs:

| Timestamp | Symbols | Sharpe | Max DD | Total Return | Alpha | Days |
|-----------|---------|--------|--------|--------------|-------|------|
| 2026-04-01 14:30 | AAPL, MSFT | 1.23 | 5.6% | 18.5% | 3.2% | 252 |
| 2026-04-02 09:11 | AAPL | 0.87 | 8.1% | 12.3% | 1.1% | 252 |

Clicking a row opens the tearsheet HTML inline via `st.components.v1.html`.

**`io/run_store.py`** API:
```python
def list_runs(output_dir: str = "output/runs") -> list[RunMeta]:
    """Return all runs sorted by timestamp descending."""

def load_run(run_id: str, output_dir: str = "output/runs") -> RunArtifacts:
    """Load equity, trades, metrics for one run."""

@dataclass
class RunMeta:
    run_id: str          # timestamp string
    symbols: list[str]
    metrics: dict        # from ml_metrics.csv

@dataclass
class RunArtifacts:
    meta: RunMeta
    equity: pd.DataFrame
    trades: pd.DataFrame
    tearsheet_path: str
```

---

### 5.3 Run Comparison (Page 2)

**File:** `research/dashboard/pages/2_Run_Comparison.py`

Multi-select up to 4 runs from the sidebar. Renders:

1. **Equity curve overlay** — all selected runs on one Plotly chart; lines labelled by run timestamp and symbols
2. **Metric delta table** — for each metric, shows value per run + % change relative to the oldest selected run
3. **Trade count and win-rate comparison** — horizontal bar chart per run

Uses `research/dashboard/components/equity_chart.py`:
```python
def equity_comparison_chart(runs: list[RunArtifacts]) -> go.Figure:
    """Overlay equity curves from multiple runs on one figure."""
```

---

### 5.4 Config Editor (Page 3)

**File:** `research/dashboard/pages/3_Config_Editor.py`

Renders a form from the current `backtest_config.yaml`. Groups fields by section (Capital, Execution, Signal Thresholds, Logging, etc.). On submit:
1. Validates using `BacktestConfig`'s field constraints (re-implemented as a Pydantic model in Python for the dashboard).
2. Shows validation errors inline if any field is out of range.
3. On success, writes the new YAML and shows a diff of changed fields.

**`io/config_io.py`:**
```python
def load_config(path: str = "backtest_config.yaml") -> dict:
    """Parse YAML into a flat dict."""

def save_config(config: dict, path: str = "backtest_config.yaml") -> None:
    """Write config dict back to YAML, preserving comments where possible."""

def diff_configs(old: dict, new: dict) -> list[tuple[str, any, any]]:
    """Return list of (field, old_value, new_value) for changed fields."""
```

---

### 5.5 Trigger Panel (Page 4)

**File:** `research/dashboard/pages/4_Trigger_Backtest.py`

Runs `run_pipeline.py` as a subprocess and streams stdout into a `st.empty()` code block, updating line by line. Shows a spinner while running; renders a success/error banner on completion.

Key constraints:
- Only one pipeline run at a time (guard with `st.session_state.running`).
- `--no-tearsheet` is offered as a checkbox (faster iteration).
- `--skip-train` is offered as a checkbox.
- After completion, refreshes the Run Browser page automatically.

```python
def stream_pipeline(args: list[str]) -> Generator[str, None, None]:
    """Yield stdout lines from run_pipeline.py as they arrive."""
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                            text=True, bufsize=1)
    for line in proc.stdout:
        yield line
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Pipeline exited with code {proc.returncode}")
```

---

### 5.6 Walk-Forward Panel (Page 5)

**File:** `research/dashboard/pages/5_Walk_Forward.py`

Reads the walk-forward report CSV produced by `research/validation/wf_report.py` (Phase 2). Renders:

1. **Fold timeline** — horizontal bar chart showing train/test windows per fold
2. **IS vs OOS Sharpe** — grouped bar chart, one group per fold
3. **OOS equity curve** — stitched out-of-sample equity across all folds
4. **Degradation table** — IS Sharpe, OOS Sharpe, ratio (OOS/IS); flags folds where ratio < 0.5 in red

---

### 5.7 Sweep Results Panel (Page 6)

**File:** `research/dashboard/pages/6_Sweep_Results.py`

Reads from the Optuna study storage (SQLite by default, path from `models/sweep_config.yaml`) or from `models/best_config.yaml` if Optuna isn't available.

Renders:
1. **Optimization history** — line chart of best value per trial
2. **Parameter importance** — horizontal bar chart (from `optuna.importance.get_param_importances`)
3. **Parallel coordinates** — Plotly parallel coordinates plot of all trials coloured by objective value
4. **Best config card** — shows best hyperparameters in a metric-card layout matching the tearsheet style

---

### 5.8 Docker Compose Integration

Add a third service to `docker-compose.yml`:

```yaml
  dashboard:
    build:
      context: .
      dockerfile: Dockerfile.python
    command: streamlit run research/dashboard/app.py --server.port 8501 --server.address 0.0.0.0
    ports:
      - "8501:8501"
    volumes:
      - ./output:/app/output:ro       # read backtest outputs
      - ./models:/app/models:ro       # read model artifacts
      - ./backtest_config.yaml:/app/backtest_config.yaml  # read/write config
    depends_on:
      - python
```

The dashboard container mounts `output/` and `models/` as read-only volumes; `backtest_config.yaml` is read-write so the Config Editor can save changes.

---

### 5.9 Test Requirements

All tests use mocked filesystem — no real backtest runs required.

**`tests/test_run_store.py`:**
- `list_runs()` returns runs sorted newest-first
- `list_runs()` on empty directory returns empty list
- `load_run()` returns correct equity/trades DataFrames from fixture CSVs
- `load_run()` on missing run_id raises `FileNotFoundError`

**`tests/test_config_io.py`:**
- `load_config()` returns correct dict from fixture YAML
- `save_config()` round-trips without data loss
- `diff_configs()` correctly identifies changed, added, and removed fields

**`tests/test_equity_chart.py`:**
- `equity_comparison_chart([run])` returns a `go.Figure` with one trace
- `equity_comparison_chart([run1, run2])` returns a `go.Figure` with two traces, labelled correctly

**`tests/test_stream_pipeline.py`:**
- `stream_pipeline()` yields each stdout line as a string
- `stream_pipeline()` raises `RuntimeError` on non-zero exit code

---

## Files Changed Summary

| File | Change Type |
|------|-------------|
| `research/dashboard/app.py` | New: Streamlit entry point with sidebar navigation |
| `research/dashboard/pages/1_Run_Browser.py` | New |
| `research/dashboard/pages/2_Run_Comparison.py` | New |
| `research/dashboard/pages/3_Config_Editor.py` | New |
| `research/dashboard/pages/4_Trigger_Backtest.py` | New |
| `research/dashboard/pages/5_Walk_Forward.py` | New |
| `research/dashboard/pages/6_Sweep_Results.py` | New |
| `research/dashboard/components/equity_chart.py` | New |
| `research/dashboard/components/metrics_table.py` | New |
| `research/dashboard/components/run_selector.py` | New |
| `research/dashboard/io/run_store.py` | New |
| `research/dashboard/io/config_io.py` | New |
| `research/dashboard/io/sweep_io.py` | New |
| `run_pipeline.py` | Add: `--archive-run` flag |
| `docker-compose.yml` | Add: `dashboard` service |
| `requirements.txt` | Add: `streamlit>=1.35` |
| `tests/test_run_store.py` | New |
| `tests/test_config_io.py` | New |
| `tests/test_equity_chart.py` | New |
| `tests/test_stream_pipeline.py` | New |

---

## Definition of Done

Phase 5 is complete when:
1. `streamlit run research/dashboard/app.py` starts without error and all 6 pages load
2. A real past backtest run appears in the Run Browser and its tearsheet renders inline
3. Two runs can be selected and equity curves overlay correctly on Page 2
4. Editing a field in the Config Editor and clicking Save writes the updated YAML
5. Clicking "Run Backtest" on Page 4 streams live output and creates a new entry in the Run Browser on completion
6. `pytest tests/test_run_store.py tests/test_config_io.py tests/test_equity_chart.py tests/test_stream_pipeline.py` all pass
7. `docker compose up` brings up all three services and the dashboard is reachable at `http://localhost:8501`
