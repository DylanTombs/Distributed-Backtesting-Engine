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

        # Per-run isolation (7.3): stray project-root files are never read —
        # the binary ran in a fresh temp dir that produced nothing.
        assert (env.project / "ml_equity.csv").read_text() == stale

    def test_stale_equity_values_never_reach_the_response(self, env):
        """A fresh run with pre-existing stale files returns only fresh data."""
        (env.project / "ml_equity.csv").write_text(
            "timestamp,equity\n2020-03-02,999999\n"
        )

        result = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")

        equities = [p.equity for p in result.equity]
        assert 999999.0 not in equities
        assert equities[0] == 100000.0


# ---------------------------------------------------------------------------
# P2-1: client-input failures raise BacktestInputError with sanitised messages
# ---------------------------------------------------------------------------

class TestErrorTaxonomy:
    def test_empty_window_raises_input_error(self, env):
        with pytest.raises(runner.BacktestInputError, match="No data for"):
            runner.run_backtest(["AAPL"], "1980-01-01", "1980-02-01")

    def test_no_feature_csvs_message_contains_no_paths(self, env, tmp_path):
        (env.data / "AAPL_features.csv").unlink()
        with pytest.raises(runner.BacktestInputError) as exc_info:
            runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")
        assert str(tmp_path) not in str(exc_info.value)
        assert "pipeline" not in str(exc_info.value)


# ---------------------------------------------------------------------------
# P2-3: warm-up distinguishes binary-missing from per-event failures
# ---------------------------------------------------------------------------

class TestWarmupLogging:
    def test_missing_binary_skips_warmup_with_single_warning(
        self, env, monkeypatch, caplog
    ):
        import logging

        monkeypatch.setattr(runner, "BINARY", env.project / "does_not_exist")
        with caplog.at_level(logging.WARNING, logger="research.api.runner"):
            runner.warmup_cache()
        assert "skipping pre-warm" in caplog.text


# ---------------------------------------------------------------------------
# P2-6: archived run directories are pruned after the TTL
# ---------------------------------------------------------------------------

class TestRunArchivePruning:
    def test_expired_run_dirs_are_pruned_fresh_ones_kept(self, env):
        import time as time_mod

        runs_root = env.output / "runs"
        old = runs_root / "20200101_000000"
        fresh = runs_root / "20990101_000000"
        old.mkdir(parents=True)
        fresh.mkdir(parents=True)
        expired = time_mod.time() - (runner._RUNS_TTL_DAYS + 1) * 86_400
        os.utime(old, (expired, expired))

        runner._prune_old_runs(runs_root)

        assert not old.exists()
        assert fresh.exists()

    def test_backtest_run_triggers_pruning(self, env):
        import time as time_mod

        runs_root = env.output / "runs"
        old = runs_root / "20200101_000000"
        old.mkdir(parents=True)
        expired = time_mod.time() - (runner._RUNS_TTL_DAYS + 1) * 86_400
        os.utime(old, (expired, expired))

        runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")

        assert not old.exists()


# ---------------------------------------------------------------------------
# ADR-028: unknown-ticker fallback surfaces a warning, exact match does not
# ---------------------------------------------------------------------------

class TestSymbolFallback:
    def test_unknown_ticker_falls_back_with_warning(self, env):
        result = runner.run_backtest(["ZZZZ"], "2020-03-02", "2020-03-05")
        assert result.warning is not None
        assert "ZZZZ" in result.warning
        assert "AAPL" in result.warning

    def test_exact_match_has_no_warning(self, env):
        result = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")
        assert result.warning is None


# ---------------------------------------------------------------------------
# Cache behaviour through the real execution path
# ---------------------------------------------------------------------------

class TestCacheHit:
    def test_second_request_is_cached_and_a_fresh_object(self, env):
        first = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")
        second = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")

        assert first.cached is False
        assert second.cached is True
        # Revalidated copy, not the same mutable object (audit: verified-safe
        # pattern — this pins it)
        assert second is not first
        assert [p.equity for p in second.equity] == [p.equity for p in first.equity]

    def test_ticker_order_hits_same_cache_entry(self, env):
        """ADR-037: cache key sorts tickers."""
        (env.data / "MSFT_features.csv").write_text(_FEATURE_CSV)
        first = runner.run_backtest(["MSFT", "AAPL"], "2020-03-02", "2020-03-05")
        second = runner.run_backtest(["AAPL", "MSFT"], "2020-03-02", "2020-03-05")
        assert first.cached is False
        assert second.cached is True


