"""Tests for dashboard components: equity_chart, metrics_table, run_selector."""

import os
import sys
import pytest
import pandas as pd
from unittest.mock import MagicMock, patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.dashboard.io.run_store import RunMeta, RunArtifacts
from research.dashboard.components.equity_chart import equity_comparison_chart, single_equity_chart
from research.dashboard.components.metrics_table import metrics_summary_table, metric_delta_table
from research.dashboard.components.run_selector import (
    run_selector_options, run_id_from_label, render_run_selector,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_equity_df(n: int = 5, start: float = 10_000.0) -> pd.DataFrame:
    return pd.DataFrame({
        "timestamp":        [f"2024-01-{i+1:02d}" for i in range(n)],
        "equity":           [start + i * 100 for i in range(n)],
        "price":            [100.0 + i for i in range(n)],
        "benchmark_equity": [start + i * 50 for i in range(n)],
    })


def _make_run(run_id: str, symbols: list[str], sharpe: float = 1.0) -> RunArtifacts:
    meta = RunMeta(
        run_id=run_id,
        symbols=symbols,
        metrics={"sharpe_ratio": sharpe, "max_drawdown": 5.0, "total_return": 18.0},
    )
    return RunArtifacts(meta=meta, equity=_make_equity_df(), trades=pd.DataFrame())


# ---------------------------------------------------------------------------
# equity_chart
# ---------------------------------------------------------------------------

def test_equity_comparison_chart_single_run_returns_figure():
    import plotly.graph_objects as go
    run = _make_run("20260401_143022", ["AAPL"])
    fig = equity_comparison_chart([run])
    assert isinstance(fig, go.Figure)


def test_equity_comparison_chart_single_run_has_two_traces():
    # Strategy trace + benchmark trace
    run = _make_run("20260401_143022", ["AAPL"])
    fig = equity_comparison_chart([run])
    assert len(fig.data) == 2


def test_equity_comparison_chart_two_runs_has_three_traces():
    # run1 strategy + benchmark + run2 strategy (benchmark only on first run)
    run1 = _make_run("20260401_143022", ["AAPL"])
    run2 = _make_run("20260402_091155", ["MSFT"])
    fig = equity_comparison_chart([run1, run2])
    assert len(fig.data) == 3


def test_equity_comparison_chart_trace_labels_contain_run_id():
    run = _make_run("20260401_143022", ["AAPL"])
    fig = equity_comparison_chart([run])
    trace_names = [t.name for t in fig.data]
    assert any("20260401_143022" in name for name in trace_names)


def test_equity_comparison_chart_benchmark_trace_is_dashed():
    run = _make_run("20260401_143022", ["AAPL"])
    fig = equity_comparison_chart([run])
    dashed = [t for t in fig.data if t.line and t.line.dash == "dash"]
    assert len(dashed) == 1


def test_equity_comparison_chart_empty_runs_returns_empty_figure():
    import plotly.graph_objects as go
    fig = equity_comparison_chart([])
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 0


def test_single_equity_chart_delegates_to_comparison():
    import plotly.graph_objects as go
    run = _make_run("20260401_143022", ["AAPL"])
    fig = single_equity_chart(run)
    assert isinstance(fig, go.Figure)
    assert len(fig.data) == 2


# ---------------------------------------------------------------------------
# metrics_table
# ---------------------------------------------------------------------------

def test_metrics_summary_table_empty_runs_returns_empty():
    df = metrics_summary_table([])
    assert df.empty


def test_metrics_summary_table_single_run_has_one_data_column():
    run = _make_run("20260401_143022", ["AAPL"])
    df = metrics_summary_table([run])
    assert df.shape[1] == 1


def test_metrics_summary_table_two_runs_has_two_data_columns():
    run1 = _make_run("20260401_143022", ["AAPL"])
    run2 = _make_run("20260402_091155", ["MSFT"])
    df = metrics_summary_table([run1, run2])
    assert df.shape[1] == 2


def test_metric_delta_table_shows_delta_for_changed_values():
    run1 = _make_run("20260401_143022", ["AAPL"], sharpe=1.0)
    run2 = _make_run("20260402_091155", ["MSFT"], sharpe=1.5)
    df = metric_delta_table(run1, run2)
    sharpe_row = df[df["Metric"].str.contains("Sharpe")]
    assert not sharpe_row.empty
    assert sharpe_row.iloc[0]["Δ"] != ""


def test_metric_delta_table_zero_delta_when_identical():
    run = _make_run("20260401_143022", ["AAPL"], sharpe=1.23)
    df = metric_delta_table(run, run)
    sharpe_row = df[df["Metric"].str.contains("Sharpe")]
    assert sharpe_row.iloc[0]["Δ"] == ""


# ---------------------------------------------------------------------------
# run_selector
# ---------------------------------------------------------------------------

def test_run_selector_options_returns_one_label_per_run():
    runs = [
        RunMeta("20260401_143022", ["AAPL"], {"sharpe_ratio": 1.23}),
        RunMeta("20260402_091155", ["MSFT"], {"sharpe_ratio": 0.87}),
    ]
    options = run_selector_options(runs)
    assert len(options) == 2


def test_run_selector_options_label_contains_run_id():
    runs = [RunMeta("20260401_143022", ["AAPL"], {"sharpe_ratio": 1.23})]
    options = run_selector_options(runs)
    assert "20260401_143022" in options[0]


def test_run_id_from_label_extracts_run_id():
    runs = [RunMeta("20260401_143022", ["AAPL"], {"sharpe_ratio": 1.23})]
    label = run_selector_options(runs)[0]
    assert run_id_from_label(label) == "20260401_143022"


# render_run_selector — tested with mocked streamlit

def _make_st_mock(selected_labels=None, selected_label=None):
    st = MagicMock()
    st.sidebar.multiselect.return_value = selected_labels or []
    st.sidebar.selectbox.return_value = selected_label or ""
    return st


def test_render_run_selector_single_returns_matching_run():
    runs = [
        RunMeta("20260401_143022", ["AAPL"], {"sharpe_ratio": 1.23}),
        RunMeta("20260402_091155", ["MSFT"], {"sharpe_ratio": 0.87}),
    ]
    label = run_selector_options(runs)[0]
    st_mock = _make_st_mock(selected_label=label)

    with patch.dict(sys.modules, {"streamlit": st_mock}):
        result = render_run_selector(runs, multi=False)

    assert result is not None
    assert result.run_id == "20260401_143022"


def test_render_run_selector_multi_returns_list():
    runs = [
        RunMeta("20260401_143022", ["AAPL"], {"sharpe_ratio": 1.23}),
        RunMeta("20260402_091155", ["MSFT"], {"sharpe_ratio": 0.87}),
    ]
    labels = run_selector_options(runs)
    st_mock = _make_st_mock(selected_labels=labels[:1])

    with patch.dict(sys.modules, {"streamlit": st_mock}):
        result = render_run_selector(runs, multi=True, max_select=4)

    assert isinstance(result, list)
    assert len(result) == 1
    assert result[0].run_id == "20260401_143022"


def test_render_run_selector_empty_runs_returns_none():
    st_mock = _make_st_mock()

    with patch.dict(sys.modules, {"streamlit": st_mock}):
        result = render_run_selector([], multi=False)

    assert result is None
    st_mock.sidebar.warning.assert_called_once()


def test_render_run_selector_empty_runs_multi_returns_empty_list():
    st_mock = _make_st_mock()

    with patch.dict(sys.modules, {"streamlit": st_mock}):
        result = render_run_selector([], multi=True)

    assert result == []


def test_render_run_selector_unknown_label_returns_none():
    runs = [RunMeta("20260401_143022", ["AAPL"], {})]
    st_mock = _make_st_mock(selected_label="unknown_id  [—]")

    with patch.dict(sys.modules, {"streamlit": st_mock}):
        result = render_run_selector(runs, multi=False)

    assert result is None
