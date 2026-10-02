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
  do, the file is skipped. --prefer-lang sets the language yourself and
  breaks ties.

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
  - Each new file is checked (no lost streams, no shorter than the
    original, the right track is default) before it replaces the
    original. --backup also keeps the original as <name>.bak, never
    deleting an existing backup unless you say so.
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

  Use the English stereo track, whatever language plays by default now:
    python3 set_stereo_default.py /path/to/videos --prefer-lang en

  Just these files, or only .mkv files and not in subfolders:
    python3 set_stereo_default.py file1.mkv file2.mp4
    python3 set_stereo_default.py /path/to/videos --ext mkv --no-recursive

Exit codes: 0 all done, 1 a file had an error, no files matched or a tool is
missing, 2 invalid options, 130 stopped by Ctrl+C, 143 stopped by SIGTERM.

Full guide: https://github.com/tronyx/Set-Stereo-Default
"""

from __future__ import annotations

import argparse
import contextlib
import importlib
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from collections import deque
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from types import FrameType, ModuleType
from typing import NoReturn

try:
    from tqdm import tqdm
    HAVE_TQDM = True
except ImportError:
    HAVE_TQDM = False



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

TMP_MARKER = ".tmp_remux"
"""Marks a file's temp copy while it's remuxed, e.g. "movie.mkv.tmp_remux.mkv"."""

COMMENTARY_NAME_RE = re.compile(r"commentary|audio[ -]?description|descriptive|described|\bdvs\b",
                                re.IGNORECASE)
"""Track names that mark commentary or audio description, as such tracks are
really named: "Director's Commentary", "Audio Description", "Descriptive
Video Service", "Described Video", "DVS". A bare "description" is too loose
to count."""

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


def setup_logging(log_file: str | None) -> None:
    """Send log messages to the console, or with --log-file to that file.
    With a log file, warnings and errors still reach the console too, so a
    failed run (e.g. a missing tool) never ends without saying why."""
    log.setLevel(logging.INFO)
    log.propagate = False
    console = TqdmLoggingHandler() if HAVE_TQDM else logging.StreamHandler(sys.stdout)
    console.setFormatter(logging.Formatter("%(message)s"))
    if log_file:
        handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
        log.addHandler(handler)
        console.setLevel(logging.WARNING)
    log.addHandler(console)


_file_context = threading.local()
"""Per thread: the "[i/N] path" header of the file it's working on, or None
outside file_context()."""

_last_header = [None]
"""The header of the file that printed the most recent line."""

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

    The check and the printing must happen as one step, or two files
    printing at the same moment could still mix. The filter runs before
    anything is printed, so it takes the lock, adds the header if needed,
    hands the line to the handlers itself, and returns False so it isn't
    printed twice. Lines logged outside file_context() pass straight
    through."""

    def filter(self, record: logging.LogRecord) -> bool:
        header = getattr(_file_context, "header", None)
        if header is None:
            return True
        with _print_lock:
            if _last_header[0] != header:
                record.msg, record.args = f"\n{header}\n{record.getMessage()}", None
                _last_header[0] = header
            log.callHandlers(record)
        return False


log.addFilter(_FileHeaderFilter())


@contextlib.contextmanager
def file_context(header: str) -> Iterator[None]:
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

_ownership_failures: list[tuple[str, tuple[int, int], tuple[int, int], str]] = []
"""Remuxed files that couldn't be given their original owner, as (full path,
wanted owner, actual owner, reason). Reported once, at the end of the run,
by report_ownership_failures()."""

_ownership_lock = threading.Lock()
"""Guards _ownership_failures, which several --jobs threads add to at once."""


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
    systemd) reaches only this process, not the remuxes it started."""
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
    progress at row progress.position. progress.on_progress(pct), if set,
    is called on every increase and with 100 on success; process_all()
    uses it to move the overall bar. Output is read as UTF-8, as in run().

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
            bar = tqdm(total=100, desc=f"  {label}"[:40], unit="%", leave=False,
                       position=progress.position)
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
    names (from NAME_TAGS), and its default, commentary and
    audio-description flags. Frozen, so streams can be shared freely."""
    index: int
    type: str = ""
    codec: str = ""
    channels: int | None = None
    default: bool = False
    comment: bool = False
    visual_impaired: bool = False
    language: str = ""
    names: tuple[str, ...] = ()