# ---------------------------------------------------------------------------
# CSV reader edge cases
# ---------------------------------------------------------------------------

class TestCsvReaders:
    def test_unparseable_equity_timestamps_are_skipped(self, env):
        _write_binary(env, """#!/bin/sh
printf 'timestamp,equity\\nnot-a-date,50\\n2020-03-03,101000\\n' > ml_equity.csv
printf 'timestamp,direction,profit\\n' > ml_trades.csv
exit 0
""")
        result = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")
        assert [p.equity for p in result.equity] == [101000.0]

    def test_trades_capped_at_500_rows(self, env):
        rows = "".join(
            f"2020-03-03,SELL,{i}\\n" for i in range(600)
        )
        _write_binary(env, f"""#!/bin/sh
printf 'timestamp,equity\\n2020-03-03,101000\\n' > ml_equity.csv
printf 'timestamp,direction,profit\\n{rows}' > ml_trades.csv
exit 0
""")
        result = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")
        assert len(result.trades) == 500


# ---------------------------------------------------------------------------
# Phase 7.3: per-run directories — concurrency isolation, unique run_ids,
# bounded parallelism
# ---------------------------------------------------------------------------

_PER_SYMBOL_BINARY = """#!/bin/sh
sleep 0.2
case "$2" in
  AAPL) v=111111 ;;
  MSFT) v=222222 ;;
  *)    v=999 ;;
esac
printf 'timestamp,equity\\n2020-03-03,%s\\n' "$v" > ml_equity.csv
printf 'timestamp,direction,profit\\n' > ml_trades.csv
exit 0
"""


class TestPerRunIsolation:
    def test_concurrent_runs_do_not_bleed_outputs(self, env):
        """Two overlapping runs each get their own symbol's results."""
        import threading

        (env.data / "MSFT_features.csv").write_text(_FEATURE_CSV)
        _write_binary(env, _PER_SYMBOL_BINARY)

        results: dict[str, object] = {}
        errors: list[Exception] = []

        def run(symbol: str) -> None:
            try:
                results[symbol] = runner.run_backtest(
                    [symbol], "2020-03-02", "2020-03-05"
                )
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        threads = [threading.Thread(target=run, args=(s,)) for s in ("AAPL", "MSFT")]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert errors == []
        assert results["AAPL"].equity[0].equity == 111111.0
        assert results["MSFT"].equity[0].equity == 222222.0

    def test_run_ids_are_unique_within_the_same_second(self, env):
        """P1-9: the UUID suffix prevents same-second run_id collisions."""
        r1 = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-04")
        r2 = runner.run_backtest(["AAPL"], "2020-03-02", "2020-03-05")
        assert r1.run_id != r2.run_id

    def test_semaphore_bounds_concurrent_binary_invocations(
        self, env, monkeypatch, tmp_path
    ):
        """P1-10: with one run slot, binary invocations never overlap."""
        import threading

        marker_dir = tmp_path / "markers"
        marker_dir.mkdir()
        monkeypatch.setenv("MARKER_DIR", str(marker_dir))
        monkeypatch.setattr(runner, "_run_slots", threading.BoundedSemaphore(1))

        (env.data / "MSFT_features.csv").write_text(_FEATURE_CSV)
        _write_binary(env, """#!/bin/sh
if [ -f "$MARKER_DIR/running" ]; then touch "$MARKER_DIR/overlap"; fi
touch "$MARKER_DIR/running"
sleep 0.2
rm -f "$MARKER_DIR/running"
printf 'timestamp,equity\\n2020-03-03,101000\\n' > ml_equity.csv
printf 'timestamp,direction,profit\\n' > ml_trades.csv
exit 0
""")

        threads = [
            threading.Thread(
                target=lambda s=s: runner.run_backtest([s], "2020-03-02", "2020-03-05")
            )
            for s in ("AAPL", "MSFT")
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)

        assert not (marker_dir / "overlap").exists()
