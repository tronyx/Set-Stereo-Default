#!/usr/bin/env python3
"""
set_stereo_default.py -- make the stereo audio track play by default.

Many video files flag a 5.1 or 7.1 track as the default audio, which sounds
thin or quiet on TV speakers, soundbars and laptops. This script finds the
2-channel (stereo) track in each file and makes it the default instead,
clearing the flag from every other audio track. Nothing is re-encoded.

Requirements:
  - Python 3.10 or newer
  - ffmpeg and ffprobe 4.4 or newer    https://ffmpeg.org
  - mkvmerge, part of MKVToolNix       https://mkvtoolnix.download
    (only needed for .mkv/.webm files)
  - tqdm, optional (pip install tqdm) for progress bars
  ffmpeg, ffprobe and mkvmerge must be on your PATH.

Which track it picks:
  The stereo track in the same language as the track that plays by default
  now, so a stereo dub never replaces the original language. Commentary and
  audio-description tracks are never picked. If no track fits, or several
  do, the file is skipped. --prefer-lang picks a language to use whenever
  there's a stereo track in it, and breaks ties; files without one are
  handled as usual.

How it changes each file:
  - .mkv/.webm       remuxed with mkvmerge
  - .mp4/.m4v/.mov   remuxed with ffmpeg, keeping the index at the front of
                     the file
  - .avi             AVI has no default flag, so --avi-reorder moves the
                     stereo track first instead; without it, AVI files are
                     skipped
  A remux copies the streams as-is into a new file. It's used instead of
  editing the file in place because in-place edits can break Windows
  Explorer thumbnails.

Safe by default:
  - Files that are already right are left alone.
  - --dry-run shows what would change without touching anything.
  - Each new file is checked before it replaces the original: every
    stream still there with the same codec, language, name and flags,
    the right track default, and no shorter than the original. --backup
    also keeps the original as <name>.bak, never deleting an existing
    backup unless you say so.
  - A file that another program replaced or edited during its remux is
    left as it is now, rather than overwritten with the remux of the old
    version; the next run fixes the new one.
  - Ctrl+C or SIGTERM (docker stop, kill) stops cleanly and removes any
    half-written temp files.
  - A symlinked file is fixed through its link: the file it points to is
    changed and the link keeps working. --skip-symlinks skips them
    instead, and --follow-symlinks also searches symlinked subfolders.

Examples:
  Preview every change without touching anything:
    python3 set_stereo_default.py /path/to/videos --dry-run

  Fix one folder, keeping each original as <name>.bak:
    python3 set_stereo_default.py "/path/to/videos/Some Show" --backup

  Fix a whole library, 4 files at a time, with the details in a log file:
    python3 set_stereo_default.py /path/to/videos --jobs 4 --log-file run.log

  Use the English stereo track wherever there is one, whatever language
  plays by default now:
    python3 set_stereo_default.py /path/to/videos --prefer-lang en

  Work through a list of folders, one per line, in the list's order (see
  --input-file below for how lines can be written):
    python3 set_stereo_default.py --input-file shows.txt --dry-run

  Just these files, or only .mkv files and not in subfolders:
    python3 set_stereo_default.py file1.mkv file2.mp4
    python3 set_stereo_default.py /path/to/videos --ext mkv --no-recursive

In the Docker image, mount your videos at /videos, which is searched unless
you name other paths, and put the options after the image name, e.g. to
preview every change:
    docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --dry-run

Exit codes: 0 all done, 1 a file had an error, no files matched or a tool is
missing, 2 invalid options, 130 stopped by Ctrl+C, 143 stopped by SIGTERM.

Full guide: https://github.com/tronyx/Set-Stereo-Default/blob/master/docs/README.md
"""

from __future__ import annotations

import argparse
import codecs
import contextlib
import copy
import dataclasses
import functools
import importlib
import io
import json
import logging
import os
import re
import shlex
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import threading
import time
from collections import Counter, deque
from collections.abc import Callable, Generator, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType, ModuleType
from typing import Any, Literal, NoReturn

try:
    from tqdm import tqdm
    HAVE_TQDM = True
except ImportError:
    HAVE_TQDM = False


def _in_docker() -> bool:
    """True inside the project's Docker image. There, paths default to
    DOCKER_VIDEOS, and advice shows docker commands instead of ones for
    running the script directly."""
    return os.environ.get(IN_DOCKER_VAR) == "1"


def _optional_module(name: str) -> ModuleType | None:
    """Import a module that only some systems have, or return None."""
    try:
        return importlib.import_module(name)
    except ImportError:
        return None


grp = _optional_module("grp")
"""Group names (Linux and macOS only; None on Windows)."""

pwd = _optional_module("pwd")
"""User names (Linux and macOS only; None on Windows)."""

DEFAULT_EXTS = {".mkv", ".webm", ".mp4", ".m4v", ".mov", ".avi"}
"""Extensions processed when --ext isn't given."""

MKV_EXTS = {".mkv", ".webm"}
"""Remuxed with mkvmerge. Everything else is remuxed with ffmpeg."""

AVI_EXTS = {".avi"}
"""No default-track flag exists; only handled with --avi-reorder."""

MOV_FASTSTART_EXTS = {".mp4", ".m4v", ".mov"}
"""Remuxed with -movflags +faststart, keeping the index at the front of the file."""

IN_DOCKER_VAR = "SET_STEREO_DEFAULT_IN_DOCKER"
"""Set to "1" by the project's Docker image (see _in_docker())."""

DOCKER_VIDEOS = "/videos"
"""Where the Docker image expects your videos to be mounted, and what it
searches when no path is given."""

TMP_MARKER = ".tmp_remux"
"""Marks a file's temp copy while it's remuxed, e.g. "movie.mkv.tmp_remux.mkv"."""

COMMENTARY_NAME_RE = re.compile(r"commentary|audio[ -]?description|descriptive|described|\bdvs\b",
                                re.IGNORECASE)
"""Track names that mark commentary or audio description, as such tracks are
really named: "Director's Commentary", "Audio Description", "Descriptive
Video Service", "Described Video", "DVS". A bare "description" is too loose
to count."""

LEGACY_FONT_MIME_TYPES = frozenset({"application/x-truetype-font", "application/vnd.ms-opentype",
                                    "application/x-font-ttf", "application/x-font-otf"})
"""Older MIME types for font attachments, which newer mkvmerge versions
rewrite to font/ttf and font/otf unless told not to (see apply_mkv())."""

NAME_TAGS = ("title", "name", "handler_name")
"""Tags that can hold a track's name: MKV uses "title", and ffprobe reports
MP4 names as "name" or "handler_name"."""

LANGUAGE_ALIASES = {
    "aa": "aar", "ab": "abk", "ae": "ave", "af": "afr", "ak": "aka", "alb": "sqi", "am": "amh",
    "an": "arg", "ar": "ara", "arm": "hye", "as": "asm", "av": "ava", "ay": "aym", "az": "aze",
    "ba": "bak", "baq": "eus", "be": "bel", "bg": "bul", "bi": "bis", "bm": "bam", "bn": "ben",
    "bo": "bod", "br": "bre", "bs": "bos", "bur": "mya", "ca": "cat", "ce": "che", "ch": "cha",
    "chi": "zho", "co": "cos", "cr": "cre", "cs": "ces", "cu": "chu", "cv": "chv", "cy": "cym",
    "cze": "ces", "da": "dan", "de": "deu", "dut": "nld", "dv": "div", "dz": "dzo", "ee": "ewe",
    "el": "ell", "en": "eng", "eo": "epo", "es": "spa", "et": "est", "eu": "eus", "fa": "fas",
    "ff": "ful", "fi": "fin", "fj": "fij", "fo": "fao", "fr": "fra", "fre": "fra", "fy": "fry",
    "ga": "gle", "gd": "gla", "geo": "kat", "ger": "deu", "gl": "glg", "gn": "grn", "gre": "ell",
    "gu": "guj", "gv": "glv", "ha": "hau", "he": "heb", "hi": "hin", "ho": "hmo", "hr": "hrv",
    "ht": "hat", "hu": "hun", "hy": "hye", "hz": "her", "ia": "ina", "ice": "isl", "id": "ind",
    "ie": "ile", "ig": "ibo", "ii": "iii", "ik": "ipk", "io": "ido", "is": "isl", "it": "ita",
    "iu": "iku", "ja": "jpn", "jv": "jav", "ka": "kat", "kg": "kon", "ki": "kik", "kj": "kua",
    "kk": "kaz", "kl": "kal", "km": "khm", "kn": "kan", "ko": "kor", "kr": "kau", "ks": "kas",
    "ku": "kur", "kv": "kom", "kw": "cor", "ky": "kir", "la": "lat", "lb": "ltz", "lg": "lug",
    "li": "lim", "ln": "lin", "lo": "lao", "lt": "lit", "lu": "lub", "lv": "lav", "mac": "mkd",
    "mao": "mri", "may": "msa", "mg": "mlg", "mh": "mah", "mi": "mri", "mk": "mkd", "ml": "mal",
    "mn": "mon", "mr": "mar", "ms": "msa", "mt": "mlt", "my": "mya", "na": "nau", "nb": "nob",
    "nd": "nde", "ne": "nep", "ng": "ndo", "nl": "nld", "nn": "nno", "no": "nor", "nr": "nbl",
    "nv": "nav", "ny": "nya", "oc": "oci", "oj": "oji", "om": "orm", "or": "ori", "os": "oss",
    "pa": "pan", "per": "fas", "pi": "pli", "pl": "pol", "ps": "pus", "pt": "por", "qu": "que",
    "rm": "roh", "rn": "run", "ro": "ron", "ru": "rus", "rum": "ron", "rw": "kin", "sa": "san",
    "sc": "srd", "sd": "snd", "se": "sme", "sg": "sag", "si": "sin", "sk": "slk", "sl": "slv",
    "slo": "slk", "sm": "smo", "sn": "sna", "so": "som", "sq": "sqi", "sr": "srp", "ss": "ssw",
    "st": "sot", "su": "sun", "sv": "swe", "sw": "swa", "ta": "tam", "te": "tel", "tg": "tgk",
    "th": "tha", "ti": "tir", "tib": "bod", "tk": "tuk", "tl": "tgl", "tn": "tsn", "to": "ton",
    "tr": "tur", "ts": "tso", "tt": "tat", "tw": "twi", "ty": "tah", "ug": "uig", "uk": "ukr",
    "ur": "urd", "uz": "uzb", "ve": "ven", "vi": "vie", "vo": "vol", "wa": "wln", "wel": "cym",
    "wo": "wol", "xh": "xho", "yi": "yid", "yo": "yor", "za": "zha", "zh": "zho", "zu": "zul",
}
"""Language codes mapped to the ISO 639-2/T code for the same language: every
two-letter ISO 639-1 code ("de" -> "deu"), and the 20 ISO 639-2/B codes that
differ from their /T code ("ger" -> "deu"). MKV files store /B codes and MP4
files often /T codes, so the same language can be tagged either way.
Generated from the ISO 639-2 code list (datasets/language-codes on GitHub)."""

MAX_DURATION_LOSS = 0.01
"""How much shorter a remux may be than the original, as a fraction of the
original's duration, before it's rejected. A normal remux changes the
duration by milliseconds; a file whose header claims more than it contains
(e.g. an incomplete download) comes out much shorter."""

MIN_DURATION_LOSS = 1.0
"""The least duration loss, in seconds, that rejects a remux, so short clips
aren't rejected over a few milliseconds of normal drift."""

log = logging.getLogger("set_stereo_default")


class TqdmLoggingHandler(logging.Handler):
    """Routes log messages through tqdm.write() so they don't clobber an
    active progress bar."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            tqdm.write(self.format(record))
        except Exception:
            self.handleError(record)


def _escape_bytes(error: UnicodeError) -> tuple[str, int]:
    r"""How the console and the log file write text they can't encode (see
    setup_logging()): each character they can't becomes an escape. A
    surrogate escape (U+DC80 to U+DCFF), which is how a byte that isn't
    valid UTF-8 in a Linux file name reaches Python, becomes that byte's
    escape, e.g. "\xe9", so the name reads as the file system has it;
    anything else becomes its code point's, as backslashreplace gives."""
    if not isinstance(error, UnicodeEncodeError):
        raise error
    escaped = "".join(f"\\x{ord(c) - 0xDC00:02x}" if 0xDC80 <= ord(c) <= 0xDCFF
                      else c.encode("ascii", "backslashreplace").decode("ascii")
                      for c in error.object[error.start:error.end])
    return escaped, error.end


codecs.register_error("escapebytes", _escape_bytes)