def _stream_info(raw: dict) -> Stream:
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


def normalize_language(code: str | None) -> str:
    """Return code in one standard form, so different tags for the same
    language compare equal: lowercased, with any region or script part
    dropped ("pt-BR" -> "pt"), then mapped to its ISO 639-2/T code through
    LANGUAGE_ALIASES ("de" and "ger" -> "deu"). Codes that aren't in the
    table come back lowercased; an empty tag stays empty."""
    base = re.split(r"[-_]", (code or "").strip().lower(), maxsplit=1)[0]
    return LANGUAGE_ALIASES.get(base, base)


def choose_target(streams: list[Stream],
                  prefer_lang: str | None) -> tuple[Stream | None, str | None]:
    """Return (stream, note): the stereo track to make default, or None and a
    note saying why the file should be skipped.

    Commentary and audio-description tracks are never picked. The track
    must be in the wanted language: prefer_lang if given, otherwise the
    language of the track players start on now (the default one, or the
    first if none is flagged). That way a stereo dub never replaces the
    original language. A track with no language tag (or "und") matches any
    language, but a track tagged with the wanted one wins over it. Codes
    are compared after normalize_language(), so "de", "ger" and "deu" all
    mean German."""
    def describe(ss: list[Stream]) -> str:
        """List tracks for a skip note, e.g. "stream#2 (eng/aac), stream#3 (spa/ac3)"."""
        return ", ".join(f"stream#{s.index} ({s.language or 'und'}/{s.codec})" for s in ss)

    stereo = [s for s in streams if s.channels == 2]
    candidates = [s for s in stereo if not is_commentary(s)]
    if not candidates:
        if stereo:
            return None, f"only 2-channel tracks are commentary/audio description [{describe(stereo)}]"
        return None, "no 2-channel audio track found"

    current = next((s for s in streams if s.default), streams[0])
    shown = prefer_lang or current.language
    wanted = normalize_language(shown)
    if wanted not in ("", "und"):
        in_lang = [s for s in candidates
                   if normalize_language(s.language) in ("", "und", wanted)]
        if not in_lang:
            return None, (f"no 2-channel track in '{shown}' [found {describe(candidates)}] "
                          f"-- use --prefer-lang to pick another language")
        exact = [s for s in in_lang if normalize_language(s.language) == wanted]
        candidates = exact if len(exact) == 1 else in_lang

    if len(candidates) == 1:
        return candidates[0], None
    return None, (
        f"multiple 2-channel tracks found [{describe(candidates)}] -- use --prefer-lang to disambiguate"
    )


def needs_change(streams: list[Stream], target_index: int) -> bool:
    """True unless the target is already the only audio track flagged default."""
    return any((s.index == target_index) != s.default for s in streams)


def verify_remux(plan: Plan, reordered: bool) -> str | None:
    """Check the finished remux at plan.tmp_path before it replaces the
    original. Returns None if it looks right, otherwise a short reason why
    not. The original isn't probed again: plan.layout and plan.duration
    already describe it.

    The new file must have as many streams as the original, so nothing was
    lost, and must not be shorter than the original by more than
    MAX_DURATION_LOSS (and at least MIN_DURATION_LOSS seconds). A much
    shorter result means the original contains less than its header
    claims, e.g. an incomplete download, so it's left alone for a person
    to look at. A longer one is fine: the original's header just
    understated it. The check is skipped if either duration is unknown.

    Then the target must be the only audio track flagged default. A
    remux keeps audio tracks in order, so the target is found by its
    position among them. After an AVI reorder (reordered=True) there's no
    flag to check, so the first audio track must instead match the
    target's codec, channel count and language (compared after
    normalize_language(), as everywhere else)."""
    after, after_duration = probe_streams(plan.tmp_path, report=False)
    if after is None:
        return "ffprobe couldn't read the file"
    before, before_duration, streams = plan.layout, plan.duration, plan.streams
    if len(after) != len(before):
        return f"stream count changed from {len(before)} to {len(after)}"
    if before_duration and after_duration is not None:
        allowed = max(before_duration * MAX_DURATION_LOSS, MIN_DURATION_LOSS)
        if after_duration < before_duration - allowed:
            return (f"duration dropped from {before_duration:.1f}s to {after_duration:.1f}s; "
                    f"the original may be incomplete")

    audio = [s for s in after if s.type == "audio"]
    if len(audio) != len(streams):
        return f"expected {len(streams)} audio tracks, found {len(audio)}"

    target = next(s for s in streams if s.index == plan.target_index)
    if reordered:
        first = audio[0]
        if (first.codec != target.codec or first.channels != target.channels
                or (target.language and normalize_language(first.language)
                    != normalize_language(target.language))):
            return "target audio track didn't end up first"
        return None

    expected = audio[streams.index(target)].index
    defaults = [s.index for s in audio if s.default]
    if defaults != [expected]:
        found = ", ".join(f"stream#{i}" for i in defaults) or "no track"
        return f"default flag is on {found}, expected only stream#{expected}"
    return None


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
    hard links aren't supported, a full copy is made instead."""
    bak_path = backup_path(path, replace)
    bak_path.unlink(missing_ok=True)
    try:
        os.link(path, bak_path)
    except OSError:
        shutil.copy2(path, bak_path)
    return bak_path


