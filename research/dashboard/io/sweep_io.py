"""
sweep_io.py — read Optuna sweep results and best config for the dashboard.

Falls back gracefully when Optuna is not installed or the storage doesn't exist.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd
import yaml


def load_best_config(models_dir: str = "models") -> dict:
    """Load the best hyperparameter config from models/best_config.yaml.

    Returns an empty dict if the file doesn't exist.
    """
    path = Path(models_dir) / "best_config.yaml"
    if not path.exists():
        return {}
    with open(path) as f:
        return yaml.safe_load(f) or {}


def load_optuna_trials(
    storage_url: str,
    study_name: str,
) -> Optional[pd.DataFrame]:
    """Load all Optuna trial results as a DataFrame.

    Returns None if optuna is not installed or the study doesn't exist.

    Columns: number, value, state, duration_s, plus one column per param.
    """
    try:
        import optuna  # noqa: F401 — optional dependency

        optuna.logging.set_verbosity(optuna.logging.WARNING)
        study = optuna.load_study(study_name=study_name, storage=storage_url)
        rows = []
        for t in study.trials:
            row = {
                "number": t.number,
                "value": t.value,
                "state": t.state.name,
                "duration_s": (
                    (t.datetime_complete - t.datetime_start).total_seconds()
                    if t.datetime_complete and t.datetime_start
                    else None
                ),
            }
            row.update(t.params)
            rows.append(row)
        return pd.DataFrame(rows)
    except Exception:
        return None


def get_sweep_storage_url(models_dir: str = "models") -> Optional[str]:
    """Read the Optuna storage URL from models/sweep_config.yaml, if present."""
    path = Path(models_dir) / "sweep_config.yaml"
    if not path.exists():
        return None
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("storage")


def get_sweep_study_name(models_dir: str = "models") -> str:
    path = Path(models_dir) / "sweep_config.yaml"
    if not path.exists():
        return "trading_transformer"
    with open(path) as f:
        cfg = yaml.safe_load(f) or {}
    return cfg.get("study_name", "trading_transformer")
