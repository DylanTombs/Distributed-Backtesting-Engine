"""Page 6 — Sweep Results: Optuna trial history and best hyperparameters."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import streamlit as st
import plotly.graph_objects as go
import plotly.express as px

from research.dashboard.io.sweep_io import (
    load_best_config,
    load_optuna_trials,
    get_sweep_storage_url,
    get_sweep_study_name,
)

MODELS_DIR = "models"

st.set_page_config(page_title="Sweep Results", layout="wide")
st.title("🔬 Hyperparameter Sweep Results")

# ---- Best config card -------------------------------------------------------
best_cfg = load_best_config(MODELS_DIR)
if best_cfg:
    st.subheader("Best Configuration")
    cols = st.columns(4)
    key_params = ["d_model", "n_heads", "e_layers", "d_ff",
                  "dropout", "learning_rate", "batch_size", "train_epochs"]
    for i, key in enumerate(key_params):
        if key in best_cfg:
            cols[i % 4].metric(key, best_cfg[key])

    with st.expander("Full best config"):
        st.json(best_cfg)
else:
    st.info(f"No `{MODELS_DIR}/best_config.yaml` found. Run an Optuna sweep first.")

st.divider()

# ---- Optuna trial history ---------------------------------------------------
storage_url  = get_sweep_storage_url(MODELS_DIR)
study_name   = get_sweep_study_name(MODELS_DIR)

trials_df = load_optuna_trials(storage_url, study_name) if storage_url else None

if trials_df is None or trials_df.empty:
    st.info(
        "No Optuna trial data found. "
        "Either run a sweep or check `models/sweep_config.yaml` for the storage URL."
    )
    st.stop()

st.subheader(f"Trial History — `{study_name}`")
st.caption(f"Storage: `{storage_url}`  |  {len(trials_df)} trials")

# Optimisation history
completed = trials_df[trials_df["state"] == "COMPLETE"].copy()
if not completed.empty:
    completed["best_so_far"] = completed["value"].cummax()

    fig_hist = go.Figure()
    fig_hist.add_scatter(x=completed["number"], y=completed["value"],
                         mode="markers", name="Trial value", marker={"opacity": 0.5})
    fig_hist.add_scatter(x=completed["number"], y=completed["best_so_far"],
                         mode="lines", name="Best so far", line={"color": "orange"})
    fig_hist.update_layout(xaxis_title="Trial", yaxis_title="Objective Value")
    st.plotly_chart(fig_hist, use_container_width=True)

# Parallel coordinates
param_cols = [c for c in completed.columns
              if c not in ("number", "value", "state", "duration_s", "best_so_far")]
if param_cols:
    st.subheader("Parallel Coordinates")
    fig_pc = px.parallel_coordinates(
        completed,
        color="value",
        dimensions=param_cols + ["value"],
        color_continuous_scale=px.colors.sequential.Viridis,
        labels={"value": "Objective"},
    )
    st.plotly_chart(fig_pc, use_container_width=True)

# Parameter importance
st.subheader("Parameter Importance")
try:
    import optuna
    storage = optuna.storages.get_storage(storage_url)
    study = optuna.load_study(study_name=study_name, storage=storage_url)
    importance = optuna.importance.get_param_importances(study)
    imp_df = sorted(importance.items(), key=lambda x: x[1], reverse=True)
    params, scores = zip(*imp_df) if imp_df else ([], [])
    fig_imp = go.Figure(go.Bar(x=list(scores), y=list(params), orientation="h"))
    fig_imp.update_layout(xaxis_title="Importance", yaxis_title="Parameter",
                          yaxis={"autorange": "reversed"})
    st.plotly_chart(fig_imp, use_container_width=True)
except Exception:
    st.caption("Parameter importance requires optuna to be installed with a live study.")

# Raw trials table
with st.expander("Raw trial data"):
    st.dataframe(trials_df, use_container_width=True, hide_index=True)
