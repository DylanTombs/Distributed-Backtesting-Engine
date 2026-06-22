"""
Tests for research/validation/walk_forward.py and wf_report.py.

All tests operate on synthetic in-memory DataFrames — no model training,
no real feature CSVs required.
"""
import io
import os
import tempfile

import numpy as np
import pandas as pd
import pytest

# conftest adds research/ subdirs to sys.path; validation is under research/
import sys
import pathlib
_RESEARCH = str(pathlib.Path(__file__).parent.parent / "research")
if _RESEARCH not in sys.path:
    sys.path.insert(0, _RESEARCH)

from validation.walk_forward import (
    WalkForwardConfig,
    WalkForwardValidator,
    WalkForwardResult,
    FoldMetrics,
    export_fold_csv,
)
from validation.wf_report import WalkForwardReport, _symbol_from_path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_config(**kwargs) -> WalkForwardConfig:
    defaults = dict(min_train_bars=100, val_bars=20, test_bars=20,
                    step_bars=20, n_folds=5)
    defaults.update(kwargs)
    return WalkForwardConfig(**defaults)


def _enough_rows(cfg: WalkForwardConfig, n_folds: int = 1) -> int:
    """Minimum rows for n_folds folds given cfg."""
    return (cfg.min_train_bars
            + cfg.val_bars
            + cfg.test_bars
            + (n_folds - 1) * cfg.step_bars)


# ---------------------------------------------------------------------------
# WalkForwardConfig defaults
# ---------------------------------------------------------------------------

class TestWalkForwardConfig:
    def test_default_n_folds(self):
        assert WalkForwardConfig().n_folds == 5

    def test_default_min_train(self):
        assert WalkForwardConfig().min_train_bars == 252

    def test_default_test_bars(self):
        assert WalkForwardConfig().test_bars == 63


# ---------------------------------------------------------------------------
# Fold boundary computation
# ---------------------------------------------------------------------------

class TestFoldBoundaries:

    def test_correct_number_of_folds_returned(self):
        cfg = _make_config(n_folds=5)
        n = _enough_rows(cfg, 5)
        validator = WalkForwardValidator(cfg)
        folds = validator.get_fold_boundaries(n)
        assert len(folds) == 5

    def test_zero_folds_when_data_too_small(self):
        cfg = _make_config(min_train_bars=300, val_bars=50, test_bars=50, n_folds=5)
        validator = WalkForwardValidator(cfg)
        folds = validator.get_fold_boundaries(100)
        assert folds == []

    def test_train_window_expands_each_fold(self):
        cfg = _make_config(n_folds=3)
        n = _enough_rows(cfg, 3)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        sizes = [f.train_end - f.train_start for f in folds]
        assert sizes[1] > sizes[0]
        assert sizes[2] > sizes[1]

    def test_train_start_is_always_zero(self):
        cfg = _make_config(n_folds=4)
        n = _enough_rows(cfg, 4)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        assert all(f.train_start == 0 for f in folds)

    def test_fold_indices_are_contiguous(self):
        """train_end == val_start, val_end == test_start for every fold."""
        cfg = _make_config(n_folds=3)
        n = _enough_rows(cfg, 3)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        for f in folds:
            assert f.val_start == f.train_end
            assert f.test_start == f.val_end

    def test_test_windows_do_not_overlap(self):
        cfg = _make_config(n_folds=4)
        n = _enough_rows(cfg, 4)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        for i in range(len(folds) - 1):
            assert folds[i].test_end <= folds[i + 1].test_start, (
                f"Fold {folds[i].fold} test window overlaps fold {folds[i+1].fold}"
            )

    def test_test_window_width_equals_config(self):
        cfg = _make_config(test_bars=30, n_folds=3)
        n = _enough_rows(cfg, 3)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        for f in folds:
            assert f.test_end - f.test_start == 30

    def test_val_window_width_equals_config(self):
        cfg = _make_config(val_bars=25, n_folds=2)
        n = _enough_rows(cfg, 2)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        for f in folds:
            assert f.val_end - f.val_start == 25

    def test_fold_numbering_starts_at_one(self):
        cfg = _make_config(n_folds=3)
        n = _enough_rows(cfg, 3)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        assert [f.fold for f in folds] == [1, 2, 3]

    def test_training_window_contains_all_prior_data(self):
        """Fold N's train window must cover rows 0..train_end, i.e. all data
        before fold N's test window."""
        cfg = _make_config(n_folds=4)
        n = _enough_rows(cfg, 4)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        for f in folds:
            # train_end is exactly where val begins; nothing is skipped
            assert f.train_end == f.val_start

    def test_partial_folds_skipped_gracefully(self):
        """When data runs out mid-fold the remaining folds are silently dropped."""
        cfg = _make_config(n_folds=10, min_train_bars=100,
                           val_bars=20, test_bars=20, step_bars=20)
        # Enough for exactly 3 folds
        n = _enough_rows(cfg, 3)
        folds = WalkForwardValidator(cfg).get_fold_boundaries(n)
        assert len(folds) == 3


