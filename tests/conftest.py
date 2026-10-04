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
    stop and owner-warning flags, which file printed last, and log handlers
    added by main()."""
    ssd._active_procs.clear()
    ssd._cancelled.clear()
    ssd._ownership_failures.clear()
    ssd._last_header[0] = None
    ssd._file_context.header = None
    for handler in ssd.log.handlers:
        handler.close()
    ssd.log.handlers.clear()
    ssd.log.propagate = True


@pytest.fixture(autouse=True)
def clean_module_state(monkeypatch):
    """Reset the script's state before and after every test, and put back
    the Ctrl+C and SIGTERM handlers that main() replaces. Tests run as if
    outside the Docker image, even when they run inside it, and each one
    checks mkvmerge's options afresh."""
    monkeypatch.delenv(ssd.IN_DOCKER_VAR, raising=False)
    ssd.mkvmerge_can_keep_legacy_font_types.cache_clear()
    original_handlers = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    _reset()
    yield
    _reset()
    for s, handler in original_handlers.items():
        signal.signal(s, handler)
