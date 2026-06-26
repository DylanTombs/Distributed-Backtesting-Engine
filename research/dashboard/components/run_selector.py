"""Run-picker sidebar widget for Streamlit pages."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from research.dashboard.io.run_store import RunMeta


def run_selector_options(runs: list) -> list[str]:
    """Return display labels for a list of RunMeta objects.

    Separated from Streamlit so the label logic is unit-testable.
    """
    return [_run_option_label(r) for r in runs]


def run_id_from_label(label: str) -> str:
    """Extract the run_id from a display label produced by run_selector_options."""
    return label.split(" ")[0]


def render_run_selector(runs: list, *, multi: bool = False, max_select: int = 4):
    """Render a Streamlit sidebar selector and return selected RunMeta(s).

    Returns a list when multi=True, else a single RunMeta or None.
    Importing streamlit here keeps it optional for unit tests.
    """
    import streamlit as st  # noqa: F401

    options = run_selector_options(runs)
    if not options:
        st.sidebar.warning("No runs found in output/runs/")
        return [] if multi else None

    if multi:
        selected_labels = st.sidebar.multiselect(
            "Select runs (up to {})".format(max_select),
            options=options,
            default=options[:1],
            max_selections=max_select,
        )
        selected_ids = {run_id_from_label(lbl) for lbl in selected_labels}
        return [r for r in runs if r.run_id in selected_ids]
    else:
        selected_label = st.sidebar.selectbox("Select run", options)
        selected_id = run_id_from_label(selected_label)
        return next((r for r in runs if r.run_id == selected_id), None)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _run_option_label(run) -> str:
    symbols = ", ".join(run.symbols) if run.symbols else "unknown"
    sharpe  = run.metrics.get("sharpe_ratio")
    sharpe_str = f"  Sharpe {sharpe:.2f}" if sharpe is not None else ""
    return f"{run.run_id}  [{symbols}]{sharpe_str}"
