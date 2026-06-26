"""
TradingTransformer Research Dashboard — Streamlit entry point.

Usage:
    streamlit run research/dashboard/app.py

Pages are auto-discovered by Streamlit from the pages/ directory.
This file sets global page config and renders the home landing screen.
"""

import streamlit as st

st.set_page_config(
    page_title="TradingTransformer Dashboard",
    page_icon="📈",
    layout="wide",
    initial_sidebar_state="expanded",
)

st.title("📈 TradingTransformer Research Dashboard")
st.markdown("""
Welcome to the interactive research dashboard.
Use the sidebar to navigate between panels.

| Page | Description |
|------|-------------|
| **1 · Run Browser** | Browse past backtest runs, view metrics, open tearsheets |
| **2 · Run Comparison** | Overlay equity curves and compare metrics across runs |
| **3 · Config Editor** | Edit `backtest_config.yaml` fields through a validated form |
| **4 · Trigger Backtest** | Launch `run_pipeline.py` and stream live output |
| **5 · Walk-Forward** | Visualise in-sample vs out-of-sample Sharpe per fold |
| **6 · Sweep Results** | Explore Optuna trial history and best hyperparameters |
""")

st.info("Select a page from the sidebar to get started.", icon="👈")
