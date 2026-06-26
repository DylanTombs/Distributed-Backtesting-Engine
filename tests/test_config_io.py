"""Tests for research/dashboard/io/config_io.py."""

import os
import sys
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from research.dashboard.io.config_io import load_config, save_config, diff_configs


SAMPLE_YAML = """\
# Capital
initial_cash: 100000.0
risk_fraction: 0.10

# Signal
buy_threshold: 0.005
allow_short: false
"""


# ---------------------------------------------------------------------------
# load_config
# ---------------------------------------------------------------------------

def test_load_config_returns_dict(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML)
    cfg = load_config(str(p))
    assert isinstance(cfg, dict)


def test_load_config_reads_float_values(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML)
    cfg = load_config(str(p))
    assert abs(cfg["initial_cash"] - 100000.0) < 1e-9
    assert abs(cfg["risk_fraction"] - 0.10) < 1e-9


def test_load_config_reads_bool_values(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML)
    cfg = load_config(str(p))
    assert cfg["allow_short"] is False


def test_load_config_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_config(str(tmp_path / "no_such.yaml"))


# ---------------------------------------------------------------------------
# save_config
# ---------------------------------------------------------------------------

def test_save_config_round_trips_values(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML)
    cfg = load_config(str(p))
    cfg["risk_fraction"] = 0.15
    save_config(cfg, str(p))
    reloaded = load_config(str(p))
    assert abs(reloaded["risk_fraction"] - 0.15) < 1e-9


def test_save_config_preserves_other_keys(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML)
    save_config({"risk_fraction": 0.20}, str(p))
    reloaded = load_config(str(p))
    assert abs(reloaded["initial_cash"] - 100000.0) < 1e-9


def test_save_config_creates_file_if_missing(tmp_path):
    p = tmp_path / "new_config.yaml"
    save_config({"my_key": 42}, str(p))
    assert p.exists()


def test_save_config_updates_bool(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(SAMPLE_YAML)
    save_config({"allow_short": True}, str(p))
    reloaded = load_config(str(p))
    assert reloaded["allow_short"] is True


# ---------------------------------------------------------------------------
# diff_configs
# ---------------------------------------------------------------------------

def test_diff_configs_empty_when_identical():
    cfg = {"a": 1, "b": 2}
    assert diff_configs(cfg, cfg) == []


def test_diff_configs_detects_changed_value():
    diffs = diff_configs({"a": 1}, {"a": 2})
    assert len(diffs) == 1
    field, old, new = diffs[0]
    assert field == "a"
    assert old == 1
    assert new == 2


def test_diff_configs_detects_added_key():
    diffs = diff_configs({}, {"new_key": 99})
    assert any(d[0] == "new_key" for d in diffs)


def test_diff_configs_detects_removed_key():
    diffs = diff_configs({"gone": 1}, {})
    assert any(d[0] == "gone" for d in diffs)


def test_diff_configs_multiple_changes():
    old = {"a": 1, "b": 2, "c": 3}
    new = {"a": 1, "b": 99, "d": 4}
    diffs = diff_configs(old, new)
    keys = {d[0] for d in diffs}
    assert "b" in keys   # changed
    assert "c" in keys   # removed
    assert "d" in keys   # added
    assert "a" not in keys  # unchanged