def setup_logging(log_file: str | None) -> None:
    r"""Send log messages to the console, or with --log-file to that file.
    With a log file, warnings and errors still reach the console too, so a
    failed run (e.g. a missing tool) never ends without saying why. Handlers
    from an earlier call are closed and replaced, so a second run in the
    same process doesn't print every line twice.

    The console (both streams, since the progress bars go to the second)
    and the log file write a character they can't encode as an escape, e.g.
    caf\xe9.mkv (see _escape_bytes()), rather than failing. A Linux file
    name that isn't valid UTF-8 reaches the script as surrogate escapes,
    which a strict UTF-8 console, the default in most locales, would
    otherwise refuse, turning each of that file's lines into a logging
    error and leaving them out of the log file."""
    for old in log.handlers[:]:
        log.removeHandler(old)
        old.close()
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="escapebytes")
    log.setLevel(logging.INFO)
    log.propagate = False
    console = TqdmLoggingHandler() if HAVE_TQDM else logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    if log_file:
        handler = logging.FileHandler(log_file, mode="a", encoding="utf-8", errors="escapebytes")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)
        console.setLevel(logging.WARNING)
    log.addHandler(console)


_file_context = threading.local()
"""Per thread: the "[i/N] path" header of the file it's working on, or None
outside file_context()."""

_last_header: dict[logging.Handler, str] = {}
"""For each place lines go (the console, the log file), the header of the
file whose line it showed most recently. Kept for each separately, since
they don't show the same lines: with --log-file the console shows only
warnings and errors, so a file's first line, often its decision, reaches
the log file but not the console."""

_print_lock = threading.Lock()
"""Makes choosing whether to repeat a header and printing the line one step,
so another file's line can't land in between."""


class _FileHeaderFilter(logging.Filter):
    """Puts each file's "[i/N] path" header above its lines: before its first
    line, and again whenever another file has printed since its last one.
    This is the only place headers are printed, so the code that logs a
    file's lines never has to.

    The repeats matter with --jobs > 1, where several files are worked on
    at once and each logs at several moments (its decision, then later its
    command, warnings or result), so other files' lines land in between.
    Lines still appear as they happen.

    Each place lines go gets the header before the first of the file's
    lines it shows, judged for itself (see _last_header). With --log-file
    the console shows only warnings and errors, so a file's first line, its
    decision, reaches the log file alone; the header with it mustn't count
    for the console, or a warning about that file would reach it with no
    header to say which file it is.

    The check and the printing must happen as one step, or two files
    printing at the same moment could still mix. The filter runs before
    anything is printed, so it takes the lock, adds the header where
    needed, hands the line to the handlers itself, and returns False so it
    isn't printed twice. Lines logged outside file_context() pass straight
    through, as do lines no handler would show, which logging then deals
    with itself."""

    def filter(self, record: logging.LogRecord) -> bool:
        header = getattr(_file_context, "header", None)
        if header is None:
            return True
        handlers = _handlers_for(record)
        if not handlers:
            return True
        with _print_lock:
            for handler in handlers:
                if _last_header.get(handler) == header:
                    handler.handle(record)
                    continue
                shown = copy.copy(record)
                shown.msg, shown.args = f"\n{header}\n{record.getMessage()}", None
                handler.handle(shown)
                _last_header[handler] = header
        return False


def _handlers_for(record: logging.LogRecord) -> list[logging.Handler]:
    """The handlers that will show record: log's own, then its parents', as
    long as each passes lines on, leaving out those set to a higher level.
    The same ones, in the same order, as logging.Logger.callHandlers()."""
    found: list[logging.Handler] = []
    logger: logging.Logger | None = log
    while logger is not None:
        found += [h for h in logger.handlers if record.levelno >= h.level]
        if not logger.propagate:
            break
        logger = logger.parent
    return found


log.addFilter(_FileHeaderFilter())


@contextlib.contextmanager
def file_context(header: str) -> Generator[None, None, None]:
    """Mark this thread as working on the file with this "[i/N] path" header
    until the block ends, so its lines are kept under that header (see
    _FileHeaderFilter)."""
    _file_context.header = header
    try:
        yield
    finally:
        _file_context.header = None


_active_procs: set[subprocess.Popen[str]] = set()
"""Running mkvmerge/ffmpeg remuxes, so a stop can kill them."""

_active_procs_lock = threading.RLock()
"""Guards _active_procs. Reentrant because the stop handler takes it in the
main thread, which may be the thread it interrupted while holding it."""

_cancelled = threading.Event()
"""Set once Ctrl+C or SIGTERM arrives, so files that haven't started are skipped."""

_requested_stop: list[int] = [signal.SIGINT]
"""The signal of the last stop request, for _check_stop()."""

_ownership_failures: list[tuple[str, tuple[int, int], tuple[int, int], str]] = []
"""Remuxed files that couldn't be given their original owner, as (full path,
wanted owner, actual owner, reason). Reported once, at the end of the run,
by report_ownership_failures()."""

_ownership_lock = threading.Lock()
"""Guards _ownership_failures, which several --jobs threads add to at once."""

Failure = Literal["damaged", "in use", "changed", "other"]
"""Why a file couldn't be fixed, as report_failed_files() groups them."""

FAILURE_GROUPS: dict[Failure, str] = {
    "damaged": ("Damaged or incomplete downloads. Replace each (e.g. search for it again in Sonarr "
                "or Radarr), then run the script again:"),
    "in use": ("Open in another program, or read-only, so the new version couldn't replace it. "
               "Close the program (a player, or a media server scanning it) or make the file "
               "writable, then run the script again:"),
    "changed": ("Changed by another program during the run, so left as it is now. Run the script "
                "again to fix the new version:"),
    "other": "Something else went wrong; the line about each file above says what:",
}
"""The heading report_failed_files() gives each kind of failure, saying what
to do about it, in the order they're listed."""

_failed_files: dict[str, Failure] = {}
"""Full path of each file this run couldn't fix, and why (see
_note_failure()). Listed again at the end of the run by
report_failed_files()."""

_failed_lock = threading.Lock()
"""Guards _failed_files, which several --jobs threads add to at once."""


def _reset_run_state() -> None:
    """Forget what an earlier run in this process left behind, so main() can
    run more than once, e.g. when called from other Python code: a stop
    request (which would cancel every file), files that couldn't keep their
    owner and files it couldn't fix (which would be reported again), the
    headers printed last, and whether this mkvmerge can keep legacy font
    types. No remux outlives its run, so _active_procs is already empty."""
    _cancelled.clear()
    with _ownership_lock:
        _ownership_failures.clear()
    with _failed_lock:
        _failed_files.clear()
    _last_header.clear()
    _file_context.header = None
    mkvmerge_can_keep_legacy_font_types.cache_clear()


def _terminate_active_procs() -> None:
    """Stop every running remux: terminate() first, then kill() anything
    still running after 5 seconds. The 5 seconds is shared, not per
    process, so stopping never takes longer however many --jobs run."""
    with _active_procs_lock:
        procs = list(_active_procs)
    for proc in procs:
        with contextlib.suppress(Exception):
            proc.terminate()
    deadline = time.monotonic() + 5
    for proc in procs:
        try:
            proc.wait(timeout=max(0, deadline - time.monotonic()))
        except Exception:
            with contextlib.suppress(Exception):
                proc.kill()


class Stopped(KeyboardInterrupt):
    """Raised by _stop_handler(); signum says which signal arrived. It's a
    KeyboardInterrupt, so everything that cleans up after Ctrl+C also
    cleans up after SIGTERM."""

    def __init__(self, signum: int) -> None:
        super().__init__()
        self.signum = signum


def _stop_handler(signum: int, frame: FrameType | None) -> NoReturn:
    """Handle Ctrl+C (SIGINT) and SIGTERM: kill every running remux, then
    raise Stopped in the main thread.

    The remuxes have to be killed here for two reasons. Python raises the
    exception only in the main thread, so with --jobs > 1 a worker waiting
    on its remux would never notice it. And SIGTERM (docker stop, kill,
    systemd) reaches only this process, not the remuxes it started.

    The request is recorded before the exception is raised, since the
    exception can be lost (see _check_stop())."""
    _requested_stop[0] = signum
    _cancelled.set()
    _terminate_active_procs()
    raise Stopped(signum)


def _stop_reason(exc: BaseException) -> tuple[int, str]:
    """Return (signal number, message) for a stop: a Stopped from
    _stop_handler(), or a plain KeyboardInterrupt, which counts as Ctrl+C."""
    signum = getattr(exc, "signum", signal.SIGINT)
    if signum == signal.SIGINT:
        return signum, "Interrupted by user (Ctrl+C)"
    return signum, f"Stopped by {signal.Signals(signum).name}"


def _check_stop() -> None:
    """Raise Stopped if a stop was requested but its exception was lost.

    _stop_handler() raises Stopped wherever the main thread happens to be,
    and Python drops an exception raised while a __del__ runs (a finished
    tool's Popen being collected, say) or a generator is closed, reporting
    it through sys.unraisablehook instead of raising it. The run would
    carry on as if nothing had happened, with the remaining files quietly
    cancelled. The request itself survives in _cancelled, so _run() asks
    here after each phase, and _unraisable() keeps the dropped exception
    quiet."""
    if _cancelled.is_set():
        raise Stopped(_requested_stop[0])


def _unraisable(unraisable: sys.UnraisableHookArgs,
                fallback: Callable[[sys.UnraisableHookArgs], object]) -> None:
    """sys.unraisablehook while main() runs: a Stopped that Python couldn't
    raise is dropped without a word, since _check_stop() acts on the
    request anyway; anything else goes to fallback, the hook that was
    there before."""
    if not isinstance(unraisable.exc_value, Stopped):
        fallback(unraisable)


class Cancelled(Exception):
    """Raised by run() when a stop (Ctrl+C, SIGTERM) killed the command it
    was waiting for. process_file() reports the file as cancelled, rather
    than reporting the killed command as a failure."""


def run(cmd: list[str]) -> subprocess.CompletedProcess[str]:
    """Run a quick command (e.g. ffprobe) and capture its output. Remuxes use
    run_with_progress() instead.

    Like a remux, the process is listed in _active_procs while it runs, so a
    stop kills it rather than waiting for it (e.g. ffprobe on a network
    share that has stopped answering), and it's killed straight away if a
    stop was already requested. If a stop is requested while it runs,
    Cancelled is raised once it has ended.

    Output is read as UTF-8, which ffprobe, ffmpeg and mkvmerge (given
    --output-charset UTF-8) all write, rather than in the system's own
    encoding: on Windows that's usually cp1252, which garbles non-English
    track names or fails on them outright. A byte that isn't valid UTF-8
    is replaced rather than stopping the run."""
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                            encoding="utf-8", errors="replace")
    try:
        with _active_procs_lock:
            _active_procs.add(proc)
        if _cancelled.is_set():
            proc.kill()
        stdout, stderr = proc.communicate()
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        with _active_procs_lock:
            _active_procs.discard(proc)
    if _cancelled.is_set():
        raise Cancelled
    return subprocess.CompletedProcess(cmd, proc.returncode, stdout, stderr)


def run_with_progress(cmd: list[str], label: str, parse_pct: Callable[[str], int | None],
                      progress: Progress | None = None) -> tuple[int, str]:
    """Run a remux, reading its output as it arrives so progress can be shown
    live. Returns (returncode, output), where output is the last 50 lines
    that aren't progress updates: that's where warnings and errors end up.

    parse_pct(line) returns 0-100 for a progress line and None for anything
    else. With progress.show, a tqdm bar titled label shows this file's
    progress on the top row, above process_all()'s overall bar.
    progress.on_progress(pct), if set, is called on every increase and with
    100 on success; process_all() uses it to move the overall bar. Output
    is read as UTF-8, as in run().

    The process is listed in _active_procs while it runs and is killed if
    anything goes wrong, so it's never left running on its own. If a stop
    was already requested, it's killed straight away. It's listed before
    that check, and the stop handler sets _cancelled before reading the
    list, so a remux starting at the same moment as a stop can't slip
    through."""
    progress = progress or Progress()
    bar = None
    last_pct = 0
    lines: deque[str] = deque(maxlen=50)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             encoding="utf-8", errors="replace", bufsize=1)
    stdout = proc.stdout
    assert stdout is not None
    try:
        with _active_procs_lock:
            _active_procs.add(proc)
        if _cancelled.is_set():
            proc.kill()
        if progress.show and HAVE_TQDM:
            bar = tqdm(total=100, desc=f"  {label}"[:40], unit="%", leave=False, position=0)
        for line in stdout:
            pct = parse_pct(line)
            if pct is None:
                lines.append(line)
            else:
                pct = max(0, min(100, pct))
                if pct > last_pct:
                    if bar:
                        bar.update(pct - last_pct)
                    last_pct = pct
                    if progress.on_progress:
                        progress.on_progress(pct)
        proc.wait()
        if last_pct < 100 and proc.returncode == 0:
            if bar:
                bar.update(100 - last_pct)
            if progress.on_progress:
                progress.on_progress(100)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        stdout.close()
        with _active_procs_lock:
            _active_procs.discard(proc)
        if bar:
            bar.close()
    return proc.returncode, "".join(lines)