# ---------------------------------------------------------------------------
# WalkForwardResult
# ---------------------------------------------------------------------------

class TestWalkForwardResult:
    def _make_result(self, n=3) -> WalkForwardResult:
        result = WalkForwardResult(symbol="TEST")
        for i in range(1, n + 1):
            result.per_fold.append(FoldMetrics(
                fold=i,
                test_mse=float(i) * 0.01,
                test_rmse=float(i) * 0.1,
                test_mape=float(i) * 1.0,
                n_test_windows=10,
            ))
        return result

    def test_summary_returns_correct_n_folds(self):
        result = self._make_result(4)
        assert result.summary()["n_folds"] == 4

    def test_summary_mean_mse_is_correct(self):
        result = self._make_result(3)
        expected = np.mean([0.01, 0.02, 0.03])
        assert result.summary()["mean_mse"] == pytest.approx(expected)

    def test_empty_result_summary_is_empty_dict(self):
        result = WalkForwardResult(symbol="X")
        assert result.summary() == {}


# ---------------------------------------------------------------------------
# export_fold_csv
# ---------------------------------------------------------------------------

class TestExportFoldCSV:
    def test_csv_written_to_output_dir(self):
        result = WalkForwardResult(symbol="AAPL")
        result.per_fold.append(FoldMetrics(
            fold=1, test_mse=0.01, test_rmse=0.1, test_mape=2.0, n_test_windows=5
        ))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_fold_csv(result, tmpdir)
            assert os.path.exists(path)
            df = pd.read_csv(path)
            assert len(df) == 1
            assert df.iloc[0]['test_mse'] == pytest.approx(0.01)

    def test_csv_filename_contains_symbol(self):
        result = WalkForwardResult(symbol="MSFT")
        result.per_fold.append(FoldMetrics(
            fold=1, test_mse=0.02, test_rmse=0.14, test_mape=1.5, n_test_windows=3
        ))
        with tempfile.TemporaryDirectory() as tmpdir:
            path = export_fold_csv(result, tmpdir)
            assert "MSFT" in os.path.basename(path)


# ---------------------------------------------------------------------------
# WalkForwardReport
# ---------------------------------------------------------------------------

class TestWalkForwardReport:
    def _write_fold_csv(self, tmpdir: str, symbol: str, rows: list) -> str:
        df = pd.DataFrame(rows)
        path = os.path.join(tmpdir, f"wf_{symbol}.csv")
        df.to_csv(path, index=False)
        return path

    def test_loads_multiple_symbol_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            for sym, mse in [("AAPL", 0.01), ("MSFT", 0.02)]:
                self._write_fold_csv(tmpdir, sym, [
                    {"fold": i, "test_mse": mse * i, "test_rmse": 0.1,
                     "test_mape": 1.0} for i in range(1, 4)
                ])
            report = WalkForwardReport(tmpdir).load()
            assert len(report.summaries) == 2

    def test_raises_when_no_files_found(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            with pytest.raises(FileNotFoundError):
                WalkForwardReport(tmpdir).load()

    def test_nan_folds_are_excluded_from_mean(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_fold_csv(tmpdir, "X", [
                {"fold": 1, "test_mse": 0.10, "test_rmse": 0.31, "test_mape": 2.0},
                {"fold": 2, "test_mse": float("nan"), "test_rmse": float("nan"),
                 "test_mape": float("nan")},
                {"fold": 3, "test_mse": 0.20, "test_rmse": 0.45, "test_mape": 3.0},
            ])
            report = WalkForwardReport(tmpdir).load()
            s = report.summaries[0]
            assert s.n_folds == 2  # NaN fold dropped
            assert s.mean_mse == pytest.approx(0.15)

    def test_text_report_contains_symbol(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_fold_csv(tmpdir, "GOOGL", [
                {"fold": i, "test_mse": 0.01, "test_rmse": 0.1, "test_mape": 1.0}
                for i in range(1, 4)
            ])
            report = WalkForwardReport(tmpdir).load()
            assert "GOOGL" in report.format_text()

    def test_export_writes_both_files(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            self._write_fold_csv(tmpdir, "TSLA", [
                {"fold": 1, "test_mse": 0.05, "test_rmse": 0.22, "test_mape": 1.5}
            ])
            report = WalkForwardReport(tmpdir).load()
            txt, csv = report.export(tmpdir)
            assert os.path.exists(txt)
            assert os.path.exists(csv)

    def test_symbol_from_path(self):
        assert _symbol_from_path("/some/dir/wf_AAPL.csv") == "AAPL"
        assert _symbol_from_path("wf_MSFT.csv") == "MSFT"