def _owner(path: Path) -> tuple[int, int]:
    """Return path's owner as (user ID, group ID)."""
    st = os.stat(path)
    return st.st_uid, st.st_gid


def _user_name(uid: int) -> str | None:
    """The name of user ID uid, or None if it has none here (or on Windows)."""
    if pwd is None:
        return None
    try:
        return pwd.getpwuid(uid).pw_name
    except KeyError:
        return None


def _group_name(gid: int) -> str | None:
    """The name of group ID gid, or None if it has none here (or on Windows)."""
    if grp is None:
        return None
    try:
        return grp.getgrgid(gid).gr_name
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
    set_stereo_default-owners-<date>-<time>.log in folder, one per line in
    path order (they're recorded in whatever order --jobs finishes them),
    and return its path. If the files don't all share one wanted and one actual
    owner, each line also says which. Falls back to the system's temp
    folder if folder can't be written to; returns None if that fails too.
    The timestamp means a later run never overwrites an earlier list."""
    owners = {(wanted, got) for _, wanted, got, _ in _ownership_failures}
    lines = [path if len(owners) == 1
             else f"{path}  (should belong to {_owner_name(*wanted)}, belongs to {_owner_name(*got)})"
             for path, wanted, got, _ in sorted(_ownership_failures)]
    name = f"set_stereo_default-owners-{time.strftime('%Y%m%d-%H%M%S')}.log"
    for place in (Path(folder), Path(tempfile.gettempdir())):
        target = (place / name).resolve()
        try:
            target.write_text("\n".join(lines) + "\n", encoding="utf-8")
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
    it looked like that file's problem."""
    if not _ownership_failures:
        return
    path, wanted, got, reason = _ownership_failures[0]
    count = len(_ownership_failures)
    user = _user_name(wanted[0]) or f"'#{wanted[0]}'"
    advice = (f"Permissions were still copied. Changing a file's owner needs root, and NFS "
              f"shares usually turn root into 'nobody'. Run the script as the files' owner "
              f"instead (sudo -u {user} python3 ...).")
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


def swap_in(path: Path, tmp_path: Path, backup: str | bool, keep_dates: bool = False) -> None:
    """Replace path with the checked remux at tmp_path. Copies the original's
    permissions and owner (and with keep_dates, its access and modification
    times), keeps the original as a backup if backup is set, then swaps the
    new file in with a single atomic rename.

    backup is falsy for no backup, "replace" to overwrite an existing
    <name>.bak, or anything else to number the new one if <name>.bak
    exists (see backup_path()).

    keep_dates is off by default because tools that spot changed files by
    size and modification time (rsync's default, some backup software)
    could skip a remux that kept both, leaving a stale copy."""
    copy_ownership(path, tmp_path)
    if keep_dates:
        st = os.stat(path)
        os.utime(tmp_path, ns=(st.st_atime_ns, st.st_mtime_ns))
    if backup:
        bak_path = make_backup(path, replace=(backup == "replace"))
        if bak_path.suffix != ".bak":
            log.info(f"    {path.name}: kept the original as {bak_path.name}")
    os.replace(tmp_path, path)


@dataclass
class Plan:
    """What's going to happen to one file, as decided by _process_file():
    its audio streams, the index of the one to make default, its duration
    in seconds (None if unknown), intro, the "setting stream#N ..." line
    logged as the remux starts (see _announce()), and layout, every stream
    in the file, which verify_remux() compares the remux against. The
    streams, duration and layout all come from one probe_streams() call."""
    path: Path
    streams: list[Stream]
    target_index: int
    duration: float | None = None
    intro: str | None = None
    layout: list[Stream] = field(default_factory=list)

    @property
    def tmp_path(self) -> Path:
        """Where the remux is written before it replaces the original."""
        return self.path.with_name(self.path.name + TMP_MARKER + self.path.suffix)


@dataclass
class Progress:
    """How to show a remux's progress: show its own bar at row position
    (--jobs 1 only), and call on_progress(pct) to move the overall bar."""
    show: bool = False
    position: int = 0
    on_progress: Callable[[int], None] | None = None


def check_and_swap_in(plan: Plan, reordered: bool, args: argparse.Namespace) -> bool:
    """Check a finished remux with verify_remux() and swap it in if it passes.
    Returns True if the original was replaced, False if the check failed
    (already logged).

    The temp file is removed if the check fails or anything interrupts this
    (Ctrl+C, SIGTERM, an unexpected error), so it's never left behind. After
    a successful swap there's no temp file left to remove."""
    path, tmp_path = plan.path, plan.tmp_path
    try:
        problem = verify_remux(plan, reordered)
        if problem:
            log.error(f"    {path.name}: post-remux check failed ({problem}), keeping original untouched")
            tmp_path.unlink(missing_ok=True)
            return False
        swap_in(path, tmp_path, args.backup, args.keep_dates)
        return True
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def mkvmerge_audio_ids(path: Path) -> list[int] | None:
    """Return mkvmerge's track IDs for path's audio tracks, in file order, or
    None if mkvmerge can't read it. mkvmerge -J exits 0 even for a file it
    doesn't recognize, but then lists no tracks, so that gives []."""
    res = run(["mkvmerge", "-J", str(path)])
    if res.returncode != 0:
        return None
    try:
        tracks = json.loads(res.stdout).get("tracks", [])
    except (json.JSONDecodeError, AttributeError):
        return None
    return [t["id"] for t in tracks if t.get("type") == "audio"]


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
                    args: argparse.Namespace, progress: Progress, *, reordered: bool = False,
                    warnings_exit: int | None = None) -> bool:
    """Run a remux command written to plan.tmp_path, then check it and swap it
    in. Returns True on success (or after a dry run, which only logs the
    command), False on failure (already logged). Shared by apply_mkv() and
    apply_remux(), which only build the command.

    plan.intro is logged with the dry-run command, or just before the remux
    starts (see _announce()). warnings_exit is an exit code that means the
    tool finished but printed warnings (mkvmerge's 1): the warnings are
    logged and the file is still checked and used. A killed process can
    also exit with that code (mkvmerge on Windows), so it only counts as
    finished if no stop was requested.

    The temp file is removed whatever stops the remux: a failure, a stop
    (Ctrl+C, SIGTERM) or an unexpected error. Until check_and_swap_in()
    has checked it, the original isn't touched."""
    path, tmp_path, tool = plan.path, plan.tmp_path, cmd[0]
    if args.dry_run:
        _announce(plan.intro, "    [dry-run] " + shlex.join(cmd))
        return True
    _announce(plan.intro)

    try:
        returncode, output = run_with_progress(cmd, path.name, parse_pct, progress)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    with_warnings = returncode == warnings_exit and not _cancelled.is_set()
    if (returncode != 0 and not with_warnings) or not tmp_path.exists():
        if _cancelled.is_set():
            log.info(f"    {path.name}: cancelled")
        else:
            log.error(f"    {path.name}: {tool} remux failed: {output.strip()}")
        tmp_path.unlink(missing_ok=True)
        return False

    if with_warnings:
        warnings = [_MKVMERGE_WARNING_PREFIX_RE.sub("", line.strip())
                    for line in output.splitlines() if "warning" in line.lower()]
        log.warning(f"    {path.name}: {tool} finished with warnings: "
                    + ("; ".join(warnings) or output.strip() or "(no details given)"))

    return check_and_swap_in(plan, reordered, args)


