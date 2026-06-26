"""Page 2 — Run Comparison: overlay equity curves and compare metrics."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import pandas as pd
import streamlit as st
import plotly.graph_objects as go

from research.dashboard.io.run_store import list_runs, load_run
from research.dashboard.components.equity_chart import equity_comparison_chart
from research.dashboard.components.metrics_table import metrics_summary_table, metric_delta_table
from research.dashboard.components.run_selector import render_run_selector

OUTPUT_DIR = "output/runs"

st.set_page_config(page_title="Run Comparison", layout="wide")
st.title("🔀 Run Comparison")

runs_meta = list_runs(OUTPUT_DIR)
if not runs_meta:
    st.warning("No runs found. Run a backtest first.")
    st.stop()

selected_meta = render_run_selector(runs_meta, multi=True, max_select=4)

if not selected_meta:
    st.info("Select 2–4 runs in the sidebar to compare.")
    st.stop()

selected_runs = [load_run(r.run_id, OUTPUT_DIR) for r in selected_meta]

# ---- Equity curve overlay ---------------------------------------------------
st.subheader("Equity Curves")
fig = equity_comparison_chart(selected_runs)
st.plotly_chart(fig, use_container_width=True)

# ---- Metric comparison table ------------------------------------------------
st.subheader("Metric Comparison")
summary_df = metrics_summary_table(selected_runs)
st.dataframe(summary_df, use_container_width=True)

# ---- Delta table (baseline = oldest selected run) --------------------------
if len(selected_runs) >= 2:
    st.subheader("Delta vs Baseline")
    st.caption(f"Baseline: {selected_runs[-1].meta.run_id}")
    delta_df = metric_delta_table(selected_runs[-1], selected_runs[0])
    st.dataframe(delta_df, use_container_width=True, hide_index=True)

# ---- Trade win-rate comparison ----------------------------------------------
st.subheader("Trade Win Rate")
win_rates = []
for run in selected_runs:
    if run.trades.empty or "profit" not in run.trades.columns:
        continue
    if "direction" in run.trades.columns:
        sells = run.trades[run.trades["direction"].isin(["SELL", "COVER"])]
    else:
        sells = run.trades
    total = len(sells)
    wins = int((sells["profit"] > 0).sum()) if total > 0 else 0
    win_rates.append({
        "Run": run.meta.run_id,
        "Total Trades": total,
        "Wins": wins,
        "Win Rate (%)": round(wins / total * 100, 1) if total > 0 else 0.0,
    })

if win_rates:
    st.dataframe(pd.DataFrame(win_rates), use_container_width=True, hide_index=True)
else:
    st.caption("No trade data available for selected runs.")