def check_tools(need_mkvmerge: bool) -> bool:
    """True if ffmpeg and ffprobe are on PATH, and mkvmerge too when there are
    .mkv/.webm files to process. Otherwise logs what's missing, with
    install links, and returns False."""
    missing = [tool for tool in ("ffmpeg", "ffprobe") if shutil.which(tool) is None]
    if need_mkvmerge and shutil.which("mkvmerge") is None:
        missing.append("mkvmerge (install MKVToolNix)")
    if missing:
        log.error("Missing required tool(s): " + ", ".join(missing))
        log.error("Install ffmpeg (https://ffmpeg.org) and, for .mkv/.webm files, "
                  "MKVToolNix (https://mkvtoolnix.download), then re-run.")
    return not missing


@dataclass(frozen=True)
class Stream:
    """One stream in a file, as probe_streams() reads it: its index (as
    ffprobe numbers streams), type ("audio", "video", "subtitle", ...),
    codec, channel count (None if not audio), language tag ("" if none),
    names (from NAME_TAGS), its default, commentary and audio-description
    flags, every other flag it has (flags, e.g. {"forced",
    "hearing_impaired"}, as ffprobe names them; never "default"), its MIME
    type ("" if none; attachments, such as fonts, have one), and its codec
    tag (tag, e.g. "avc1"; "" if none). Frozen, so streams can be shared
    freely."""
    index: int
    type: str = ""
    codec: str = ""
    channels: int | None = None
    default: bool = False
    comment: bool = False
    visual_impaired: bool = False
    language: str = ""
    names: tuple[str, ...] = ()
    flags: frozenset[str] = frozenset()
    mimetype: str = ""
    tag: str = ""


def _stream_info(raw: dict[str, Any]) -> Stream:
    """One stream from ffprobe's JSON (raw) as a Stream."""
    tags = raw.get("tags", {}) or {}
    disposition = raw.get("disposition", {}) or {}
    return Stream(
        index=raw["index"],
        type=raw.get("codec_type", ""),
        codec=raw.get("codec_name", ""),
        channels=raw.get("channels"),
        default=bool(disposition.get("default", 0)),
        comment=bool(disposition.get("comment", 0)),
        visual_impaired=bool(disposition.get("visual_impaired", 0)),
        language=tags.get("language", ""),
        names=tuple(tags[k] for k in NAME_TAGS if tags.get(k)),
        flags=frozenset(k for k, on in disposition.items() if on and k != "default"),
        mimetype=tags.get("mimetype", ""),
        tag=raw.get("codec_tag_string", ""),
    )


def probe_streams(path: Path, report: bool = True) -> tuple[list[Stream] | None, float | None]:
    """Return (streams, duration in seconds) for path: every stream, as a
    Stream, and the duration (None if unknown). Returns
    (None, None) if ffprobe can't read the file, after logging why unless
    report is False.

    One call gives everything a file needs: the audio streams to choose
    from, the duration for ffmpeg's progress bar, and the full stream list
    verify_remux() compares the remux against, so the original is only
    probed once."""
    res = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", str(path),
    ])
    if res.returncode != 0:
        if report:
            log.error(f"  ffprobe failed on {Path(path).name}: {res.stderr.strip()}")
        return None, None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        if report:
            log.error(f"  Could not parse ffprobe output for {Path(path).name}")
        return None, None

    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return [_stream_info(s) for s in data.get("streams", [])], duration


def is_commentary(stream: Stream) -> bool:
    """True for a commentary or audio-description track. These are often
    stereo but should never play by default. They're recognized by the
    file's own flags, or failing that by the track's name."""
    return (stream.comment or stream.visual_impaired
            or any(COMMENTARY_NAME_RE.search(n) for n in stream.names))


def is_chapter_track(stream: Stream) -> bool:
    """True for an MP4 or MOV chapter track: a text track holding the
    chapter titles, which ffprobe lists as a data stream. ffmpeg writes a
    new one from the file's chapter list on every remux, so apply_remux()
    leaves the original's out, and the post-remux check takes the new one
    in its place (see verify_remux())."""
    return stream.type == "data" and stream.tag in ("text", "tx3g")


def normalize_language(code: str | None) -> str:
    """Return code in one standard form, so different tags for the same
    language compare equal: lowercased, with any region or script part
    dropped ("pt-BR" -> "pt") and any spaces around it removed, then
    mapped to its ISO 639-2/T code through LANGUAGE_ALIASES ("de" and
    "ger" -> "deu"). Codes that aren't in the table come back lowercased;
    an empty tag stays empty."""
    base = re.split(r"[-_]", (code or "").lower(), maxsplit=1)[0].strip()
    return LANGUAGE_ALIASES.get(base, base)


def choose_target(streams: list[Stream],
                  prefer_lang: str | None) -> tuple[Stream | None, str | None]:
    """Return (stream, note): the stereo track to make default, or None and a
    note saying why the file should be skipped. When a track is returned,
    note is None, unless prefer_lang matched nothing and the track was
    picked the usual way instead; then note says so.

    Commentary and audio-description tracks are never picked. The track
    must be in the language of the track players start on now (the
    default one, or the first if none is flagged), so a stereo dub never
    replaces the original language. prefer_lang is tried before that: a
    stereo track in it wins, but if there's none the usual rule still
    applies, so the option never leaves a file alone that a run without it
    would fix. It does skip a file with several stereo tracks in that
    language, rather than falling back to another language. A track with no
    language tag (or "und") matches any language, but a track tagged with
    the wanted one wins over it. Codes are compared after
    normalize_language(), so "de", "ger" and "deu" all mean German."""
    def describe(ss: list[Stream]) -> str:
        """List tracks for a skip note, e.g. "stream#2 (eng/aac), stream#3 (spa/ac3)"."""
        return ", ".join(f"stream#{s.index} ({s.language or 'und'}/{s.codec})" for s in ss)

    def in_language(lang: str | None) -> list[Stream]:
        """The candidates in lang or with no language tag, narrowed to the
        one tagged with lang when there's exactly one; every candidate when
        lang itself is unset."""
        wanted = normalize_language(lang)
        if wanted in ("", "und"):
            return candidates
        matches = [s for s in candidates if normalize_language(s.language) in ("", "und", wanted)]
        exact = [s for s in matches if normalize_language(s.language) == wanted]
        return exact if len(exact) == 1 else matches

    stereo = [s for s in streams if s.channels == 2]
    candidates = [s for s in stereo if not is_commentary(s)]
    if not candidates:
        if stereo:
            return None, f"only 2-channel tracks are commentary/audio description [{describe(stereo)}]"
        return None, "no 2-channel audio track found"

    current = next((s for s in streams if s.default), streams[0])
    tried = [prefer_lang or current.language]
    picks = in_language(tried[0])
    fell_back = (not picks and prefer_lang is not None
                 and normalize_language(prefer_lang) != normalize_language(current.language))
    if fell_back:
        picks = in_language(current.language)
        if normalize_language(current.language) not in ("", "und"):
            tried.append(current.language)
    missing = f"no 2-channel track in '{prefer_lang}', " if fell_back else ""

    if not picks:
        languages = " or ".join(f"'{t}'" for t in tried)
        return None, (f"no 2-channel track in {languages} [found {describe(candidates)}] "
                      f"-- use --prefer-lang to pick another language")
    if len(picks) == 1:
        return picks[0], (f"{missing}so picked as if --prefer-lang wasn't given" if fell_back else None)
    return None, (
        f"{missing}multiple 2-channel tracks found [{describe(picks)}] -- use --prefer-lang to disambiguate"
    )


def needs_change(streams: list[Stream], target_index: int) -> bool:
    """True unless the target is already the only audio track flagged default."""
    return any((s.index == target_index) != s.default for s in streams)


def _stream_loss(was: Stream, now: Stream) -> str | None:
    """What a remux changed about one stream (was, before; now, after), as a
    short reason, or None if nothing it must keep. Its type, codec and
    channel count must match, a known language mustn't change, and every
    name it had must still be there. Flags are checked by
    _streams_problem(), since whether losing one is acceptable depends on
    the container.

    Only losses count. A tool may add a name (ffmpeg names MP4 tracks
    "SoundHandler" when they have none) or fill in "und" for a missing
    language, which loses nothing."""
    def shape(s: Stream) -> str:
        """e.g. "audio eac3 6ch" or "subtitle subrip"."""
        return " ".join(str(part) for part in (s.type, s.codec, f"{s.channels}ch" if s.channels else "")
                        if part)

    if (was.type, was.codec, was.channels) != (now.type, now.codec, now.channels):
        return f"stream#{was.index} changed from {shape(was)} to {shape(now)}"
    old, new = normalize_language(was.language), normalize_language(now.language)
    if old not in ("", "und") and new not in ("", "und") and old != new:
        return f"stream#{was.index}'s language changed from {was.language} to {now.language}"
    gone = [name for name in was.names if name not in now.names]
    if gone:
        return f"stream#{was.index} lost its name {gone[0]!r}"
    return None


def _content_problem(plan: Plan, after: list[Stream]) -> str | None:
    """Whether the remux (after) lost any stream outright: it must have as
    many streams, chapter tracks (see is_chapter_track()) and audio tracks
    as the original."""
    before = plan.layout
    if len(after) != len(before):
        return f"stream count changed from {len(before)} to {len(after)}"
    had, has = (sum(1 for s in streams if is_chapter_track(s)) for streams in (before, after))
    if has != had:
        return f"chapter track count changed from {had} to {has}"
    audio = [s for s in after if s.type == "audio"]
    if len(audio) != len(plan.streams):
        return f"expected {len(plan.streams)} audio tracks, found {len(audio)}"
    return None


def _clock(seconds: float) -> str:
    """seconds the way a player shows a time: "9:41", or "1:02:05" from an
    hour on. Partial seconds are dropped, so a time is never overstated."""
    whole = int(seconds)
    hours, minutes, secs = whole // 3600, whole // 60 % 60, whole % 60
    return f"{hours}:{minutes:02}:{secs:02}" if hours else f"{minutes}:{secs:02}"


def _damage_problem(plan: Plan, after_duration: float | None) -> str | None:
    """Whether the remux came out shorter than the original by more than
    MAX_DURATION_LOSS (and at least MIN_DURATION_LOSS seconds), as a
    message for the person who has to deal with it, or None. The tools copy
    all they can read, so a much shorter remux means the original holds
    less than its header claims: it's damaged, or an incomplete download
    (whose missing parts a download client may have left as zeros). That's
    a problem with the original, not the remux, and only replacing the file
    fixes it, so the message says so in plain words. A longer remux is
    fine: the original's header just understated it. The check is skipped
    if either duration is unknown."""
    before = plan.duration
    if not before or after_duration is None:
        return None
    if after_duration >= before - max(before * MAX_DURATION_LOSS, MIN_DURATION_LOSS):
        return None
    return (f"only {_clock(after_duration)} of this {_clock(before)} video could be read, so the file seems "
            f"to be damaged or an incomplete download. It was left as it is; replace it (e.g. search for "
            f"it again in Sonarr or Radarr), then run the script again")


def _first_audio_problem(plan: Plan, audio: list[Stream]) -> str | None:
    """After an AVI reorder, whether the target failed to end up as the
    first audio track (audio, the remux's audio tracks). AVI has no default
    flag to check, so the first track must match the target's codec,
    channel count and language (compared after normalize_language(), as
    everywhere else, and only when both are known: a tool may drop a
    language or fill one in, as in _stream_loss())."""
    target, first = plan.target, audio[0]
    wanted, got = normalize_language(target.language), normalize_language(first.language)
    if (first.codec != target.codec or first.channels != target.channels
            or (wanted not in ("", "und") and got not in ("", "und") and got != wanted)):
        return "target audio track didn't end up first"
    return None


def _expected_order(plan: Plan) -> list[Stream]:
    """The original's streams in the order the remux should have them,
    except chapter tracks: ffmpeg writes those afresh (see
    is_chapter_track()), with a name, language and place of its own. A
    remux keeps the order (mkvmerge is told to; see apply_mkv()), except
    that an AVI reorder (plan.reordered) writes apply_remux()'s order:
    video, the target, the other audio tracks, subtitles, data, then
    anything else."""
    before = [s for s in plan.layout if not is_chapter_track(s)]
    if not plan.reordered:
        return before
    of_type = {t: [s for s in before if s.type == t] for t in ("video", "audio", "subtitle", "data")}
    moved = next(s for s in of_type["audio"] if s.index == plan.target_index)
    return (of_type["video"] + [moved] + [s for s in of_type["audio"] if s is not moved]
            + of_type["subtitle"] + of_type["data"] + [s for s in before if s.type not in of_type])


