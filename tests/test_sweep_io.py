"""Tests for research/dashboard/io/sweep_io.py."""

import sys
import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest
import yaml

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from research.dashboard.io.sweep_io import (
    load_best_config,
    load_optuna_trials,
    get_sweep_storage_url,
    get_sweep_study_name,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _write_yaml(path: Path, data: dict) -> None:
    with open(path, "w") as f:
        yaml.dump(data, f)


# ---------------------------------------------------------------------------
# load_best_config
# ---------------------------------------------------------------------------

class TestLoadBestConfig:
    def test_returns_empty_dict_when_file_missing(self, tmp_path):
        result = load_best_config(str(tmp_path))
        assert result == {}

    def test_returns_config_dict_when_file_exists(self, tmp_path):
        _write_yaml(tmp_path / "best_config.yaml", {"d_model": 128, "n_heads": 4})
        result = load_best_config(str(tmp_path))
        assert result["d_model"] == 128
        assert result["n_heads"] == 4

    def test_returns_empty_dict_for_empty_file(self, tmp_path):
        (tmp_path / "best_config.yaml").write_text("")
        result = load_best_config(str(tmp_path))
        assert result == {}

    def test_reads_all_keys(self, tmp_path):
        cfg = {"d_model": 256, "n_heads": 8, "dropout": 0.1, "val_mse": 1.23}
        _write_yaml(tmp_path / "best_config.yaml", cfg)
        result = load_best_config(str(tmp_path))
        assert set(result.keys()) == set(cfg.keys())


# ---------------------------------------------------------------------------
# get_sweep_storage_url
# ---------------------------------------------------------------------------

class TestGetSweepStorageUrl:
    def test_returns_none_when_no_file(self, tmp_path):
        assert get_sweep_storage_url(str(tmp_path)) is None

    def test_returns_storage_from_yaml(self, tmp_path):
        _write_yaml(tmp_path / "sweep_config.yaml",
                    {"storage": "sqlite:///models/optuna_study.db", "study_name": "test"})
        result = get_sweep_storage_url(str(tmp_path))
        assert result == "sqlite:///models/optuna_study.db"

    def test_returns_none_when_storage_key_missing(self, tmp_path):
        _write_yaml(tmp_path / "sweep_config.yaml", {"study_name": "test"})
        result = get_sweep_storage_url(str(tmp_path))
        assert result is None


# ---------------------------------------------------------------------------
# get_sweep_study_name
# ---------------------------------------------------------------------------

class TestGetSweepStudyName:
    def test_returns_default_when_no_file(self, tmp_path):
        result = get_sweep_study_name(str(tmp_path))
        assert result == "trading_transformer"

    def test_returns_name_from_yaml(self, tmp_path):
        _write_yaml(tmp_path / "sweep_config.yaml",
                    {"storage": "sqlite:///x.db", "study_name": "my_study"})
        result = get_sweep_study_name(str(tmp_path))
        assert result == "my_study"

    def test_returns_default_when_key_missing(self, tmp_path):
        _write_yaml(tmp_path / "sweep_config.yaml", {"storage": "sqlite:///x.db"})
        result = get_sweep_study_name(str(tmp_path))
        assert result == "trading_transformer"


# ---------------------------------------------------------------------------
# load_optuna_trials
# ---------------------------------------------------------------------------

class TestLoadOptunaTrials:
    def test_returns_none_when_optuna_not_available(self):
        with patch.dict(sys.modules, {"optuna": None}):
            result = load_optuna_trials("sqlite:///nonexistent.db", "study")
        assert result is None

    def test_returns_none_when_study_does_not_exist(self, tmp_path):
        optuna = pytest.importorskip("optuna")
        db = tmp_path / "study.db"
        result = load_optuna_trials(f"sqlite:///{db}", "nonexistent_study")
        assert result is None

    def test_returns_dataframe_with_trial_columns(self, tmp_path):
        optuna = pytest.importorskip("optuna")
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        db_url = f"sqlite:///{tmp_path / 'study.db'}"
        study = optuna.create_study(
            direction="minimize",
            study_name="test_study",
            storage=db_url,
        )

        def objective(trial):
            x = trial.suggest_float("x", -5, 5)
            return x ** 2

        study.optimize(objective, n_trials=3)

        result = load_optuna_trials(db_url, "test_study")
        assert result is not None
        assert isinstance(result, pd.DataFrame)
        assert "number" in result.columns
        assert "value" in result.columns
        assert "state" in result.columns
        assert len(result) == 3

    def test_includes_pruned_trials(self, tmp_path):
        optuna = pytest.importorskip("optuna")
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        db_url = f"sqlite:///{tmp_path / 'study.db'}"
        study = optuna.create_study(
            direction="minimize",
            study_name="prune_study",
            storage=db_url,
        )

        def objective(trial):
            trial.suggest_float("x", -5, 5)
            raise optuna.TrialPruned()

        study.optimize(objective, n_trials=2, catch=(optuna.TrialPruned,))

        result = load_optuna_trials(db_url, "prune_study")
        assert result is not None
        assert len(result) == 2

    def test_trial_params_appear_as_columns(self, tmp_path):
        optuna = pytest.importorskip("optuna")
        optuna.logging.set_verbosity(optuna.logging.WARNING)
        db_url = f"sqlite:///{tmp_path / 'study.db'}"
        study = optuna.create_study(
            direction="minimize",
            study_name="param_study",
            storage=db_url,
        )

        def objective(trial):
            a = trial.suggest_float("alpha", 0, 1)
            b = trial.suggest_int("beta", 1, 10)
            return a + b

        study.optimize(objective, n_trials=2)

        result = load_optuna_trials(db_url, "param_study")
        assert "alpha" in result.columns
        assert "beta" in result.columns
