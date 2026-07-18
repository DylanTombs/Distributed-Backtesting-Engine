"""Speed-budget regression tests (Phase 8.5).

Budgets are enforced with generous CI headroom — the point is catching
order-of-magnitude regressions (an accidental O(n²), an unbounded retry),
not micro-benchmarking. The engine budget in PHASE_8.md is 250 ms for a
1-year daily window; the assertion allows 4× for slow CI machines.
"""
from __future__ import annotations

import subprocess
import time
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RULES_BINARY = PROJECT_ROOT / "backtester" / "backtest"
AAPL_CSV = PROJECT_ROOT / "backtester" / "data" / "AAPL.csv"

_ENGINE_BUDGET_S = 1.0   # PHASE_8.md budget 250 ms × 4 CI headroom

pytestmark = pytest.mark.timing


@pytest.mark.skipif(
    not (RULES_BINARY.exists() and AAPL_CSV.exists()),
    reason="pre-built backtest binary or AAPL data not present",
)
def test_engine_executes_full_history_within_budget(tmp_path):
    """The real engine runs the FULL AAPL history (~2 800 bars, >10 years)
    under the 1-year budget — an order-of-magnitude safety margin."""
    spec = tmp_path / "spec.txt"
    spec.write_text(
        "version: 1\n"
        "name: ma_cross\n"
        "entry: SMA:10 crosses_above SMA:50\n"
        "exit: SMA:10 crosses_below SMA:50\n"
    )

    t0 = time.monotonic()
    proc = subprocess.run(
        [str(RULES_BINARY), str(AAPL_CSV), "AAPL", str(spec), str(tmp_path)],
        capture_output=True,
        text=True,
        timeout=30,
    )
    elapsed = time.monotonic() - t0

    assert proc.returncode == 0, proc.stderr[-400:]
    assert (tmp_path / "ml_equity.csv").exists()
    assert elapsed < _ENGINE_BUDGET_S, (
        f"engine took {elapsed:.2f}s for the full history — "
        f"budget is {_ENGINE_BUDGET_S:.1f}s (PHASE_8.md: 250 ms/year)"
    )


def test_runner_overhead_is_bounded(tmp_path, monkeypatch):
    """Python-side overhead (filtering, spec write, archive, metrics) must
    stay well inside the 3 s cached-path budget; the binary is a no-op stub
    so only runner overhead is measured."""
    import research.api.runner as runner
    from research.data import fetcher

    data = tmp_path / "data"
    output = tmp_path / "output"
    data.mkdir()
    output.mkdir()

    rows = [
        f"2020-{(i // 28) + 1:02d}-{(i % 28) + 1:02d},1,1,1,{100 + i},1"
        for i in range(260)
    ]
    (data / "AAPL.csv").write_text(
        "date,open,high,low,close,volume\n" + "\n".join(sorted(rows)) + "\n"
    )

    stub = tmp_path / "backtest"
    stub.write_text(
        "#!/bin/sh\n"
        "printf 'timestamp,equity\\n2020-06-01,100000\\n' > ml_equity.csv\n"
        "printf 'timestamp,direction,profit\\n' > ml_trades.csv\n"
    )
    stub.chmod(0o755)

    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)
    monkeypatch.setattr(runner, "OUTPUT_DIR", output)
    monkeypatch.setattr(runner, "RULES_BINARY", stub)
    monkeypatch.setattr(runner, "_cache", runner._LRUCache(runner._LRU_MAX))
    monkeypatch.setattr(fetcher, "OHLCV_DIR", tmp_path / "ohlcv")
    monkeypatch.setattr(fetcher, "LEGACY_DATA_DIR", data)

    t0 = time.monotonic()
    result = runner.run_backtest(["AAPL"], "2020-02-01", "2020-09-01")
    elapsed = time.monotonic() - t0

    assert result.run_id
    assert elapsed < 1.0, f"runner overhead {elapsed:.2f}s exceeds budget"