def _streams_problem(plan: Plan, order: list[Stream],
                     after: list[Stream]) -> tuple[str | None, tuple[str, ...]]:
    """Whether a stream failed to come through the remux as it was: each of
    order (see _expected_order()) against the remux's stream in the same
    place, by _stream_loss() and then by its flags (forced, commentary,
    hearing impaired, ...). Returns (problem, notes): the first problem
    found, or None and the warnings to log if the remux is used.

    In MKV files every flag must survive: mkvmerge 54 and newer keep every
    one, but 52 and older drop the commentary, audio-description,
    hearing-impaired and original-language flags, which players use to
    label and choose tracks. ffmpeg can't write any of these flags to MP4,
    MOV or AVI files, so a flag lost there is unavoidable: it becomes a
    note, and the remux is still used."""
    is_mkv = plan.path.suffix.lower() in MKV_EXTS
    notes: list[str] = []
    for was, now in zip(order, after, strict=True):
        loss = _stream_loss(was, now)
        if loss:
            return loss, ()
        lost = sorted(was.flags - now.flags)
        if not lost:
            continue
        what = f"stream#{was.index} lost its {', '.join(lost)} flag{'s' if len(lost) > 1 else ''}"
        if is_mkv:
            return f"{what}; mkvmerge 52 and older drop it, so update MKVToolNix to 54 or newer", ()
        notes.append(f"    {plan.path.name}: {what}, which ffmpeg can't write to "
                     f"{plan.path.suffix.lower()} files")
    return None, tuple(notes)


def _default_flag_problem(plan: Plan, audio: list[Stream]) -> str | None:
    """Whether anything but the target is flagged default among the remux's
    audio tracks (audio). A remux keeps audio tracks in order, so the
    target is found by its position among them."""
    expected = audio[plan.streams.index(plan.target)].index
    defaults = [s.index for s in audio if s.default]
    if defaults != [expected]:
        found = ", ".join(f"stream#{i}" for i in defaults) or "no track"
        return f"default flag is on {found}, expected only stream#{expected}"
    return None


@dataclass(frozen=True)
class Verification:
    """verify_remux()'s result: problem, why the remux must be rejected (None
    if it passed), and notes, warnings to log if the remux is then used (a
    flag ffmpeg can't write; see _streams_problem()). A rejected remux
    never carries notes, since they'd describe a change that isn't made.
    damaged marks a problem with the original rather than the remux (see
    _damage_problem()), whose message is complete as it is."""
    problem: str | None = None
    notes: tuple[str, ...] = ()
    damaged: bool = False


def verify_remux(plan: Plan) -> Verification:
    """Check the finished remux at plan.tmp_path before it replaces the
    original. Returns a Verification: no problem if it looks right,
    otherwise a short reason why not. The original isn't probed again:
    plan.layout and plan.duration already describe it.

    Warnings worth giving if the remux is used come back as notes rather
    than being logged, since a later check can still reject the remux.
    check_and_swap_in() logs them once the remux has replaced the original.

    The checks run in this order, each only once the ones before it have
    passed, and the first problem found is the one reported:
    - the remux isn't much shorter than the original, which would mean the
      original is damaged (_damage_problem()); first, since if it is, that
      explains anything else that's wrong, and only replacing it helps;
    - nothing lost outright: streams, chapter tracks, audio tracks
      (_content_problem());
    - after an AVI reorder (plan.reordered), the target is the first audio
      track (_first_audio_problem());
    - every stream but a chapter track came through as it was, in the
      expected order (_expected_order(), _streams_problem());
    - except after an AVI reorder, which has no default flag, the target
      is the only audio track flagged default (_default_flag_problem())."""
    after, after_duration = probe_streams(plan.tmp_path, report=False)
    if after is None:
        return Verification("ffprobe couldn't read the file")
    damage = _damage_problem(plan, after_duration)
    if damage:
        return Verification(damage, damaged=True)
    audio = [s for s in after if s.type == "audio"]
    notes: tuple[str, ...] = ()
    problem = _content_problem(plan, after)
    if problem is None and plan.reordered:
        problem = _first_audio_problem(plan, audio)
    if problem is None:
        problem, notes = _streams_problem(plan, _expected_order(plan),
                                          [s for s in after if not is_chapter_track(s)])
    if problem is None and not plan.reordered:
        problem = _default_flag_problem(plan, audio)
    return Verification(problem, notes if problem is None else ())


def backup_path(path: Path, replace: bool) -> Path:
    """Where to keep path's original: <name>.bak, unless that already exists
    and replace is false, in which case the first free <name>.bak.1,
    <name>.bak.2, ... so an earlier backup is never lost."""
    bak_path = path.with_name(path.name + ".bak")
    if replace or not bak_path.exists():
        return bak_path
    n = 1
    while path.with_name(f"{path.name}.bak.{n}").exists():
        n += 1
    return path.with_name(f"{path.name}.bak.{n}")


def make_backup(path: Path, replace: bool = False) -> Path:
    """Keep the original at backup_path() and return that path. A hard link
    is instant and needs no room while the remux runs; once the new file
    replaces the original, the backup holds the original's data on its
    own, so it takes the original's full size until it's deleted. Where
    hard links aren't supported, a full copy is made instead.

    The backup is made under a staging name, <name>.bak.tmp_remux.<ext>,
    and renamed into place in one step once it's complete. So a copy that
    fails partway (a full disk, a dropped share) never leaves a partial
    backup that looks whole, nor removes the <name>.bak it was replacing.
    The staging file is removed if anything goes wrong, and if the run is
    killed outright, the next run reports it as a leftover temp file. It's
    also removed after the rename: renaming it over a <name>.bak that is
    already a hard link to the file (left by a run stopped just after its
    backup) does nothing, but succeeds, so it would otherwise stay behind.

    Whatever already has the staging name is removed first, and the copy
    only ever writes a new file (see _copy_new()), so a symlink someone put
    at that name is never written through."""
    bak_path = backup_path(path, replace)
    staging = bak_path.with_name(bak_path.name + TMP_MARKER + path.suffix)
    staging.unlink(missing_ok=True)
    try:
        try:
            os.link(path, staging)
        except OSError:
            _copy_new(path, staging)
        os.replace(staging, bak_path)
        staging.unlink(missing_ok=True)
    except BaseException:
        staging.unlink(missing_ok=True)
        raise
    return bak_path


def _copy_new(src: Path, dst: Path) -> None:
    """Copy src to dst, with its permissions and dates, like shutil.copy2(),
    except that dst must not exist yet: copy2() opens whatever is there,
    following a symlink to wherever it points, while this fails instead."""
    with open(src, "rb") as source, open(dst, "xb") as target:
        shutil.copyfileobj(source, target, 1024 * 1024)
    shutil.copystat(src, dst)


def _owner(path: Path) -> tuple[int, int]:
    """Return path's owner as (user ID, group ID)."""
    st = os.stat(path)
    return st.st_uid, st.st_gid


def _user_name(uid: int) -> str | None:
    """The name of user ID uid, or None if it has none here (or on Windows)."""
    if pwd is None:
        return None
    try:
        return str(pwd.getpwuid(uid).pw_name)
    except KeyError:
        return None


def _group_name(gid: int) -> str | None:
    """The name of group ID gid, or None if it has none here (or on Windows)."""
    if grp is None:
        return None
    try:
        return str(grp.getgrgid(gid).gr_name)
    except KeyError:
        return None


def _owner_name(uid: int, gid: int) -> str:
    """Describe an owner as "tronyx:users (1000:100)", or as just "1000:100"
    if either ID has no name on this system."""
    user, group = _user_name(uid), _group_name(gid)
    return f"{user}:{group} ({uid}:{gid})" if user and group else f"{uid}:{gid}"


def copy_ownership(src: Path, dst: Path) -> None:
    """Give dst the same permissions and owner as src. A remux creates a new
    file owned by whoever ran the script, which could lock out tools that
    share your media through a group (Sonarr, Radarr, Plex, containers).

    The owner is only changed if it differs. On storage where owners can't
    be changed but every file gets the right one anyway (e.g. an NFS share
    that maps every user to the media owner), trying would fail and warn
    for nothing.

    Changing the owner needs root, and NFS shares usually turn root into
    "nobody", so it can fail. If it does, the file is noted for
    report_ownership_failures() and the run carries on; the permissions
    are copied either way."""
    shutil.copymode(src, dst)
    if not hasattr(os, "chown"):
        return
    wanted, got = _owner(src), _owner(dst)
    if wanted == got:
        return
    try:
        os.chown(dst, *wanted)
    except OSError as exc:
        with _ownership_lock:
            _ownership_failures.append((str(src), wanted, got, exc.strerror or str(exc)))


def _write_ownership_list(folder: Path | str) -> Path | None:
    """Write the full path of every file in _ownership_failures to a new
    set_stereo_default-owners-<date>-<time>-<process ID>.log in folder, one
    per line in path order (they're recorded in whatever order --jobs
    finishes them), and return its path. If the files don't all share one
    wanted and one actual owner, each line also says which. Falls back to
    the system's temp folder if folder can't be written to; returns None if
    that fails too.

    The file is always created new, never written over or through something
    already at that name: an earlier list, or a symlink someone planted in a
    shared temp folder so that a run as root would overwrite the file it
    points to. If the name is taken, the next place is tried. The time and
    process ID in the name mean a later run never finds its name taken by
    an earlier one."""
    owners = {(wanted, got) for _, wanted, got, _ in _ownership_failures}
    lines = [path if len(owners) == 1
             else f"{path}  (should belong to {_owner_name(*wanted)}, belongs to {_owner_name(*got)})"
             for path, wanted, got, _ in sorted(_ownership_failures)]
    name = f"set_stereo_default-owners-{time.strftime('%Y%m%d-%H%M%S')}-{os.getpid()}.log"
    for place in (Path(folder), Path(tempfile.gettempdir())):
        target = place.resolve() / name
        try:
            with open(target, "x", encoding="utf-8") as listing:
                listing.write("\n".join(lines) + "\n")
            return target
        except OSError:
            continue
    return None


def report_ownership_failures(folder: Path | str = ".") -> None:
    """Warn once about every remuxed file that couldn't be given its original
    owner: how many, the owner they should have and the one they got, and
    the command to run the script as the right user. With more than one,
    the full list goes to a file in folder (see _write_ownership_list())
    and the warning says where.

    It's one warning at the end of the run, just before the summary, because
    the cause (usually running as root on an NFS share) affects the whole
    run, not one file. Shown under whichever file happened to fail first,
    it looked like that file's problem.

    In the Docker image (see _in_docker()), the fix is a docker run
    --user with the owner's IDs instead of sudo: sudo isn't there, and the
    owner's name usually isn't either."""
    if not _ownership_failures:
        return
    path, wanted, got, reason = _ownership_failures[0]
    count = len(_ownership_failures)
    if _in_docker():
        how = f"the container as the files' owner instead (docker run --user {wanted[0]}:{wanted[1]} ...)"
    else:
        user = _user_name(wanted[0]) or f"'#{wanted[0]}'"
        how = f"the script as the files' owner instead (sudo -u {user} python3 ...)"
    advice = (f"Permissions were still copied. Changing a file's owner needs root, and NFS "
              f"shares usually turn root into 'nobody'. Run {how}.")
    if count == 1:
        log.warning(f"\nCouldn't give {path} its original owner ({reason}).\n\n"
                    f"It should belong to {_owner_name(*wanted)} but belongs to "
                    f"{_owner_name(*got)}. {advice}")
        return
    listed = _write_ownership_list(folder)
    where = (f"You can view the full list of files here: {listed}" if listed
             else "The list of files couldn't be saved:\n" +
                  "\n".join(p for p, _, _, _ in _ownership_failures))
    if len({(w, g) for _, w, g, _ in _ownership_failures}) == 1:
        owners = (f"These files should belong to {_owner_name(*wanted)} but belong to "
                  f"{_owner_name(*got)}.")
    else:
        owners = (f"Their owners vary (the list shows each file's); for example, "
                  f"{Path(path).name} should belong to {_owner_name(*wanted)} but belongs to "
                  f"{_owner_name(*got)}.")
    log.warning(f"\nCouldn't give {count} remuxed files their original owner ({reason}). "
                f"{where}\n\n{owners} {advice}")


def _note_failure(path: Path, why: Failure) -> None:
    """Record that path couldn't be fixed, and why, for report_failed_files().
    The first reason recorded stands, so process_file()'s catch-all "other"
    never replaces a more specific one."""
    with _failed_lock:
        _failed_files.setdefault(str(path), why)


def report_failed_files() -> None:
    """List every file this run couldn't fix, with its full path, just before
    the summary, grouped by what to do about it (see FAILURE_GROUPS). Each
    was reported as it happened, but in a run over a whole library those
    lines have long scrolled away by the end, and the summary only counts
    them as errors. Sorted within each group, so a show's episodes are
    together, rather than in the order --jobs threads finished them."""
    with _failed_lock:
        failed = dict(_failed_files)
    if not failed:
        return
    lines = [f"{len(failed)} file{'s' if len(failed) > 1 else ''} couldn't be fixed:"]
    for why, heading in FAILURE_GROUPS.items():
        paths = sorted(p for p, kind in failed.items() if kind == why)
        if paths:
            lines += ["", f"  {heading}", *(f"    {p}" for p in paths)]
    log.warning("\n" + "\n".join(lines))


