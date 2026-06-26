"""Page 1 — Run Browser: list past backtest runs and view tearsheets."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import streamlit as st
import pandas as pd

from research.dashboard.io.run_store import list_runs, load_run

OUTPUT_DIR = "output/runs"

st.set_page_config(page_title="Run Browser", layout="wide")
st.title("🗂 Run Browser")

runs = list_runs(OUTPUT_DIR)

if not runs:
    st.warning(
        f"No runs found in `{OUTPUT_DIR}`. "
        "Run a backtest first, or enable `--archive-run` in `run_pipeline.py`."
    )
    st.stop()

# ---- Summary table ----------------------------------------------------------
rows = []
for r in runs:
    m = r.metrics
    rows.append({
        "Run ID":        r.run_id,
        "Symbols":       ", ".join(r.symbols),
        "Sharpe":        round(m.get("sharpe_ratio", 0.0), 3),
        "Max DD (%)":    round(m.get("max_drawdown", 0.0), 2),
        "Return (%)":    round(m.get("total_return", 0.0), 2),
        "Alpha (%)":     round(m.get("alpha", 0.0), 2),
        "Days":          int(m.get("trading_days", 0)),
        "Has Tearsheet": "✅" if r.tearsheet_path else "—",
    })

df = pd.DataFrame(rows)
st.dataframe(df, use_container_width=True, hide_index=True)

# ---- Detail panel -----------------------------------------------------------
st.divider()
st.subheader("Run Detail")

selected_id = st.selectbox(
    "Select a run to inspect",
    options=[r.run_id for r in runs],
    format_func=lambda rid: next(
        (f"{r.run_id}  [{', '.join(r.symbols)}]" for r in runs if r.run_id == rid), rid
    ),
)

if selected_id:
    art = load_run(selected_id, OUTPUT_DIR)

    col1, col2 = st.columns(2)
    with col1:
        st.markdown("**Equity Curve**")
        if not art.equity.empty and "timestamp" in art.equity.columns:
            st.line_chart(art.equity.set_index("timestamp")[["equity", "benchmark_equity"]])

    with col2:
        st.markdown("**Trades**")
        if not art.trades.empty:
            st.dataframe(art.trades, use_container_width=True, hide_index=True)
        else:
            st.caption("No trades recorded.")

    if art.meta.tearsheet_path:
        with st.expander("📄 View Tearsheet HTML", expanded=False):
            html_content = open(art.meta.tearsheet_path).read()
            st.components.v1.html(html_content, height=900, scrolling=True)
