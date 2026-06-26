"""Page 5 — Walk-Forward: per-symbol MSE/MAPE summary and fold drill-down."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
import streamlit as st

WF_SUMMARY_PATH = "output/wf_summary.csv"
WF_PER_FOLD_DIR = "output"

st.set_page_config(page_title="Walk-Forward", layout="wide")
st.title("Walk-Forward Validation")

summary_path = st.text_input("Walk-forward summary CSV", value=WF_SUMMARY_PATH)

if not Path(summary_path).exists():
    st.info(
        f"No walk-forward data found at `{summary_path}`. "
        "Results will appear here after a validation run completes."
    )
    st.stop()

df = pd.read_csv(summary_path)

required_cols = {"symbol", "mean_mse", "mean_rmse", "mean_mape_%"}
if not required_cols.issubset(df.columns):
    st.error(f"Summary must have columns: {required_cols}. Found: {list(df.columns)}")
    st.stop()

# ---- Cross-symbol summary table ---------------------------------------------
st.subheader("Cross-Symbol Summary")
display_df = df.rename(columns={
    "symbol":      "Symbol",
    "folds":       "Folds",
    "mean_mse":    "Mean MSE",
    "std_mse":     "Std MSE",
    "mean_rmse":   "Mean RMSE",
    "std_rmse":    "Std RMSE",
    "mean_mape_%": "Mean MAPE (%)",
    "std_mape_%":  "Std MAPE (%)",
})
st.dataframe(display_df, use_container_width=True, hide_index=True)

# ---- MAPE bar chart ---------------------------------------------------------
st.subheader("Mean MAPE by Symbol")
fig = go.Figure()
fig.add_bar(
    x=df["symbol"],
    y=df["mean_mape_%"],
    error_y={"type": "data", "array": df.get("std_mape_%", [0] * len(df)).tolist()},
    marker_color="#4C78A8",
)
fig.update_layout(
    xaxis_title="Symbol",
    yaxis_title="MAPE (%)",
    xaxis={"tickangle": -45},
)
st.plotly_chart(fig, use_container_width=True)

# ---- RMSE bar chart ---------------------------------------------------------
st.subheader("Mean RMSE by Symbol")
fig2 = go.Figure()
fig2.add_bar(
    x=df["symbol"],
    y=df["mean_rmse"],
    error_y={"type": "data", "array": df.get("std_rmse", [0] * len(df)).tolist()},
    marker_color="#F58518",
)
fig2.update_layout(xaxis_title="Symbol", yaxis_title="RMSE",
                   xaxis={"tickangle": -45})
st.plotly_chart(fig2, use_container_width=True)

# ---- Per-fold drill-down ----------------------------------------------------
st.divider()
st.subheader("Per-Fold Detail")

symbols_with_folds = []
for sym in df["symbol"].tolist():
    fold_csv = Path(WF_PER_FOLD_DIR) / f"wf_{sym}.csv"
    if fold_csv.exists():
        symbols_with_folds.append(sym)

if not symbols_with_folds:
    st.caption("No per-fold CSV files found in `output/`. Run walk-forward to generate them.")
else:
    selected_sym = st.selectbox("Select symbol", symbols_with_folds)
    fold_df = pd.read_csv(Path(WF_PER_FOLD_DIR) / f"wf_{selected_sym}.csv")

    fig3 = go.Figure()
    fig3.add_bar(x=fold_df["fold"], y=fold_df["test_mse"],
                 name="Test MSE", marker_color="#4C78A8")
    fig3.add_bar(x=fold_df["fold"], y=fold_df["test_rmse"],
                 name="Test RMSE", marker_color="#F58518")
    fig3.update_layout(barmode="group", xaxis_title="Fold",
                       yaxis_title="Error", title=f"{selected_sym} — per-fold errors")
    st.plotly_chart(fig3, use_container_width=True)

    st.dataframe(fold_df, use_container_width=True, hide_index=True)