class Superseded(Exception):
    """Raised by swap_in() when the original changed after it was probed: the
    remux was made from the old version, and swapping it in would undo
    whatever replaced it, such as an upgrade a media manager imported (see
    _snapshot())."""


def _changed_since(path: Path, snapshot: tuple[int, int] | None) -> bool:
    """True if path no longer matches snapshot, its _snapshot() from when it
    was probed. Never true without a snapshot to compare."""
    return snapshot is not None and _snapshot(path) != snapshot


def swap_in(path: Path, tmp_path: Path, backup: BackupMode | None,
            keep_dates: bool = False, snapshot: tuple[int, int] | None = None) -> None:
    """Replace path with the checked remux at tmp_path: keep the original as
    a backup if backup is set, copy its permissions and owner (and with
    keep_dates, its access and modification times) onto the new file, then
    swap the new file in with a single atomic rename.

    backup is None for no backup, "replace" to overwrite an existing
    <name>.bak, or "number" to number the new one if <name>.bak exists
    (see backup_path()).

    snapshot, if given, is the file's _snapshot() from when it was probed,
    and the file is checked against it twice: before the backup, so a file
    already replaced doesn't get a pointless one, and right after it, just
    before the swap. The backup comes first because without hard links it's
    a full copy, which can take minutes on a network share, and a change
    during it must be caught too. If the file changed, Superseded is raised
    with nothing touched.

    If anything stops the swap once the backup is made (the file changed,
    the rename failed because another program has the file open, a stop),
    the backup is removed again: the original is untouched, and a backup
    would only suggest it had been changed. A backup it replaced is gone,
    though. Whether the swap happened is read from the file system, since
    a stop can land right after the rename, so the backup of a file that
    was replaced is never removed.

    keep_dates is off by default because tools that spot changed files by
    size and modification time (rsync's default, some backup software)
    could skip a remux that kept both, leaving a stale copy."""
    if _changed_since(path, snapshot):
        raise Superseded
    bak_path = make_backup(path, replace=(backup == "replace")) if backup else None
    try:
        if bak_path is not None and _changed_since(path, snapshot):
            raise Superseded
        copy_ownership(path, tmp_path)
        if keep_dates:
            st = os.stat(path)
            os.utime(tmp_path, ns=(st.st_atime_ns, st.st_mtime_ns))
        if bak_path is not None and bak_path.suffix != ".bak":
            log.info(f"    {path.name}: kept the original as {bak_path.name}")
        os.replace(tmp_path, path)
    except BaseException:
        if bak_path is not None and tmp_path.exists():
            bak_path.unlink(missing_ok=True)
        raise


@dataclass
class Plan:
    """What's going to happen to one file, as decided by _process_file():
    its audio streams, the index of the one to make default (target gives
    the stream itself), its duration in seconds (None if unknown), intro,
    the "setting stream#N ..." line logged as the remux starts (see
    _announce()), layout, every stream in the file, which verify_remux()
    compares the remux against, snapshot, the file's _snapshot() from
    before it was probed, which swap_in() compares again before the swap
    (None skips that), and reordered, True for an AVI reorder, where the
    target is moved to the first audio track instead of flagged default
    (see apply_remux()). The streams, duration and layout all come from one
    probe_streams() call."""
    path: Path
    streams: list[Stream]
    target_index: int
    duration: float | None = None
    intro: str | None = None
    layout: list[Stream] = field(default_factory=list)
    snapshot: tuple[int, int] | None = None
    reordered: bool = False

    @property
    def target(self) -> Stream:
        """The audio stream to make default (or, reordered, to move first)."""
        return next(s for s in self.streams if s.index == self.target_index)

    @property
    def tmp_path(self) -> Path:
        """Where the remux is written before it replaces the original."""
        return self.path.with_name(self.path.name + TMP_MARKER + self.path.suffix)


def _snapshot(path: Path) -> tuple[int, int] | None:
    """path's size and modification time (in nanoseconds), or None if it's
    gone. If these differ by the time a remux is ready to replace the file,
    another program replaced, edited or removed it in the meantime, e.g.
    Sonarr or Radarr importing an upgrade.

    The file's inode isn't compared: some network and FUSE file systems
    don't keep it stable, which would make every file look replaced. A
    replacement or an edit changes the size or modification time anyway."""
    try:
        st = os.stat(path)
    except FileNotFoundError:
        return None
    return st.st_size, st.st_mtime_ns


@dataclass
class Progress:
    """How to show a remux's progress: show its own bar (--jobs 1 only; see
    run_with_progress()), and call on_progress(pct) to move the overall bar."""
    show: bool = False
    on_progress: Callable[[int], None] | None = None


def check_and_swap_in(plan: Plan, opts: Options, tool_warnings: str | None = None) -> bool:
    """Check a finished remux with verify_remux() and swap it in if it passes.
    Returns True if the original was replaced, False if the check failed
    (already logged). The check's warnings (see verify_remux()) are only
    logged once the remux has replaced the original, so a rejected remux
    never warns about a file it didn't change. A damaged original (see
    _damage_problem()) is logged in its own plain words, and any other
    rejected remux as a failed check. The failures with a fix of their own
    (a damaged original, a file another program has open, and one it
    changed meanwhile) are recorded with that reason (see _note_failure()),
    to be listed under what to do about them at the end of the run (see
    report_failed_files()).

    tool_warnings is the line about the remux tool's warnings (see
    _remux_and_swap()), logged before the result: once the check has run,
    or before saying the remux isn't a regular file, where they may help
    explain it. Not for a damaged original, though: the tool's warnings
    then only say, in byte positions, what the plain message already says
    (mkvmerge's run of "Still resyncing at position ..." while it looks for
    readable data past the damage).

    It's also not swapped in if the original changed since it was probed,
    which swap_in() checks before the backup and again just before the swap
    (see Superseded): the remux was made from the old version, and swapping
    it in would silently undo whatever replaced it, such as an upgrade a
    media manager imported. The file is left as it is now, to be fixed on
    the next run.

    Nor is it swapped in unless it's a regular file. _remux_and_swap()
    removes whatever has the temp name before the remux, but a symlink
    planted there after that would have been written through by the tool,
    and renaming it over the original would turn the video into a link.

    The temp file is removed if the check fails or anything interrupts this
    (Ctrl+C, SIGTERM, an unexpected error), so it's never left behind. After
    a successful swap there's no temp file left to remove."""
    path, tmp_path = plan.path, plan.tmp_path
    try:
        if not stat.S_ISREG(os.lstat(tmp_path).st_mode):
            if tool_warnings:
                log.warning(tool_warnings)
            log.error(f"    {path.name}: {tmp_path.name} isn't a regular file, "
                      f"keeping original untouched")
            tmp_path.unlink(missing_ok=True)
            return False
        checked = verify_remux(plan)
        if checked.damaged:
            log.error(f"    {path.name}: {checked.problem}.")
            _note_failure(path, "damaged")
            tmp_path.unlink(missing_ok=True)
            return False
        if tool_warnings:
            log.warning(tool_warnings)
        if checked.problem:
            log.error(f"    {path.name}: post-remux check failed ({checked.problem}), "
                      f"keeping original untouched")
            tmp_path.unlink(missing_ok=True)
            return False
        try:
            swap_in(path, tmp_path, opts.backup_mode, opts.keep_dates, plan.snapshot)
        except OSError as exc:
            hint = ""
            if isinstance(exc, PermissionError):
                hint = " -- is it read-only, or open in another program?"
                _note_failure(path, "in use")
            log.error(f"    {path.name}: couldn't swap the new file in ({exc.strerror or exc}), "
                      f"so it's left as it was{hint}")
            tmp_path.unlink(missing_ok=True)
            return False
    except Superseded:
        log.error(f"    {path.name}: changed by another program during the remux, so it's "
                  f"left as it is now; run the script again to fix the new version")
        _note_failure(path, "changed")
        tmp_path.unlink(missing_ok=True)
        return False
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    for note in checked.notes:
        log.warning(note)
    return True


def mkvmerge_tracks(path: Path) -> list[tuple[int, str]] | None:
    """Return every track in path as mkvmerge sees it, (ID, type) in file
    order, e.g. [(0, "video"), (1, "audio"), (2, "subtitles")], or None if
    mkvmerge can't read it. mkvmerge -J exits 0 even for a file it doesn't
    recognize, but then lists no tracks, so that gives []."""
    res = run(["mkvmerge", "-J", str(path)])
    if res.returncode != 0:
        return None
    try:
        tracks = json.loads(res.stdout).get("tracks", [])
    except (json.JSONDecodeError, AttributeError):
        return None
    return [(t["id"], t.get("type", "")) for t in tracks]


@functools.cache
def mkvmerge_can_keep_legacy_font_types() -> bool:
    """True if this mkvmerge has --enable-legacy-font-mime-types, judging by
    its --help. Older versions don't have it, and don't need it either:
    they leave font MIME types alone. Checked once per run."""
    try:
        return "--enable-legacy-font-mime-types" in run(["mkvmerge", "--help"]).stdout
    except OSError:
        return False


def _announce(intro: str | None, line: str | None = None) -> None:
    """Log a file's intro (what's about to happen to it, from
    _process_file()) and line together, as one message. With --jobs > 1,
    nothing from another file can then land between the two, so the
    file's header isn't printed a second time for line (see
    _FileHeaderFilter). Either can be None."""
    text = "\n".join(part for part in (intro, line) if part)
    if text:
        log.info(text)


_MKVMERGE_PCT_RE = re.compile(r"#GUI#progress\s+(\d+)%")
"""mkvmerge --gui-mode's progress lines, e.g. "#GUI#progress 42%"."""

_MKVMERGE_WARNING_PREFIX_RE = re.compile(r"^(?:#GUI#warning\s*)?(?:warning:\s*)?", re.IGNORECASE)
"""The "#GUI#warning" and "Warning:" prefixes on mkvmerge's warnings."""


_WARNING_NUMBER_RE = re.compile(r"(?<![Tt]rack )(?<!\d)\d+")
"""The numbers _collapse_repeats() ignores when comparing two warnings: all
but a track's ("track 2: ..."), so warnings about different tracks stay
apart."""


def _collapse_repeats(lines: list[str]) -> list[str]:
    """lines with each kind of line once, where it first came, and how many
    more of that kind followed: "Still resyncing at position 511838020 (and
    14 more like it)". Lines are the same kind if they differ only in their
    numbers, as mkvmerge's do when it reports the same thing at one byte
    position after another, other than a track number, so a warning about
    one track never hides another's. Lines that don't repeat come through
    as they are."""
    kinds: dict[str, list[str]] = {}
    for line in lines:
        kinds.setdefault(_WARNING_NUMBER_RE.sub("#", line), []).append(line)
    return [same[0] if len(same) == 1 else f"{same[0].rstrip('.')} (and {len(same) - 1} more like it)"
            for same in kinds.values()]


_FFMPEG_PROGRESS_RE = re.compile(r"[a-z0-9_]+=")
"""ffmpeg -progress output: a block of key=value lines per update."""


def _mkvmerge_pct(line: str) -> int | None:
    """Read the percentage from mkvmerge's "#GUI#progress 42%" lines; None
    for any other line."""
    m = _MKVMERGE_PCT_RE.search(line)
    return int(m.group(1)) if m else None


def _ffmpeg_pct(duration: float | None) -> Callable[[str], int | None]:
    """Return a parse_pct for ffmpeg's -progress output (see
    run_with_progress()). out_time_us gives the percentage done, which
    needs the file's duration in seconds. Every other key returns 0: still
    a progress line, so it's kept out of error messages, but it doesn't
    move the bar. Without a duration, the bar only fills at the end."""
    def parse_pct(line: str) -> int | None:
        """Read one line of ffmpeg's -progress output."""
        line = line.strip()
        if not _FFMPEG_PROGRESS_RE.match(line):
            return None
        if duration and line.startswith("out_time_us="):
            try:
                return int(int(line.split("=", 1)[1]) / 1_000_000 / duration * 100)
            except ValueError:
                pass
        return 0
    return parse_pct


