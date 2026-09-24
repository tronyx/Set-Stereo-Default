"""Shared pytest setup: makes set_stereo_default.py importable from the repo
root, and resets the module's global state around every test so one test's
logging, signal handler or tracked subprocesses can't leak into the next."""

import signal
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import set_stereo_default as ssd


def _reset():
    ssd._active_procs.clear()
    ssd._cancelled.clear()
    ssd.log.handlers.clear()
    ssd.log.propagate = True


@pytest.fixture(autouse=True)
def clean_module_state():
    original_sigint = signal.getsignal(signal.SIGINT)
    _reset()
    yield
    _reset()
    signal.signal(signal.SIGINT, original_sigint)
