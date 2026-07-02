"""Branch-coverage tests for research/api/runner.py using a fake binary.

These tests exercise the real runner code paths (no mocking of run_backtest):
a small shell script stands in for the compiled ml_backtest binary, and all
module-level path constants are monkeypatched into a tmp_path sandbox.

runner.py is excluded from coverage measurement via .coveragerc, but these
tests are mandatory regression coverage for the audit fixes.
"""
from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

import research.api.runner as runner

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

_FEATURE_CSV = (
    "timestamp,close\n"
    "2020-03-02,100.0\n"
    "2020-03-03,101.0\n"
    "2020-03-04,102.0\n"
    "2020-03-05,103.0\n"
)

_GOOD_BINARY = """#!/bin/sh
printf 'timestamp,equity\\n2020-03-02,100000\\n2020-03-03,101000\\n2020-03-04,102000\\n' > ml_equity.csv
printf 'timestamp,direction,profit\\n2020-03-03,SELL,500\\n2020-03-04,SELL,-200\\n' > ml_trades.csv
exit 0
"""

_SILENT_BINARY = """#!/bin/sh
exit 0
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Sandboxed runner environment with a fresh cache and a working binary."""
    project = tmp_path / "project"
    data = tmp_path / "data"
    output = tmp_path / "output"
    bindir = tmp_path / "bin"
    for d in (project, data, output, bindir):
        d.mkdir()

    binary = bindir / "ml_backtest"
    binary.write_text(_GOOD_BINARY)
    binary.chmod(0o755)

    (data / "AAPL_features.csv").write_text(_FEATURE_CSV)

    monkeypatch.setattr(runner, "PROJECT_ROOT", project)
    monkeypatch.setattr(runner, "DATA_DIR", data)
    monkeypatch.setattr(runner, "OUTPUT_DIR", output)
    monkeypatch.setattr(runner, "MODEL_DIR", tmp_path / "models")
    monkeypatch.setattr(runner, "BINARY", binary)
    monkeypatch.setattr(runner, "_cache", runner._LRUCache(runner._LRU_MAX))

    return SimpleNamespace(
        project=project, data=data, output=output, binary=binary
    )


def _write_binary(env, script: str) -> None:
    env.binary.write_text(script)
    env.binary.chmod(0o755)


# ---------------------------------------------------------------------------
# P0-1: stale fixed-path outputs must never be returned as fresh results
# ---------------------------------------------------------------------------

class TestStaleOutputs:
    def test_silent_binary_raises_instead_of_returning_stale_result(self, env):
        """Binary exits 0 without writing output: RuntimeError, not stale data."""
        stale = "timestamp,equity\n2019-01-01,999999\n"
        (env.project / "ml_equity.csv").write_text(stale)
        (env.project / "ml_trades.csv").write_text(
            "timestamp,direction,profit\n2019-01-01,SELL,42\n"
        )
        _write_binary(env, _SILENT_BINARY)

        with pytest.raises(RuntimeError, match="produced no output"):
            runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")

        # The stale files were unlinked before the run — nothing to re-read
        assert not (env.project / "ml_equity.csv").exists()
        assert not (env.project / "ml_trades.csv").exists()

    def test_stale_equity_values_never_reach_the_response(self, env):
        """A fresh run with pre-existing stale files returns only fresh data."""
        (env.project / "ml_equity.csv").write_text(
            "timestamp,equity\n2020-03-02,999999\n"
        )

        result = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")

        equities = [p.equity for p in result.equity]
        assert 999999.0 not in equities
        assert equities[0] == 100000.0