def _remux_and_swap(plan: Plan, cmd: list[str], parse_pct: Callable[[str], int | None],
                    opts: Options, progress: Progress, *,
                    warnings_exit: int | None = None) -> bool:
    """Run a remux command written to plan.tmp_path, then check it and swap it
    in. Returns True on success (or after a dry run, which only logs the
    command), False on failure (already logged). Shared by apply_mkv() and
    apply_remux(), which only build the command. parse_pct reads a
    percentage from one line of the tool's output, or None for any other
    line (see run_with_progress()).

    plan.intro is logged with the dry-run command, or just before the remux
    starts (see _announce()). warnings_exit is an exit code that means the
    tool finished but printed warnings (mkvmerge's 1): the file is still
    checked and used, and the warnings, each kind once (see
    _collapse_repeats()), are handed to check_and_swap_in(),
    which logs them once the check has run, unless it found the original
    damaged. A killed process can also exit with that code (mkvmerge on
    Windows), so it only counts as finished if no stop was requested.

    Whatever already has the temp name is removed before the remux, so the
    tool writes a new file rather than through a symlink someone left there
    (see check_and_swap_in()). A temp name the file system refuses, because
    the file's name is already near its length limit (255 characters on
    most), is reported, and the file left alone. The temp file is removed
    whatever stops the remux: a failure, a stop (Ctrl+C, SIGTERM) or an
    unexpected error. One try covers everything from starting the remux to
    swapping it in, check_and_swap_in() included, since a stop can land
    between any two lines: also after the remux has finished, before the
    check has started. Until check_and_swap_in() has checked it, the
    original isn't touched."""
    path, tmp_path, tool = plan.path, plan.tmp_path, cmd[0]
    if opts.dry_run:
        _announce(plan.intro, "    [dry-run] " + shlex.join(cmd))
        return True
    _announce(plan.intro)
    try:
        tmp_path.unlink(missing_ok=True)
    except OSError as exc:
        log.error(f"    {path.name}: can't use the temp name {tmp_path.name} ({exc.strerror or exc}); "
                  f"the name may be too long for this file system")
        return False

    try:
        returncode, output = run_with_progress(cmd, path.name, parse_pct, progress)
        with_warnings = returncode == warnings_exit and not _cancelled.is_set()
        if (returncode != 0 and not with_warnings) or not tmp_path.exists():
            if _cancelled.is_set():
                log.info(f"    {path.name}: cancelled")
            else:
                log.error(f"    {path.name}: {tool} remux failed: {output.strip()}")
            tmp_path.unlink(missing_ok=True)
            return False

        tool_warnings = None
        if with_warnings:
            warnings = _collapse_repeats([_MKVMERGE_WARNING_PREFIX_RE.sub("", line.strip())
                                          for line in output.splitlines() if "warning" in line.lower()])
            tool_warnings = (f"    {path.name}: {tool} finished with warnings: "
                             + ("; ".join(warnings) or output.strip() or "(no details given)"))

        return check_and_swap_in(plan, opts, tool_warnings)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def apply_mkv(plan: Plan, opts: Options, progress: Progress | None = None) -> bool:
    """Remux an MKV/WebM file with mkvmerge so only plan.target_index is
    flagged default, using opts.dry_run, opts.backup_mode and opts.keep_dates.
    Returns True on success, False on failure (already logged). The steps
    shared with apply_remux() are in _remux_and_swap().

    A full remux is used instead of an in-place mkvpropedit edit, which can
    move the track list to the end of the file and break Windows Explorer
    thumbnails.

    mkvmerge details:
    - It numbers tracks its own way, which usually matches ffprobe's stream
      indexes but not always: ffmpeg skips track types it doesn't know, so
      every later index shifts. So mkvmerge's own IDs are looked up
      (mkvmerge_tracks()) and matched to ffprobe's audio streams by
      position, since both list audio tracks in file order. If the two
      don't see the same number of audio tracks, the file is left alone.
    - By default it writes video tracks first, then audio, then subtitles,
      so a file with a subtitle between two audio tracks would come out
      reordered (and fail verify_remux()). --track-order lists every
      track in its original order, so the order is kept.
    - The flag is set with --default-track. mkvmerge 65 renamed it
      --default-track-flag but promises to keep accepting the old name,
      and older versions only know the old one.
    - --output-charset UTF-8 makes its messages UTF-8 everywhere, as
      run_with_progress() expects; otherwise it uses the system's
      encoding, which isn't UTF-8 on Windows or with LANG=C in Docker.
    - Exit code 1 means it finished with warnings, which are logged
      without mkvmerge's "#GUI#warning" and "Warning:" prefixes.
    - Newer versions rewrite fonts attached with an older MIME type
      (LEGACY_FONT_MIME_TYPES) to font/ttf or font/otf, which ffmpeg, and
      players built on it, don't recognize as fonts, so styled subtitles
      could lose them. For a file with such a font,
      --enable-legacy-font-mime-types keeps its type as it is. That also
      turns any font/ttf or font/otf in the same file into the older type,
      but a file with both is rare, and the older types work everywhere.
    """
    path = plan.path
    tracks = mkvmerge_tracks(path)
    ids = [track_id for track_id, kind in tracks or [] if kind == "audio"]
    if tracks is None or len(ids) != len(plan.streams):
        found = "couldn't read the file" if tracks is None else f"sees {len(ids)} audio track(s)"
        _announce(plan.intro)
        log.error(f"    {path.name}: mkvmerge {found}, but ffprobe sees {len(plan.streams)}; "
                  f"leaving the file alone")
        return False

    cmd = ["mkvmerge", "--gui-mode", "--output-charset", "UTF-8"]
    if (any(s.mimetype.lower() in LEGACY_FONT_MIME_TYPES for s in plan.layout)
            and mkvmerge_can_keep_legacy_font_types()):
        cmd.append("--enable-legacy-font-mime-types")
    cmd += ["-o", str(plan.tmp_path),
            "--track-order", ",".join(f"0:{track_id}" for track_id, _ in tracks)]
    for s, track_id in zip(plan.streams, ids, strict=True):
        flag = "yes" if s.index == plan.target_index else "no"
        cmd += ["--default-track", f"{track_id}:{flag}"]
    cmd.append(str(path))
    return _remux_and_swap(plan, cmd, _mkvmerge_pct, opts, progress or Progress(),
                           warnings_exit=1)


def apply_remux(plan: Plan, opts: Options, progress: Progress | None = None) -> bool:
    """Remux any non-MKV file with ffmpeg (-c copy, so nothing is re-encoded)
    so only plan.target_index is flagged default, using opts.dry_run,
    opts.backup_mode and opts.keep_dates. Returns True on success, False on
    failure (already logged). The steps shared with apply_mkv() are in
    _remux_and_swap().

    For an AVI reorder (plan.reordered), the target is moved to the first
    audio track instead, since AVI has no default flag. MP4/M4V/MOV files
    get -movflags +faststart, which keeps the index at the front of the
    file where thumbnailers expect it. Their chapter track (see
    is_chapter_track()) is left out, since ffmpeg writes a new one from
    the chapter list: copying the original's too either fails or leaves
    it as an extra data stream. plan.duration drives the progress bar."""
    path, target_index = plan.path, plan.target_index
    ext = path.suffix.lower()

    if plan.reordered:
        others = [s.index for s in plan.streams if s.index != target_index]
        map_args = ["-map", "0:v?", "-map", f"0:{target_index}"]
        for i in others:
            map_args += ["-map", f"0:{i}"]
        map_args += ["-map", "0:s?", "-map", "0:d?"]
        disp_args = ["-disposition:a:0", "+default"]
        for out_idx in range(1, len(plan.streams)):
            disp_args += [f"-disposition:a:{out_idx}", "-default"]
    else:
        map_args = ["-map", "0"]
        for s in plan.layout:
            if is_chapter_track(s):
                map_args += ["-map", f"-0:{s.index}"]
        disp_args = []
        for out_idx, s in enumerate(plan.streams):
            flag = "+default" if s.index == target_index else "-default"
            disp_args += [f"-disposition:a:{out_idx}", flag]

    faststart = ["-movflags", "+faststart"] if ext in MOV_FASTSTART_EXTS else []
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-i", str(path), *map_args, "-c", "copy", "-map_metadata", "0",
           *disp_args, *faststart, str(plan.tmp_path)]
    return _remux_and_swap(plan, cmd, _ffmpeg_pct(plan.duration), opts, progress or Progress())


Outcome = Literal["changed", "unchanged", "skipped", "error", "cancelled"]
"""What happened to one file. print_summary() gives each but "cancelled" its
own line, and counts cancelled files only in a stopped run."""


def process_file(path: Path, opts: Options,
                 on_progress: Callable[[int], None] | None = None) -> Outcome:
    """Check one file, fix it if needed, and return "changed", "unchanged",
    "skipped" or "error", or "cancelled" if a stop interrupted it (see
    Cancelled). An unexpected error is logged and returned as "error" so
    one bad file doesn't stop the run. on_progress(pct), if given, is
    called as the remux progresses (see run_with_progress()). Run it inside
    file_context(), which puts the file's header above its lines.

    Every "error" is recorded for report_failed_files(), as "other" unless
    a more specific reason was recorded already, except one caused by a
    stop: that file wasn't left unfixed by anything to put right.
    """
    try:
        outcome = _process_file(path, opts, on_progress)
    except Cancelled:
        log.info(f"  {path.name}: cancelled")
        return "cancelled"
    except Exception as exc:
        log.error(f"  {path.name}: unexpected error, skipping rest of file ({exc})")
        outcome = "error"
    if outcome == "error" and not _cancelled.is_set():
        _note_failure(path, "other")
    return outcome


def _process_file(path: Path, opts: Options,
                  on_progress: Callable[[int], None] | None = None) -> Outcome:
    """The work behind process_file(). For AVI files with --avi-reorder,
    "already correct" means the target is already the first audio track.
    --force remuxes even files that are already correct. When --prefer-lang
    matched no track, choose_target()'s note is added to the file's line,
    so it's clear why the track is in another language.

    The "setting stream#N ..." line isn't logged here but goes into the
    Plan as its intro. apply_mkv()/apply_remux() log it with the dry-run
    command, or as the remux starts, so in a --jobs dry run each file's
    lines come out together under one header."""
    ext = path.suffix.lower()
    is_avi_reorder = ext in AVI_EXTS and opts.avi_reorder

    if ext in AVI_EXTS and not opts.avi_reorder:
        log.info(f"  {path.name}: SKIP (AVI has no reliable default-track flag; re-run "
                 f"with --avi-reorder to reorder streams instead, or convert to mkv)")
        return "skipped"

    snapshot = _snapshot(path)
    layout, duration = probe_streams(path)
    if layout is None:
        return "error"
    streams = [s for s in layout if s.type == "audio"]
    if not streams:
        log.info(f"  {path.name}: SKIP (no audio streams found)")
        return "skipped"

    target, note = choose_target(streams, opts.prefer_lang)
    if target is None:
        log.info(f"  {path.name}: SKIP ({note})")
        return "skipped"
    fallback = f" ({note})" if note else ""

    if is_avi_reorder:
        changed = streams[0].index != target.index
    else:
        changed = needs_change(streams, target.index)

    if not changed and not opts.force:
        what = "is first audio stream" if is_avi_reorder else "is default"
        log.info(f"  {path.name}: already correct (stream#{target.index} {what}), skipping{fallback}")
        return "unchanged"

    action = "moving" if is_avi_reorder else "setting"
    outcome = "to the first audio track" if is_avi_reorder else "as default audio"
    plan = Plan(path, streams, target.index, duration,
                intro=f"  {path.name}: {action} stream#{target.index} "
                      f"({target.language or 'und'}, {target.codec}) {outcome}{fallback}",
                layout=layout, snapshot=snapshot, reordered=is_avi_reorder)
    progress = Progress(show=_show_bars(opts) and opts.jobs == 1, on_progress=on_progress)
    apply = apply_mkv if ext in MKV_EXTS else apply_remux
    return "changed" if apply(plan, opts, progress) else "error"


def _walk(folder: Path | str, recursive: bool, follow_symlinks: bool,
          visited: set[str] | None = None) -> Iterator[tuple[Path, os.DirEntry[str], str]]:
    """Yield (path, entry, real path) for every entry in folder that isn't a
    folder, and with recursive, in its subfolders too. entry is the
    os.DirEntry, whose type checks (is_file(), is_symlink()) are answered
    from the folder listing itself, so they cost nothing; the real path
    (as os.path.realpath() would give it) is built from the folder's own
    real path, worked out once per folder. On a network share, where each
    check of a file is a round trip, that's most of the cost of searching
    a large library.

    Symlinked subfolders are only searched with follow_symlinks; otherwise
    each one is logged, so it's clear why its files weren't found. Every
    folder is searched at most once, so a symlink loop can't make the
    search run forever.

    A folder that can't be opened (no permission, a network share that
    dropped) gets a warning, so its files aren't just silently missing
    from the run."""
    if visited is None:
        visited = set()
    real_folder = os.path.realpath(folder)
    if real_folder in visited:
        return
    visited.add(real_folder)
    try:
        with os.scandir(folder) as listing:
            entries = list(listing)
    except OSError as err:
        log.warning(f"Couldn't search {err.filename}: {err.strerror}")
        return

    subfolders = [e for e in entries if e.is_dir()] if recursive else []
    if not follow_symlinks:
        for entry in subfolders:
            if entry.is_symlink():
                log.info(f"Not searching symlinked folder (use --follow-symlinks): {entry.path}")
        subfolders = [e for e in subfolders if not e.is_symlink()]
    for entry in entries:
        if not entry.is_dir():
            yield Path(entry.path), entry, os.path.join(real_folder, entry.name)
    for entry in subfolders:
        yield from _walk(entry.path, recursive, follow_symlinks, visited)