def apply_mkv(plan: Plan, args: argparse.Namespace, progress: Progress | None = None) -> bool:
    """Remux an MKV/WebM file with mkvmerge so only plan.target_index is
    flagged default, using args.dry_run, args.backup and args.keep_dates.
    Returns True on success, False on failure (already logged). The steps
    shared with apply_remux() are in _remux_and_swap().

    A full remux is used instead of an in-place mkvpropedit edit, which can
    move the track list to the end of the file and break Windows Explorer
    thumbnails.

    mkvmerge details:
    - It numbers tracks its own way, which usually matches ffprobe's stream
      indexes but not always: ffmpeg skips track types it doesn't know, so
      every later index shifts. So mkvmerge's own IDs are looked up
      (mkvmerge_audio_ids()) and matched to ffprobe's audio streams by
      position, since both list audio tracks in file order. If the two
      don't see the same number of audio tracks, the file is left alone.
    - The flag is set with --default-track. mkvmerge 65 renamed it
      --default-track-flag but promises to keep accepting the old name,
      and older versions only know the old one.
    - --output-charset UTF-8 makes its messages UTF-8 everywhere, as
      run_with_progress() expects; otherwise it uses the system's
      encoding, which isn't UTF-8 on Windows or with LANG=C in Docker.
    - Exit code 1 means it finished with warnings, which are logged
      without mkvmerge's "#GUI#warning" and "Warning:" prefixes.
    """
    path = plan.path
    ids = mkvmerge_audio_ids(path)
    if ids is None or len(ids) != len(plan.streams):
        found = "couldn't read the file" if ids is None else f"sees {len(ids)} audio track(s)"
        _announce(plan.intro)
        log.error(f"    {path.name}: mkvmerge {found}, but ffprobe sees {len(plan.streams)}; "
                  f"leaving the file alone")
        return False

    cmd = ["mkvmerge", "--gui-mode", "--output-charset", "UTF-8", "-o", str(plan.tmp_path)]
    for s, track_id in zip(plan.streams, ids, strict=True):
        flag = "yes" if s.index == plan.target_index else "no"
        cmd += ["--default-track", f"{track_id}:{flag}"]
    cmd.append(str(path))
    return _remux_and_swap(plan, cmd, _mkvmerge_pct, args, progress or Progress(),
                           warnings_exit=1)


