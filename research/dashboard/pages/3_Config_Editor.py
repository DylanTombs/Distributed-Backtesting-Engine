"""Page 3 — Config Editor: edit backtest_config.yaml through validated form inputs."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import streamlit as st

from research.dashboard.io.config_io import load_config, save_config, diff_configs

CONFIG_PATH = "backtest_config.yaml"

st.set_page_config(page_title="Config Editor", layout="wide")
st.title("⚙️ Config Editor")
st.caption(f"Editing `{CONFIG_PATH}`")

try:
    cfg = load_config(CONFIG_PATH)
except FileNotFoundError:
    st.error(f"`{CONFIG_PATH}` not found. Make sure you are running the dashboard from the project root.")
    st.stop()

original_cfg = dict(cfg)

# ---- Form sections ----------------------------------------------------------
with st.form("config_form"):
    st.subheader("Capital & Sizing")
    col1, col2, col3 = st.columns(3)
    cfg["initial_cash"]       = col1.number_input("Initial Cash ($)", value=float(cfg.get("initial_cash", 100000)), min_value=1.0)
    cfg["risk_fraction"]      = col2.number_input("Risk Fraction", value=float(cfg.get("risk_fraction", 0.10)), min_value=0.01, max_value=1.0, step=0.01, format="%.3f")
    cfg["max_symbol_exposure"]= col3.number_input("Max Symbol Exposure", value=float(cfg.get("max_symbol_exposure", 0.20)), min_value=0.01, max_value=1.0, step=0.01, format="%.3f")

    col4, col5 = st.columns(2)
    cfg["max_total_exposure"] = col4.number_input("Max Total Exposure", value=float(cfg.get("max_total_exposure", 0.80)), min_value=0.01, max_value=1.0, step=0.01, format="%.3f")
    cfg["max_position_size"]  = col5.number_input("Max Position Size (shares)", value=int(cfg.get("max_position_size", 10000)), min_value=1, step=100)

    st.subheader("Execution Costs")
    col6, col7, col8, col9 = st.columns(4)
    cfg["half_spread"]        = col6.number_input("Half Spread", value=float(cfg.get("half_spread", 0.0005)), min_value=0.0, step=0.0001, format="%.4f")
    cfg["slippage_fraction"]  = col7.number_input("Slippage Fraction", value=float(cfg.get("slippage_fraction", 0.0005)), min_value=0.0, step=0.0001, format="%.4f")
    cfg["market_impact"]      = col8.number_input("Market Impact ($/share)", value=float(cfg.get("market_impact", 0.0)), min_value=0.0, step=0.001, format="%.4f")
    cfg["commission"]         = col9.number_input("Commission ($/trade)", value=float(cfg.get("commission", 1.0)), min_value=0.0, step=0.5)

    st.subheader("Signal Thresholds")
    col10, col11 = st.columns(2)
    cfg["buy_threshold"]  = col10.number_input("Buy Threshold", value=float(cfg.get("buy_threshold", 0.005)), min_value=0.0, max_value=0.1, step=0.001, format="%.4f")
    cfg["exit_threshold"] = col11.number_input("Exit Threshold", value=float(cfg.get("exit_threshold", 0.0)), min_value=0.0, max_value=0.1, step=0.001, format="%.4f")

    st.subheader("Short Selling")
    col12, col13 = st.columns(2)
    cfg["allow_short"]      = col12.checkbox("Allow Short Selling", value=bool(cfg.get("allow_short", False)))
    cfg["short_margin_rate"]= col13.number_input("Short Margin Rate", value=float(cfg.get("short_margin_rate", 1.0)), min_value=0.01, max_value=2.0, step=0.1, format="%.2f")

    st.subheader("Logging")
    col14, col15 = st.columns(2)
    cfg["log_level"] = col14.selectbox("Log Level", ["trace", "debug", "info", "warn", "error", "critical"],
                                        index=["trace", "debug", "info", "warn", "error", "critical"].index(cfg.get("log_level", "info")))
    cfg["log_file"] = col15.text_input("Log File Path", value=cfg.get("log_file", "output/backtest.log"))

    submitted = st.form_submit_button("💾 Save Config", type="primary")

# ---- Validation & save ------------------------------------------------------
if submitted:
    errors = []
    if cfg["risk_fraction"] > cfg["max_symbol_exposure"]:
        errors.append("risk_fraction must be ≤ max_symbol_exposure")
    if cfg["max_symbol_exposure"] > cfg["max_total_exposure"]:
        errors.append("max_symbol_exposure must be ≤ max_total_exposure")

    if errors:
        for e in errors:
            st.error(e)
    else:
        changes = diff_configs(original_cfg, cfg)
        if not changes:
            st.info("No changes detected.")
        else:
            save_config(cfg, CONFIG_PATH)
            st.success(f"Saved {len(changes)} change(s) to `{CONFIG_PATH}`")
            with st.expander("Changed fields"):
                for field, old, new in changes:
                    st.markdown(f"- **{field}**: `{old}` → `{new}`")