def iter_files(paths: Iterable[Path | str], exts: set[str], recursive: bool,
               skip_symlinks: bool = False, follow_symlinks: bool = False) -> Iterator[Path]:
    """Yield every file in paths with an extension in exts, path by path in
    the order given, each path's files sorted. Files are used as given;
    folders are searched (into subfolders if recursive). The extension is
    checked before anything else, and files found in a folder are checked
    using what the folder listing already says about them (see _walk()), so
    searching costs little beyond listing each folder.

    A symlinked file is yielded as the file it points to, so that file gets
    fixed and the link keeps working; replacing the link itself would turn
    it into a separate copy. With skip_symlinks, linked files are skipped
    instead. Symlinked subfolders are only searched with follow_symlinks
    (see _walk()); a folder named in paths is always searched. A file
    reached by more than one path (through links, or given twice) is only
    yielded once, with the first path that reaches it.

    A run killed outright (kill -9, a reboot) can leave a temp file such as
    "name.mkv.tmp_remux.mkv", which still ends in .mkv. Those are skipped
    with a warning instead of being treated as videos.

    A path that doesn't exist (a typo, an unmounted share) is skipped with a
    warning, so a mistake in one of several paths doesn't go unnoticed. In
    the Docker image, a path in DOCKER_VIDEOS when nothing is mounted there
    (a forgotten -v) also gets the fix.

    Every path is made absolute first (without following symlinks), so the
    files found are too. ffprobe, ffmpeg and mkvmerge read a relative name
    starting with "-" as an option, mkvmerge reads one starting with "@" as
    a file of options, and ffmpeg reads one with a colon ("Movie:Part2.mp4")
    as a protocol, like "http:". Running the script on "." gives such
    names, since pathlib drops the leading "./"."""
    seen = set()
    videos = Path(DOCKER_VIDEOS)
    for p in (Path(os.path.abspath(x)) for x in paths):
        candidates: Iterable[tuple[Path, os.DirEntry[str] | None, str | None]]
        if p.is_file():
            candidates = [(p, None, None)]
        elif p.is_dir():
            candidates = _walk(p, recursive, follow_symlinks)
        elif not p.exists():
            hint = ""
            if _in_docker() and (p == videos or videos in p.parents) and not videos.exists():
                hint = (f". Nothing is mounted at {DOCKER_VIDEOS}: add "
                        f"-v \"/path/to/videos:{DOCKER_VIDEOS}\" to docker run")
            log.warning(f"Skipping {p}: no such file or directory{hint}")
            continue
        else:
            log.warning(f"Skipping {p}: not a file or directory")
            continue
        here = []
        for candidate in candidates:
            found = _video_file(*candidate, exts, skip_symlinks)
            if found and found[1] not in seen:
                seen.add(found[1])
                here.append(found[0])
        yield from sorted(here)


def _video_file(path: Path, entry: os.DirEntry[str] | None, real: str | None, exts: set[str],
                skip_symlinks: bool) -> tuple[Path, str] | None:
    """Decide about one candidate for iter_files(): return (the file to
    process, its real path), or None to leave it out, logging why where
    that's worth saying. entry and real come from _walk(), or are None for
    a file named on the command line. A symlink becomes the file it points
    to (or is left out with skip_symlinks)."""
    if path.suffix.lower() not in exts:
        return None
    if entry.is_symlink() if entry else path.is_symlink():
        if skip_symlinks:
            log.info(f"Skipping symlink (--skip-symlinks): {path}")
            return None
        target = path.resolve()
        if target.suffix.lower() not in exts:
            log.info(f"Skipping symlink to a file without a video extension: {path} -> {target}")
            return None
        path, entry, real = target, None, None
    if not (entry.is_file() if entry else path.is_file()):
        return None
    if path.stem.endswith(TMP_MARKER):
        log.warning(f"Skipping leftover temp file from an interrupted run (safe to delete): {path}")
        return None
    return path, real or os.path.realpath(path)


def _can_ask() -> bool:
    """True if someone is at a terminal to answer a question: both input and
    output must be terminals. Checking input alone isn't enough: Windows
    counts the NUL device as a terminal, and Task Scheduler and other
    launchers start programs with input from NUL. And with output piped
    (e.g. to tee), the question may never be seen."""
    return sys.stdin.isatty() and sys.stdout.isatty()


def ask_about_existing_backups(count: int) -> Literal["replace", "number", "quit"]:
    """Ask once what to do about files that already have a <name>.bak.
    Returns "replace", "number" or "quit". Asks again on any other answer.
    End of input (Ctrl+D, or no one there after all) counts as "number",
    as when no one can be asked, since numbering never deletes anything.

    It's asked before any file is checked, so count includes files that
    turn out to need no change. The question says the answer only applies
    to files that do; counting only those would mean probing every file
    twice."""
    question = (f"{count} file(s) already have a backup. If they're changed: [d]elete and "
                f"replace the old backup, [n]umber the new one (.bak.1, .bak.2...), or [q]uit? ")
    choices: dict[str, Literal["replace", "number", "quit"]] = {
        "d": "replace", "n": "number", "q": "quit"}
    while True:
        try:
            answer = input(question).strip().lower()
        except EOFError:
            print()
            log.info("No answer given; new backups will be numbered (.bak.1, .bak.2, ...).")
            return "number"
        if answer in choices:
            return choices[answer]


def _input_path(line: str, base: Path) -> str:
    r"""One line of an --input-file as a path, relative to base if it isn't
    absolute. Lists come written in different ways, so the line is tried as
    it is first (a plain path, spaces and all, or a Windows path, whose
    backslashes a shell would eat), then the way a shell would read it: in
    single or double quotes, with '\'' for an apostrophe (how ls shows names
    on a terminal), or with backslash-escaped spaces. A leading ~ is your
    home folder. The first reading that exists is used. If none does, the
    path is returned anyway, unquoted if it was quoted, so the warning that
    it doesn't exist names it the way you'd recognize it."""
    readings = [line]
    try:
        words = shlex.split(line)
    except ValueError:
        words = []
    if len(words) == 1 and words[0] != line:
        readings.append(words[0])
    readings += [os.path.expanduser(r) for r in readings if r.startswith("~")]
    candidates = [str(base / r) for r in readings]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    quoted = line[0] in "'\"" and len(words) == 1
    return candidates[1] if quoted else candidates[0]


def read_input_file(name: str) -> list[str]:
    """The paths listed in the file name, one per line in order, or on
    standard input if name is "-" (see _input_path()). Relative paths are
    taken from the file's folder, or the current folder for standard input,
    so a list kept next to your videos can name them by their folders.
    Blank lines and lines starting with # are skipped. The text is read as
    UTF-8, ignoring a byte order mark and Windows line endings; a name that
    isn't valid UTF-8 is kept byte for byte, so it still matches the file
    on Linux. Raises OSError if the file can't be read."""
    if name == "-":
        data, base = sys.stdin.buffer.read(), Path.cwd()
    else:
        data, base = Path(name).read_bytes(), Path(name).parent
    paths = []
    for line in data.decode("utf-8-sig", "surrogateescape").splitlines():
        line = line.strip()
        if line and not line.startswith("#"):
            paths.append(_input_path(line, base))
    return paths


BackupMode = Literal["replace", "number"]
"""What to do when a file's <name>.bak already exists: replace it, or number
the new backup (.bak.1, .bak.2, ...)."""


@dataclass(frozen=True)
class Options:
    """The command line, as parse_args() checked it: one field per option,
    named as argparse names it (--dry-run is dry_run), and paths, the paths
    given followed by --input-file's. Frozen, so no option changes partway
    through a run. The one thing settled later, the answer to the backup
    question, goes into a copy made with dataclasses.replace() (see
    main()).

    The defaults are those of a run with no options, so code that builds
    one, as the tests do, only names the options that matter."""
    paths: list[str] = field(default_factory=list)
    input_file: str | None = None
    ext: str | None = None
    no_recursive: bool = False
    skip_symlinks: bool = False
    follow_symlinks: bool = False
    dry_run: bool = False
    backup: bool = False
    existing_backups: BackupMode | None = None
    prefer_lang: str | None = None
    avi_reorder: bool = False
    keep_dates: bool = False
    force: bool = False
    log_file: str | None = None
    no_progress: bool = False
    jobs: int = 1

    @property
    def backup_mode(self) -> BackupMode | None:
        """None without --backup. Otherwise what to do about an existing
        <name>.bak: replace it if told to, or else number the new backup,
        since numbering never deletes anything."""
        if not self.backup:
            return None
        return self.existing_backups or "number"


def parse_args(argv: list[str] | None = None) -> Options:
    """Parse and check the command line (sys.argv's, unless argv is given).
    Invalid options exit with argparse's usage message and code 2. That
    includes a --log-file in a folder that doesn't exist, which would
    otherwise only fail, with a traceback, once logging starts.

    The paths to process are the ones given on the command line, then the
    ones listed in --input-file (see read_input_file()). At least one is
    needed, except in the Docker image, where with neither they default to
    DOCKER_VIDEOS, the folder the image documents mounting your videos at.
    The image itself runs --help when given no arguments at all, so a bare
    docker run never starts changing files.

    Every option argparse parses becomes the Options field of the same
    name, so a new option needs a new field too."""
    in_docker = _in_docker()
    paths_help = "Video files and folders to process"
    paths_help += f" (default: {DOCKER_VIDEOS})" if in_docker else " (or use --input-file)"
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="*", help=paths_help)
    ap.add_argument("--input-file", default=None, metavar="FILE",
                    help="Also process the paths listed in FILE, one per line, in order "
                         "(- reads them from standard input). Each path can be written as "
                         "is or quoted as a shell would; a relative one is taken from FILE's "
                         "folder. Blank lines and lines starting with # are skipped")
    ap.add_argument("--ext", default=None,
                    help="Comma-separated extensions to process, replacing the default list "
                         "(default: mkv,webm,mp4,m4v,mov,avi)")
    ap.add_argument("--no-recursive", action="store_true", help="Don't look inside subfolders")
    ap.add_argument("--skip-symlinks", action="store_true",
                    help="Skip symlinked files (default: fix the file the link points to, "
                         "leaving the link as it is)")
    ap.add_argument("--follow-symlinks", action="store_true",
                    help="Also look inside symlinked subfolders (default: skip them; folders "
                         "you name on the command line are always searched)")
    ap.add_argument("--dry-run", action="store_true",
                    help="Show what would change, and the exact commands, without touching "
                         "any files")
    ap.add_argument("--backup", action="store_true",
                    help="Keep each original as <name>.bak. Each backup takes as much space "
                         "as the original until you delete it")
    ap.add_argument("--existing-backups", choices=("replace", "number"), default=None,
                    help="With --backup, what to do when <name>.bak already exists: replace "
                         "it, or number the new one (.bak.1, .bak.2, ...). Without this, "
                         "you're asked once before any file is changed; when there's no "
                         "one to ask (cron, Docker without -it), new backups are numbered")
    ap.add_argument("--prefer-lang", default=None, metavar="LANG",
                    help="Language to use when there's a stereo track in it, as a 2- or "
                         "3-letter code (en, eng, de, ger and deu all work); also picks "
                         "between several stereo tracks. Files without one get a stereo "
                         "track in the language that plays by default now, as without "
                         "this option")
    ap.add_argument("--avi-reorder", action="store_true",
                    help="For .avi files, which have no default flag, move the stereo track "
                         "to the front instead")
    ap.add_argument("--keep-dates", action="store_true",
                    help="Give each changed file the original's modification and access "
                         "times, so it doesn't look newly changed to media servers. Off by "
                         "default: backup tools that compare size and date (e.g. rsync) "
                         "could then miss the change")
    ap.add_argument("--force", action="store_true",
                    help="Remux even files that are already correct, e.g. to restore Windows "
                         "thumbnails after another tool edited a file in place")
    ap.add_argument("--log-file", default=None, metavar="PATH",
                    help="Write the details to this file instead of the console; warnings, "
                         "errors, the progress bar and the summary still show on the console")
    ap.add_argument("--no-progress", action="store_true",
                    help="Hide the progress bars. They're already hidden when the output "
                         "isn't a terminal (cron, docker run without -t, a pipe)")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="Remux up to N files at once (default: 1). The work is limited by "
                         "disk speed, not CPU, so choose N for what your storage can handle. "
                         "Above 1, only the overall progress bar is shown, and a file's "
                         "[i/N] header is repeated when its lines follow another file's")
    args = ap.parse_args(argv)
    if args.input_file is not None:
        try:
            args.paths = [*args.paths, *read_input_file(args.input_file)]
        except OSError as exc:
            ap.error(f"couldn't read --input-file {args.input_file}: {exc.strerror or exc}")
    elif not args.paths:
        if not in_docker:
            ap.error("give at least one path to process, or --input-file")
        args.paths = [DOCKER_VIDEOS]

    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    if args.existing_backups and not args.backup:
        ap.error("--existing-backups only applies with --backup; add --backup to keep backups")
    if args.prefer_lang is not None and not re.fullmatch("[a-z]{3}",
                                                         normalize_language(args.prefer_lang)):
        ap.error(f"--prefer-lang {args.prefer_lang!r} isn't a language code; use a 2- or "
                 f"3-letter code such as en or eng")
    if args.log_file is not None and not Path(args.log_file).parent.is_dir():
        ap.error(f"--log-file's folder doesn't exist: {Path(args.log_file).parent}")
    return Options(**vars(args))