def apply_remux(plan: Plan, args: argparse.Namespace, progress: Progress | None = None) -> bool:
    """Remux any non-MKV file with ffmpeg (-c copy, so nothing is re-encoded)
    so only plan.target_index is flagged default, using args.dry_run,
    args.backup, args.keep_dates and args.avi_reorder. Returns True on
    success, False on failure (already logged). The steps shared with
    apply_mkv() are in _remux_and_swap().

    With --avi-reorder, an AVI file instead gets the target moved to the
    first audio track, since AVI has no default flag. MP4/M4V/MOV files get
    -movflags +faststart, which keeps the index at the front of the file
    where thumbnailers expect it. plan.duration drives the progress bar.
    """
    path, target_index = plan.path, plan.target_index
    ext = path.suffix.lower()
    reordered = args.avi_reorder and ext in AVI_EXTS

    if reordered:
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
        disp_args = []
        for out_idx, s in enumerate(plan.streams):
            flag = "+default" if s.index == target_index else "-default"
            disp_args += [f"-disposition:a:{out_idx}", flag]

    faststart = ["-movflags", "+faststart"] if ext in MOV_FASTSTART_EXTS else []
    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-i", str(path), *map_args, "-c", "copy", "-map_metadata", "0",
           *disp_args, *faststart, str(plan.tmp_path)]
    return _remux_and_swap(plan, cmd, _ffmpeg_pct(plan.duration), args,
                           progress or Progress(), reordered=reordered)


def process_file(path: Path, args: argparse.Namespace, position: int = 0,
                 on_progress: Callable[[int], None] | None = None) -> str:
    """Check one file, fix it if needed, and return "changed", "unchanged",
    "skipped" or "error", or "cancelled" if a stop interrupted it (see
    Cancelled). An unexpected error is logged and returned as "error" so
    one bad file doesn't stop the run. position is the progress bar's row
    (--jobs 1 only). Run it inside file_context(), which puts the file's
    header above its lines.
    """
    try:
        return _process_file(path, args, position, on_progress)
    except Cancelled:
        log.info(f"  {path.name}: cancelled")
        return "cancelled"
    except Exception as exc:
        log.error(f"  {path.name}: unexpected error, skipping rest of file ({exc})")
        return "error"


