"""Page 5 — Walk-Forward: visualise IS vs OOS Sharpe per fold."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from pathlib import Path

import streamlit as st
import pandas as pd
import plotly.graph_objects as go

WF_REPORT_PATH = "output/wf_report.csv"

st.set_page_config(page_title="Walk-Forward", layout="wide")
st.title("🔄 Walk-Forward Validation")

report_path = st.text_input("Walk-forward report CSV", value=WF_REPORT_PATH)

if not Path(report_path).exists():
    st.warning(
        f"`{report_path}` not found. "
        "Run `python research/validation/wf_report.py` to generate it."
    )
    st.stop()

df = pd.read_csv(report_path)

required_cols = {"fold", "is_sharpe", "oos_sharpe"}
if not required_cols.issubset(df.columns):
    st.error(f"Report must have columns: {required_cols}. Found: {list(df.columns)}")
    st.stop()

# ---- IS vs OOS Sharpe bar chart --------------------------------------------
st.subheader("In-Sample vs Out-of-Sample Sharpe")
fig = go.Figure()
fig.add_bar(x=df["fold"], y=df["is_sharpe"],  name="In-Sample",     marker_color="#4C78A8")
fig.add_bar(x=df["fold"], y=df["oos_sharpe"], name="Out-of-Sample", marker_color="#F58518")
fig.update_layout(
    barmode="group",
    xaxis_title="Fold",
    yaxis_title="Sharpe Ratio",
    hovermode="x unified",
)
st.plotly_chart(fig, use_container_width=True)

# ---- Degradation table -------------------------------------------------------
st.subheader("Degradation Analysis")
deg_df = df[["fold", "is_sharpe", "oos_sharpe"]].copy()
deg_df["OOS/IS Ratio"] = (deg_df["oos_sharpe"] / deg_df["is_sharpe"].replace(0, float("nan"))).round(3)

def _highlight_degraded(val):
    if pd.isna(val):
        return ""
    return "background-color: #ffcccc" if val < 0.5 else ""

st.dataframe(
    deg_df.style.applymap(_highlight_degraded, subset=["OOS/IS Ratio"]),
    use_container_width=True,
    hide_index=True,
)

# ---- OOS equity curve (stitched) -------------------------------------------
if "oos_equity_csv" in df.columns:
    st.subheader("Stitched OOS Equity Curve")
    frames = []
    for _, row in df.iterrows():
        if Path(row["oos_equity_csv"]).exists():
            fold_df = pd.read_csv(row["oos_equity_csv"])
            fold_df["fold"] = row["fold"]
            frames.append(fold_df)
    if frames:
        combined = pd.concat(frames, ignore_index=True)
        if "timestamp" in combined.columns and "equity" in combined.columns:
            st.line_chart(combined.set_index("timestamp")["equity"])
