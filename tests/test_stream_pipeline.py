"""Tests for the stream_pipeline generator used in Page 4."""

import os
import sys
import subprocess
import pytest

# Import the generator directly from the page module without triggering Streamlit
# by loading it as a plain Python file via importlib.
import importlib.util

_PAGE_PATH = os.path.join(
    os.path.dirname(__file__), "..", "research", "dashboard", "pages", "4_Trigger_Backtest.py"
)


def _load_stream_pipeline():
    """Import stream_pipeline without executing Streamlit page-level code."""
    spec = importlib.util.spec_from_file_location("page4", _PAGE_PATH)
    # We only want the function, not the page UI — mock streamlit before load
    import unittest.mock as mock
    with mock.patch.dict("sys.modules", {"streamlit": mock.MagicMock()}):
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    return mod.stream_pipeline


# ---------------------------------------------------------------------------
# stream_pipeline
# ---------------------------------------------------------------------------

def test_stream_pipeline_yields_stdout_lines():
    stream_pipeline = _load_stream_pipeline()
    cmd = [sys.executable, "-c", "print('hello'); print('world')"]
    lines = list(stream_pipeline(cmd))
    assert any("hello" in l for l in lines)
    assert any("world" in l for l in lines)


def test_stream_pipeline_lines_are_strings():
    stream_pipeline = _load_stream_pipeline()
    cmd = [sys.executable, "-c", "print('test line')"]
    lines = list(stream_pipeline(cmd))
    for line in lines:
        assert isinstance(line, str)


def test_stream_pipeline_raises_on_nonzero_exit():
    stream_pipeline = _load_stream_pipeline()
    cmd = [sys.executable, "-c", "import sys; sys.exit(1)"]
    with pytest.raises(RuntimeError, match="code 1"):
        list(stream_pipeline(cmd))


def test_stream_pipeline_captures_stderr_too():
    stream_pipeline = _load_stream_pipeline()
    # stderr goes to stdout via stderr=STDOUT
    cmd = [sys.executable, "-c", "import sys; print('err', file=sys.stderr)"]
    lines = list(stream_pipeline(cmd))
    assert any("err" in l for l in lines)


def test_stream_pipeline_empty_output_succeeds():
    stream_pipeline = _load_stream_pipeline()
    cmd = [sys.executable, "-c", "pass"]
    lines = list(stream_pipeline(cmd))
    assert lines == []