def _process_file(path: Path, args: argparse.Namespace, position: int = 0,
                  on_progress: Callable[[int], None] | None = None) -> str:
    """The work behind process_file(). For AVI files with --avi-reorder,
    "already correct" means the target is already the first audio track.
    --force remuxes even files that are already correct.

    The "setting stream#N ..." line isn't logged here but goes into the
    Plan as its intro. apply_mkv()/apply_remux() log it with the dry-run
    command, or as the remux starts, so in a --jobs dry run each file's
    lines come out together under one header."""
    ext = path.suffix.lower()
    is_avi_reorder = ext in AVI_EXTS and args.avi_reorder

    if ext in AVI_EXTS and not args.avi_reorder:
        log.info(f"  {path.name}: SKIP (AVI has no reliable default-track flag; re-run "
                 f"with --avi-reorder to reorder streams instead, or convert to mkv)")
        return "skipped"

    layout, duration = probe_streams(path)
    if layout is None:
        return "error"
    streams = [s for s in layout if s.type == "audio"]
    if not streams:
        log.info(f"  {path.name}: no audio streams found, skipping")
        return "skipped"

    target, note = choose_target(streams, args.prefer_lang)
    if target is None:
        log.info(f"  {path.name}: SKIP ({note})")
        return "skipped"

    if is_avi_reorder:
        changed = streams[0].index != target.index
    else:
        changed = needs_change(streams, target.index)

    if not changed and not args.force:
        what = "is first audio stream" if is_avi_reorder else "is default"
        log.info(f"  {path.name}: already correct (stream#{target.index} {what}), skipping")
        return "unchanged"

    action = "moving" if is_avi_reorder else "setting"
    outcome = "to the first audio track" if is_avi_reorder else "as default audio"
    plan = Plan(path, streams, target.index, duration,
                intro=f"  {path.name}: {action} stream#{target.index} "
                      f"({target.language or 'und'}, {target.codec}) {outcome}",
                layout=layout)
    progress = Progress(show=HAVE_TQDM and not args.no_progress and args.jobs == 1,
                        position=position, on_progress=on_progress)
    apply = apply_mkv if ext in MKV_EXTS else apply_remux
    return "changed" if apply(plan, args, progress) else "error"


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
    """Yield every file in paths with an extension in exts. Files are used
    as given; folders are searched (into subfolders if recursive). The
    extension is checked before anything else, and files found in a folder
    are checked using what the folder listing already says about them (see
    _walk()), so searching costs little beyond listing each folder.

    A symlinked file is yielded as the file it points to, so that file gets
    fixed and the link keeps working; replacing the link itself would turn
    it into a separate copy. With skip_symlinks, linked files are skipped
    instead. Symlinked subfolders are only searched with follow_symlinks
    (see _walk()); a folder named in paths is always searched. A file
    reached by more than one path (through links, or given twice) is only
    yielded once.

    A run killed outright (kill -9, a reboot) can leave a temp file such as
    "name.mkv.tmp_remux.mkv", which still ends in .mkv. Those are skipped
    with a warning instead of being treated as videos.

    A path that doesn't exist (a typo, an unmounted share) is skipped with a
    warning, so a mistake in one of several paths doesn't go unnoticed."""
    seen = set()
    for p in map(Path, paths):
        candidates: Iterable[tuple[Path, os.DirEntry[str] | None, str | None]]
        if p.is_file():
            candidates = [(p, None, None)]
        elif p.is_dir():
            candidates = _walk(p, recursive, follow_symlinks)
        elif not p.exists():
            log.warning(f"Skipping {p}: no such file or directory")
            continue
        else:
            log.warning(f"Skipping {p}: not a file or directory")
            continue
        for candidate in candidates:
            found = _video_file(*candidate, exts, skip_symlinks)
            if found and found[1] not in seen:
                seen.add(found[1])
                yield found[0]


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


