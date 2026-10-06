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
    """Clear everything the script keeps between calls: what main() clears
    at the start of each run (see _reset_run_state()), plus tracked remuxes
    and the log handlers main() added. Records go back to propagating, so
    caplog sees them."""
    ssd._reset_run_state()
    ssd._active_procs.clear()
    for handler in ssd.log.handlers:
        handler.close()
    ssd.log.handlers.clear()
    ssd.log.propagate = True


@pytest.fixture(autouse=True)
def clean_module_state(monkeypatch):
    """Reset the script's state before and after every test, and put back
    the Ctrl+C and SIGTERM handlers that main() replaces. Tests run as if
    outside the Docker image, even when they run inside it, and each one
    checks mkvmerge's options afresh. Whatever the test patched is put back
    before the reset afterwards, since the reset uses the script's own
    functions."""
    monkeypatch.delenv(ssd.IN_DOCKER_VAR, raising=False)
    original_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    _reset()
    yield
    monkeypatch.undo()
    _reset()
    for s, handler in original_handlers.items():
        signal.signal(s, handler)
