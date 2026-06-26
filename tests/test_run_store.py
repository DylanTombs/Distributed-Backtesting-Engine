"""Tests for research/dashboard/io/run_store.py."""

import os
import sys
import pytest
import pandas as pd

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.dashboard.io.run_store import list_runs, load_run, RunMeta, RunArtifacts


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _write_run(tmp_path, run_id: str, symbols=("AAPL",)):
    run_dir = tmp_path / run_id
    run_dir.mkdir()

    equity = pd.DataFrame({
        "timestamp": ["2024-01-01", "2024-01-02"],
        "equity":    [10000.0, 10100.0],
        "price":     [150.0,   152.0],
        "benchmark_equity": [10000.0, 10050.0],
    })
    equity.to_csv(run_dir / "ml_equity.csv", index=False)

    n = len(symbols)
    trades = pd.DataFrame({
        "timestamp": ["2024-01-01"] * n,
        "symbol":    list(symbols),
        "price":     [150.0] * n,
        "quantity":  [5] * n,
        "direction": ["BUY"] * n,
        "profit":    [True] * n,
        "pnl":       [0.0] * n,
    })
    trades.to_csv(run_dir / "ml_trades.csv", index=False)

    metrics = pd.DataFrame({
        "metric": ["sharpe_ratio", "max_drawdown", "total_return"],
        "value":  [1.23, 5.6, 18.5],
    })
    metrics.to_csv(run_dir / "ml_metrics.csv", index=False)

    return run_dir


# ---------------------------------------------------------------------------
# list_runs
# ---------------------------------------------------------------------------

def test_list_runs_empty_directory_returns_empty(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    assert list_runs(str(runs_dir)) == []


def test_list_runs_nonexistent_directory_returns_empty(tmp_path):
    assert list_runs(str(tmp_path / "no_such_dir")) == []


def test_list_runs_returns_run_meta(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "20260401_143022")
    result = list_runs(str(runs_dir))
    assert len(result) == 1
    assert result[0].run_id == "20260401_143022"


def test_list_runs_sorted_newest_first(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "20260401_090000")
    _write_run(runs_dir, "20260402_120000")
    result = list_runs(str(runs_dir))
    assert result[0].run_id == "20260402_120000"
    assert result[1].run_id == "20260401_090000"


def test_list_runs_skips_dirs_without_metrics(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "20260401_143022")
    # Dir with no metrics
    (runs_dir / "bad_run").mkdir()
    result = list_runs(str(runs_dir))
    assert len(result) == 1


def test_list_runs_infers_symbols(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "20260401_143022", symbols=["AAPL", "MSFT"])
    result = list_runs(str(runs_dir))
    assert sorted(result[0].symbols) == ["AAPL", "MSFT"]


def test_list_runs_loads_metrics_dict(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "20260401_143022")
    result = list_runs(str(runs_dir))
    assert abs(result[0].metrics["sharpe_ratio"] - 1.23) < 1e-9


# ---------------------------------------------------------------------------
# load_run
# ---------------------------------------------------------------------------

def test_load_run_returns_artifacts(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "20260401_143022")
    art = load_run("20260401_143022", str(runs_dir))
    assert isinstance(art, RunArtifacts)
    assert isinstance(art.equity, pd.DataFrame)
    assert isinstance(art.trades, pd.DataFrame)


def test_load_run_equity_has_expected_columns(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    _write_run(runs_dir, "20260401_143022")
    art = load_run("20260401_143022", str(runs_dir))
    for col in ("timestamp", "equity", "price", "benchmark_equity"):
        assert col in art.equity.columns


def test_load_run_missing_run_id_raises(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    with pytest.raises(FileNotFoundError):
        load_run("nonexistent_id", str(runs_dir))


def test_load_run_missing_equity_csv_raises(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_dir = runs_dir / "20260401_143022"
    run_dir.mkdir()
    # No ml_equity.csv
    with pytest.raises(FileNotFoundError):
        load_run("20260401_143022", str(runs_dir))


def test_load_run_finds_tearsheet(tmp_path):
    runs_dir = tmp_path / "runs"
    runs_dir.mkdir()
    run_dir = _write_run(runs_dir, "20260401_143022")
    (run_dir / "tearsheet_20260401_143022.html").write_text("<html/>")
    art = load_run("20260401_143022", str(runs_dir))
    assert art.meta.tearsheet_path is not None
    assert art.meta.tearsheet_path.endswith(".html")