def ask_about_existing_backups(count: int) -> str:
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
    choices = {"d": "replace", "n": "number", "q": "quit"}
    while True:
        try:
            answer = input(question).strip().lower()
        except EOFError:
            print()
            log.info("No answer given; new backups will be numbered (.bak.1, .bak.2, ...).")
            return "number"
        if answer in choices:
            return choices[answer]


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    """Parse and check the command line (sys.argv's, unless argv is given).
    Invalid options exit with argparse's usage message and code 2."""
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="Video files and folders to process")
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
                         "one to ask (cron, Docker), new backups are numbered")
    ap.add_argument("--prefer-lang", default=None, metavar="LANG",
                    help="Language the stereo track must be in, as a 2- or 3-letter code "
                         "(en, eng, de, ger and deu all work); also picks between several "
                         "stereo tracks (default: the language of the track that plays by "
                         "default now)")
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
                    help="Hide the progress bars (useful for logs from cron or CI)")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                    help="Remux up to N files at once (default: 1). The work is limited by "
                         "disk speed, not CPU, so choose N for what your storage can handle. "
                         "Above 1, only the overall progress bar is shown, and a file's "
                         "[i/N] header is repeated when its lines follow another file's")
    args = ap.parse_args(argv)

    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    if args.existing_backups and not args.backup:
        ap.error("--existing-backups only applies with --backup; add --backup to keep backups")
    if args.prefer_lang is not None and not re.fullmatch("[a-z]{3}",
                                                         normalize_language(args.prefer_lang)):
        ap.error(f"--prefer-lang {args.prefer_lang!r} isn't a language code; use a 2- or "
                 f"3-letter code such as en or eng")
    return args


def _tell(args: argparse.Namespace, line: str) -> None:
    """Log line, and with --log-file print it too, so it stays on the console
    (where a log file only sends warnings and errors)."""
    log.info(line)
    if args.log_file:
        print(line)


def find_files(args: argparse.Namespace) -> list[Path]:
    """Every file to process, sorted: the ones under args.paths with an
    extension from --ext (or DEFAULT_EXTS). See iter_files()."""
    if args.ext:
        exts = {("." + e.strip().lstrip(".")).lower() for e in args.ext.split(",")}
    else:
        exts = DEFAULT_EXTS
    return sorted(set(iter_files(args.paths, exts, not args.no_recursive,
                                 args.skip_symlinks, args.follow_symlinks)))


def choose_backup_mode(args: argparse.Namespace, files: list[Path]) -> str:
    """What to do with backups when <name>.bak already exists: "replace",
    "number" or "quit". --existing-backups decides if given. Otherwise,
    if any of files has one, the user is asked (see
    ask_about_existing_backups()), or new backups are numbered when no one
    can answer, since that never deletes anything."""
    if args.existing_backups:
        return args.existing_backups
    with_backup = sum(1 for f in files if f.with_name(f.name + ".bak").exists())
    if not with_backup:
        return "number"
    if _can_ask():
        return ask_about_existing_backups(with_backup)
    log.info(f"{with_backup} file(s) already have a backup; new backups will be "
             f"numbered (.bak.1, .bak.2, ...). Use --existing-backups to choose.")
    return "number"


