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
    """Clear everything the script keeps between calls: tracked remuxes, the
    stop and owner-warning flags, and log handlers added by main()."""
    ssd._active_procs.clear()
    ssd._cancelled.clear()
    ssd._chown_warned.clear()
    for handler in ssd.log.handlers:
        handler.close()
    ssd.log.handlers.clear()
    ssd.log.propagate = True


@pytest.fixture(autouse=True)
def clean_module_state():
    """Reset the script's state before and after every test, and put back
    the Ctrl+C and SIGTERM handlers that main() replaces."""
    original_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    _reset()
    yield
    _reset()
    for s, handler in original_handlers.items():
        signal.signal(s, handler)