def _tell(opts: Options, line: str) -> None:
    """Log line, and with --log-file print it too, so it stays on the console
    (where a log file only sends warnings and errors)."""
    log.info(line)
    if opts.log_file:
        print(line)


def find_files(opts: Options) -> list[Path]:
    """Every file to process: the ones under opts.paths with an extension
    from --ext (or DEFAULT_EXTS), in the order the paths were given, each
    path's files sorted. So listing one show before another processes it
    first. See iter_files()."""
    if opts.ext:
        exts = {("." + e.strip().lstrip(".")).lower() for e in opts.ext.split(",")}
    else:
        exts = DEFAULT_EXTS
    return list(iter_files(opts.paths, exts, not opts.no_recursive,
                           opts.skip_symlinks, opts.follow_symlinks))


def _files_with_backups(files: list[Path]) -> int:
    """How many of files have a <name>.bak next to them, found with one
    listing per folder rather than one check per file: on a network share
    each check is a round trip, and a library can hold tens of thousands of
    files. A folder that can't be listed counts as having none; the run
    reports it when it searches the folder."""
    wanted: dict[Path, set[str]] = {}
    for f in files:
        wanted.setdefault(f.parent, set()).add(f.name + ".bak")
    count = 0
    for folder, names in wanted.items():
        with contextlib.suppress(OSError), os.scandir(folder) as listing:
            count += sum(1 for entry in listing if entry.name in names)
    return count


def choose_backup_mode(opts: Options,
                       files: list[Path]) -> Literal["replace", "number", "quit"]:
    """What to do with backups when <name>.bak already exists: "replace",
    "number" or "quit". --existing-backups decides if given. Otherwise,
    if any of files has one (see _files_with_backups()), the user is asked
    (see ask_about_existing_backups()), or new backups are numbered when no
    one can answer, since that never deletes anything."""
    if opts.existing_backups:
        return opts.existing_backups
    with_backup = _files_with_backups(files)
    if not with_backup:
        return "number"
    if _can_ask():
        return ask_about_existing_backups(with_backup)
    log.info(f"{with_backup} file(s) already have a backup; new backups will be "
             f"numbered (.bak.1, .bak.2, ...). Use --existing-backups to choose.")
    return "number"


def _show_bars(opts: Options) -> bool:
    """True if progress bars should be drawn: tqdm is installed, --no-progress
    wasn't given, and the bars' output (stderr) is a terminal. Anywhere else
    (cron, docker run without -t, docker logs, a pipe), the codes that move
    the cursor to redraw a bar would land in the output as junk."""
    return HAVE_TQDM and not opts.no_progress and sys.stderr.isatty()


def process_all(files: list[Path], opts: Options, stats: Counter[Outcome]) -> None:
    """Process every file, counting each outcome in stats ("changed": 3, ...).
    A stop (Ctrl+C, SIGTERM) comes out as KeyboardInterrupt, with the
    progress bars closed and stats holding the files that finished.

    How a run is shown:
    - --jobs 1 handles one file at a time, with a progress bar for the
      current file above the overall one. --jobs N > 1 handles N at once in
      threads (the work waits on disk, not CPU) and shows only the overall
      bar. With --log-file and no bars, a "Processing i/N..." counter takes
      their place on a terminal. Without one, there are no bars or counter
      (see _show_bars()).
    - The overall bar counts fractions of files, so it keeps moving during
      a long remux. Its count is rounded and capped at the total, because
      adding up many small steps can drift just past it, which makes tqdm
      warn and show a negative time remaining.
    - The blank line between the bars is an empty tqdm bar, not a print():
      tqdm can't account for output it didn't write, and would draw the
      bars in the wrong place.
    - Each file runs inside file_context(), so its lines are kept under its
      header, repeated if another file printed in between
      (_FileHeaderFilter).
    - Once a stop is requested, files still waiting their turn return
      "cancelled" straight away instead of starting."""
    use_bar = _show_bars(opts)
    counter = opts.log_file and not use_bar and sys.stdout.isatty()
    bars, overall = [], None
    if use_bar:
        first_row = 1 if opts.jobs == 1 else 0
        bars.append(tqdm(total=1, position=first_row, bar_format="{desc}", desc="", leave=False))
        overall = tqdm(total=len(files), unit="file", desc="Processing", position=first_row + 1,
                       bar_format="{l_bar}{bar}| {n:.2f}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
                       leave=False)
        bars.append(overall)
    overall_lock = threading.Lock()

    def advance_overall(delta: float) -> None:
        """Move the overall bar by delta files. Several threads report at once
        with --jobs > 1, so updates go through a lock. Only called when
        there is an overall bar."""
        assert overall is not None
        with overall_lock:
            overall.n = min(round(overall.n + delta, 6), overall.total)
            overall.refresh()

    def run_one(i: int, f: Path) -> Outcome:
        """Process file number i, keeping the overall bar in step."""
        if _cancelled.is_set():
            return "cancelled"
        last_reported = 0.0

        def on_progress(pct: int) -> None:
            """Move the overall bar by however much this file has progressed
            since its last report."""
            nonlocal last_reported
            advance_overall(pct / 100.0 - last_reported)
            last_reported = pct / 100.0

        with file_context(f"[{i}/{len(files)}] {f}"):
            result = process_file(f, opts, on_progress=on_progress if overall else None)
        if overall:
            advance_overall(1.0 - last_reported)
        return result

    def show_counter(n: int) -> None:
        """Show "Processing n/N..." in place, when it stands in for the bars."""
        if counter:
            print(f"\rProcessing {n}/{len(files)}...", end="", flush=True)

    try:
        if opts.jobs == 1:
            for i, f in enumerate(files, 1):
                show_counter(i)
                result = run_one(i, f)
                stats[result] += 1
        else:
            with ThreadPoolExecutor(max_workers=opts.jobs) as pool:
                futures = [pool.submit(run_one, i, f) for i, f in enumerate(files, 1)]
                for done, fut in enumerate(as_completed(futures), 1):
                    show_counter(done)
                    result = fut.result()
                    stats[result] += 1
    finally:
        for bar in reversed(bars):
            with contextlib.suppress(Exception):
                bar.close()
    if overall or counter:
        print()


def print_summary(stats: Counter[Outcome], opts: Options, partial: bool = False,
                  cancelled: int = 0) -> None:
    """Log the counts, printing them too with --log-file (see _tell()). In a
    dry run nothing was changed, so the heading says so and "Changed" reads
    "Would change". partial marks a run that was stopped, where cancelled
    files didn't finish. Each line, and the heading's note, starts with a
    capital letter."""
    notes = (["partial -- interrupted"] if partial else []) + (
        ["dry run, nothing was changed"] if opts.dry_run else [])
    note = "; ".join(notes)
    heading = "Summary" + (f" ({note[:1].upper()}{note[1:]})" if note else "")
    _tell(opts, "")
    _tell(opts, f"----- {heading} -----")
    lines: tuple[Outcome, ...] = ("changed", "unchanged", "skipped", "error")
    for k in lines:
        name = "would change" if k == "changed" and opts.dry_run else k
        _tell(opts, f"{name.capitalize()}: {stats[k]}")
    if cancelled:
        _tell(opts, f"Cancelled: {cancelled}")


def main(argv: list[str] | None = None) -> int:
    """Run the command line: find the files, process each one, and print a
    summary. Returns the exit code: 0 when done, 1 if no files were found,
    a tool is missing or any file had an error, or 128 + the signal number
    when stopped (130 for Ctrl+C, 143 for SIGTERM). Invalid options exit
    with 2 straight from parse_args(), and a --log-file that can't be
    written (no permission, or a folder rather than a file) returns 2 too.

    ffmpeg and ffprobe are checked before the search for files, which can
    take minutes on a large library or a network share, so a missing tool is
    reported at once. mkvmerge is checked once the search shows there are
    MKV files to process.

    When stopped while files are being processed, the stop handler has
    already killed every remux and each one has removed its temp file.
    What's left is a partial summary that counts unfinished files as
    cancelled. A stop while looking for files, or at the backup question,
    ends the run with no summary, since nothing has changed. Once every
    file is done, a stop is ignored: only the summary is left. Files that
    couldn't be given their original owner are reported in one warning
    just before the summary (report_ownership_failures()), with the full
    list saved next to --log-file, or in the current folder. Then every file
    that couldn't be fixed is listed with its full path, grouped by what to
    do about it (report_failed_files()), so none needs finding in a long
    run's output.

    The answer to the backup question goes into opts.existing_backups, in
    a copy of opts (Options is frozen), so the rest of the run reads it from
    opts.backup_mode like any other option.

    Call it from the main thread, since it installs the Ctrl+C and SIGTERM
    handlers, which Python only allows there (the previous handlers are put
    back when it returns), and one run at a time: runs share the module's
    state, which each one resets as it starts (see _reset_run_state()).
    """
    _reset_run_state()
    opts = parse_args(argv)
    try:
        setup_logging(opts.log_file)
    except OSError as exc:
        print(f"Can't write --log-file {opts.log_file}: {exc.strerror or exc}", file=sys.stderr)
        return 2
    previous = {sig: signal.signal(sig, _stop_handler) for sig in (signal.SIGINT, signal.SIGTERM)}
    previous_hook = sys.unraisablehook
    sys.unraisablehook = lambda unraisable: _unraisable(unraisable, previous_hook)
    try:
        return _run(opts)
    finally:
        sys.unraisablehook = previous_hook
        for sig, handler in previous.items():
            signal.signal(sig, handler)


def _ignore_stops() -> None:
    """Ignore Ctrl+C and SIGTERM from now on: the files are done, or a stop
    is already being reported, and only the summary is left. Otherwise a
    signal now would raise Stopped wherever the main thread happens to be
    and end the run in a traceback.

    Run as a program, this is also set before main() starts, so the
    handlers main() puts back when it returns are these, and a signal
    during the interpreter's own exit is ignored too, rather than ending it
    with Python's default KeyboardInterrupt."""
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    signal.signal(signal.SIGTERM, signal.SIG_IGN)


def _run(opts: Options) -> int:
    """The run behind main(), once the stop handlers are installed: the
    search, the backup question, the files, the summary. One try covers
    all of it, so a stop can't arrive between two of them and go
    unhandled; stage says what it interrupted, which decides the message
    and whether there's a partial summary to print. After each phase,
    _check_stop() catches a stop whose exception was lost."""
    files: list[Path] = []
    stats: Counter[Outcome] = Counter()
    list_folder = Path(opts.log_file).parent if opts.log_file else Path.cwd()
    stage = "search"
    try:
        if not check_tools(need_mkvmerge=False):
            return 1
        files = find_files(opts)
        _check_stop()
        if not files:
            log.error("No matching files found.")
            return 1
        if any(f.suffix.lower() in MKV_EXTS for f in files) and not check_tools(need_mkvmerge=True):
            return 1
        _tell(opts, f"Found {len(files)} file(s){' (dry run)' if opts.dry_run else ''}.")

        if opts.backup and not opts.dry_run:
            stage = "question"
            answer = choose_backup_mode(opts, files)
            _check_stop()
            if answer == "quit":
                _tell(opts, "Quit before changing any files.")
                return 0
            opts = dataclasses.replace(opts, existing_backups=answer)

        stage = "files"
        process_all(files, opts, stats)
        _check_stop()
        _ignore_stops()
    except KeyboardInterrupt as exc:
        _ignore_stops()
        signum, reason = _stop_reason(exc)
        if stage == "search":
            log.error(f"{reason} while looking for files. No files were changed.")
            return 128 + signum
        print()
        if stage == "question":
            log.error(f"{reason}. No files were changed.")
            return 128 + signum
        log.error(f"{reason}. In-flight remuxes were stopped and their "
                  "partial temp files removed; already-finished files are unaffected.")
        report_ownership_failures(list_folder)
        report_failed_files()
        finished = sum(stats[k] for k in ("changed", "unchanged", "skipped", "error"))
        print_summary(stats, opts, partial=True, cancelled=len(files) - finished)
        return 128 + signum

    report_ownership_failures(list_folder)
    report_failed_files()
    print_summary(stats, opts)
    return 1 if stats["error"] else 0


if __name__ == "__main__":
    _ignore_stops()
    sys.exit(main())