def process_all(files: list[Path], args: argparse.Namespace, stats: dict[str, int]) -> None:
    """Process every file, adding each outcome to stats ("changed": 3, ...).
    A stop (Ctrl+C, SIGTERM) comes out as KeyboardInterrupt, with the
    progress bars closed and stats holding the files that finished.

    How a run is shown:
    - --jobs 1 handles one file at a time, with a progress bar for the
      current file above the overall one. --jobs N > 1 handles N at once in
      threads (the work waits on disk, not CPU) and shows only the overall
      bar. With --log-file and no bars, a "Processing i/N..." counter takes
      their place.
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
    use_bar = HAVE_TQDM and not args.no_progress
    counter = args.log_file and not use_bar
    bars, overall = [], None
    if use_bar:
        first_row = 1 if args.jobs == 1 else 0
        bars.append(tqdm(total=1, position=first_row, bar_format="{desc}", desc="", leave=False))
        overall = tqdm(total=len(files), unit="file", desc="Processing", position=first_row + 1,
                       bar_format="{l_bar}{bar}| {n:.2f}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
                       leave=False)
        bars.append(overall)
    overall_lock = threading.Lock()

    def advance_overall(delta: float) -> None:
        """Move the overall bar by delta files. Several threads report at once
        with --jobs > 1, so updates go through a lock."""
        if overall is None:
            return
        with overall_lock:
            overall.n = min(round(overall.n + delta, 6), overall.total)
            overall.refresh()

    def run_one(i: int, f: Path) -> str:
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
            result = process_file(f, args, on_progress=on_progress if overall else None)
        if overall:
            advance_overall(1.0 - last_reported)
        return result

    def show_counter(n: int) -> None:
        """Show "Processing n/N..." in place, when it stands in for the bars."""
        if counter:
            print(f"\rProcessing {n}/{len(files)}...", end="", flush=True)

    try:
        if args.jobs == 1:
            for i, f in enumerate(files, 1):
                show_counter(i)
                result = run_one(i, f)
                stats[result] = stats.get(result, 0) + 1
        else:
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = [pool.submit(run_one, i, f) for i, f in enumerate(files, 1)]
                for done, fut in enumerate(as_completed(futures), 1):
                    show_counter(done)
                    result = fut.result()
                    stats[result] = stats.get(result, 0) + 1
    finally:
        for bar in reversed(bars):
            with contextlib.suppress(Exception):
                bar.close()
    if overall or counter:
        print()


def print_summary(stats: dict[str, int], args: argparse.Namespace, partial: bool = False,
                  cancelled: int = 0) -> None:
    """Log the counts, printing them too with --log-file (see _tell()). In a
    dry run nothing was changed, so the heading says so and "Changed" reads
    "Would change". partial marks a run that was stopped, where cancelled
    files didn't finish. Each line, and the heading's note, starts with a
    capital letter."""
    notes = (["partial -- interrupted"] if partial else []) + (
        ["dry run, nothing was changed"] if args.dry_run else [])
    note = "; ".join(notes)
    heading = "Summary" + (f" ({note[:1].upper()}{note[1:]})" if note else "")
    _tell(args, "")
    _tell(args, f"----- {heading} -----")
    for k in ("changed", "unchanged", "skipped", "error"):
        name = "would change" if k == "changed" and args.dry_run else k
        _tell(args, f"{name.capitalize()}: {stats[k]}")
    if cancelled:
        _tell(args, f"Cancelled: {cancelled}")


def main(argv: list[str] | None = None) -> int:
    """Run the command line: find the files, process each one, and print a
    summary. Returns the exit code: 0 when done, 1 if no files were found,
    a tool is missing or any file had an error, or 128 + the signal number
    when stopped (130 for Ctrl+C, 143 for SIGTERM). Invalid options exit
    with 2 straight from parse_args().

    When stopped while files are being processed, the stop handler has
    already killed every remux and each one has removed its temp file.
    What's left is a partial summary that counts unfinished files as
    cancelled. A stop while looking for files, or at the backup question,
    ends the run with no summary, since nothing has changed. Files that
    couldn't be given their original owner are reported in one warning
    just before the summary (report_ownership_failures()), with the full
    list saved next to --log-file, or in the current folder.
    """
    args = parse_args(argv)
    setup_logging(args.log_file)
    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    try:
        files = find_files(args)
    except KeyboardInterrupt as exc:
        signum, reason = _stop_reason(exc)
        log.error(f"{reason} while looking for files. No files were changed.")
        return 128 + signum
    if not files:
        log.error("No matching files found.")
        return 1
    if not check_tools(need_mkvmerge=any(f.suffix.lower() in MKV_EXTS for f in files)):
        return 1
    _tell(args, f"Found {len(files)} file(s){' (dry run)' if args.dry_run else ''}.")

    if args.backup and not args.dry_run:
        try:
            args.backup = choose_backup_mode(args, files)
        except KeyboardInterrupt as exc:
            signum, reason = _stop_reason(exc)
            print()
            log.error(f"{reason}. No files were changed.")
            return 128 + signum
        if args.backup == "quit":
            _tell(args, "Quit before changing any files.")
            return 0

    stats = {"changed": 0, "unchanged": 0, "skipped": 0, "error": 0}
    list_folder = Path(args.log_file).parent if args.log_file else Path.cwd()
    try:
        process_all(files, args, stats)
    except KeyboardInterrupt as exc:
        signum, reason = _stop_reason(exc)
        print()
        log.error(f"{reason}. In-flight remuxes were stopped and their "
                  "partial temp files removed; already-finished files are unaffected.")
        report_ownership_failures(list_folder)
        print_summary(stats, args, partial=True, cancelled=len(files) - sum(stats.values()))
        return 128 + signum

    report_ownership_failures(list_folder)
    print_summary(stats, args)
    return 1 if stats["error"] else 0


if __name__ == "__main__":
    sys.exit(main())
