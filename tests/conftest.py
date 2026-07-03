"""Pytest configuration: adds repo root and research sub-packages to sys.path so
test modules can import production code without install."""
import sys
import os

_REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
_RESEARCH = os.path.join(_REPO_ROOT, "research")
_FEATURES = os.path.join(_RESEARCH, "features")
_TRANSFORMER = os.path.join(_RESEARCH, "transformer")

for _path in (_REPO_ROOT, _RESEARCH, _FEATURES, _TRANSFORMER):
    if _path not in sys.path:
        sys.path.insert(0, _path)


import pytest  # noqa: E402


@pytest.fixture(autouse=True)
def _rate_limiter_disabled():
    """Keep the per-IP rate limiter out of unrelated API tests.

    The slowapi limiter counts every request from the TestClient's single
    fake IP, so a full-suite run would trip the 10/minute backtest limit.
    Tests that exercise rate limiting re-enable it explicitly; counters are
    reset on teardown either way.
    """
    try:
        from research.api.rate_limit import limiter
    except ImportError:  # slowapi not installed in this environment
        yield
        return
    limiter.enabled = False
    yield
    limiter.enabled = False
    limiter.reset()
