"""Reusable Plotly equity curve chart component."""

from __future__ import annotations

from typing import TYPE_CHECKING

import plotly.graph_objects as go

if TYPE_CHECKING:
    from research.dashboard.io.run_store import RunArtifacts


def equity_comparison_chart(runs: list) -> go.Figure:
    """Overlay equity curves from multiple RunArtifacts on one Plotly figure.

    Each run gets one strategy trace (labelled by run_id + symbols) and, on
    the first run only, a benchmark trace for reference.
    """
    fig = go.Figure()

    for i, run in enumerate(runs):
        df = run.equity
        if df.empty or "timestamp" not in df.columns or "equity" not in df.columns:
            continue

        label = _run_label(run)
        fig.add_trace(go.Scatter(
            x=df["timestamp"],
            y=df["equity"],
            mode="lines",
            name=label,
        ))

        if i == 0 and "benchmark_equity" in df.columns:
            fig.add_trace(go.Scatter(
                x=df["timestamp"],
                y=df["benchmark_equity"],
                mode="lines",
                name="Buy & Hold",
                line={"dash": "dash", "color": "grey"},
            ))

    fig.update_layout(
        title="Equity Curve Comparison",
        xaxis_title="Date",
        yaxis_title="Portfolio Value ($)",
        hovermode="x unified",
        legend={"orientation": "h", "y": -0.2},
    )
    return fig


def single_equity_chart(run) -> go.Figure:
    """Equity curve for a single run with benchmark overlay."""
    return equity_comparison_chart([run])


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------

def _run_label(run) -> str:
    symbols = ", ".join(run.meta.symbols) if run.meta.symbols else "unknown"
    return f"{run.meta.run_id} ({symbols})"
