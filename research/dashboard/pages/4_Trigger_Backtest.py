"""Page 4 — Trigger Panel: launch run_pipeline.py and stream stdout live."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", ".."))

import subprocess
from typing import Generator

import streamlit as st

st.set_page_config(page_title="Trigger Backtest", layout="wide")
st.title("🚀 Trigger Backtest")

# ---- Options ----------------------------------------------------------------
with st.expander("Pipeline options", expanded=True):
    skip_train   = st.checkbox("Skip training (--skip-train)", value=False,
                               help="Re-export an existing checkpoint; skip model training.")
    no_tearsheet = st.checkbox("Skip tearsheet (--no-tearsheet)", value=False,
                               help="Faster iteration — omit the HTML tearsheet stage.")
    archive_run  = st.checkbox("Archive run (--archive-run)", value=True,
                               help="Save outputs to a timestamped output/runs/ subdirectory.")

run_btn = st.button("▶ Run Pipeline", type="primary",
                    disabled=st.session_state.get("pipeline_running", False))

# ---- Stream pipeline output -------------------------------------------------

def stream_pipeline(args: list[str]) -> Generator[str, None, None]:
    """Yield stdout+stderr lines from run_pipeline.py as they arrive."""
    proc = subprocess.Popen(
        args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    for line in proc.stdout:
        yield line
    proc.wait()
    if proc.returncode != 0:
        raise RuntimeError(f"Pipeline exited with code {proc.returncode}")


if run_btn:
    st.session_state["pipeline_running"] = True

    cmd = [sys.executable, "run_pipeline.py"]
    if skip_train:
        cmd.append("--skip-train")
    if no_tearsheet:
        cmd.append("--no-tearsheet")
    if archive_run:
        cmd.append("--archive-run")

    output_box = st.empty()
    lines: list[str] = []

    with st.spinner("Pipeline running…"):
        try:
            for line in stream_pipeline(cmd):
                lines.append(line)
                output_box.code("".join(lines[-80:]), language="text")
            st.success("✅ Pipeline completed successfully.")
        except RuntimeError as e:
            st.error(str(e))
        finally:
            st.session_state["pipeline_running"] = False

    st.caption("Navigate to **Run Browser** to see the new run.")
