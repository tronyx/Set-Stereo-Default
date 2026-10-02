#!/usr/bin/env python3
"""
set_stereo_default.py -- make the stereo audio track play by default.

Many video files flag a 5.1 or 7.1 track as the default audio, which sounds
thin or quiet on TV speakers, soundbars and laptops. This script finds the
2-channel (stereo) track in each file and makes it the default instead,
clearing the flag from every other audio track. Nothing is re-encoded.

Requirements (on your PATH):
  - ffmpeg and ffprobe 4.4 or newer    https://ffmpeg.org
  - mkvmerge, part of MKVToolNix       https://mkvtoolnix.download
    (only needed for .mkv/.webm files)
  - tqdm, optional (pip install tqdm) for progress bars

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
  python3 set_stereo_default.py /path/to/videos
  python3 set_stereo_default.py /path/to/videos --dry-run
  python3 set_stereo_default.py file1.mkv file2.mp4
  python3 set_stereo_default.py /path/to/videos --ext mkv,mp4 --no-recursive
  python3 set_stereo_default.py /path/to/videos --follow-symlinks
  python3 set_stereo_default.py /path/to/videos --prefer-lang eng
  python3 set_stereo_default.py /path/to/videos --avi-reorder
  python3 set_stereo_default.py /path/to/videos --backup
  python3 set_stereo_default.py /path/to/videos --force
  python3 set_stereo_default.py /path/to/videos --no-progress
  python3 set_stereo_default.py /path/to/videos --log-file run.log
  python3 set_stereo_default.py /path/to/videos --jobs 4
"""

import argparse
import json
import logging
import os
import re
import shlex
import shutil
import signal
import subprocess
import sys
import threading
import time
from collections import deque
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

try:
    from tqdm import tqdm
    HAVE_TQDM = True
except ImportError:
    HAVE_TQDM = False

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

    def emit(self, record):
        try:
            tqdm.write(self.format(record))
        except Exception:
            self.handleError(record)


def setup_logging(log_file):
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


_active_procs = set()
"""Running mkvmerge/ffmpeg remuxes, so a stop can kill them."""

_active_procs_lock = threading.RLock()
"""Guards _active_procs. Reentrant because the stop handler takes it in the
main thread, which may be the thread it interrupted while holding it."""

_cancelled = threading.Event()
"""Set once Ctrl+C or SIGTERM arrives, so files that haven't started are skipped."""

_chown_warned = threading.Event()
"""Set once the "couldn't keep the owner" warning is shown, so it's shown once per run."""


def _terminate_active_procs():
    """Stop every running remux: terminate() first, then kill() anything
    still running after 5 seconds. The 5 seconds is shared, not per
    process, so stopping never takes longer however many --jobs run."""
    with _active_procs_lock:
        procs = list(_active_procs)
    for proc in procs:
        try:
            proc.terminate()
        except Exception:
            pass
    deadline = time.monotonic() + 5
    for proc in procs:
        try:
            proc.wait(timeout=max(0, deadline - time.monotonic()))
        except Exception:
            try:
                proc.kill()
            except Exception:
                pass


class Stopped(KeyboardInterrupt):
    """Raised by _stop_handler(); signum says which signal arrived. It's a
    KeyboardInterrupt, so everything that cleans up after Ctrl+C also
    cleans up after SIGTERM."""

    def __init__(self, signum):
        super().__init__()
        self.signum = signum


def _stop_handler(signum, frame):
    """Handle Ctrl+C (SIGINT) and SIGTERM: kill every running remux, then
    raise Stopped in the main thread.

    The remuxes have to be killed here for two reasons. Python raises the
    exception only in the main thread, so with --jobs > 1 a worker waiting
    on its remux would never notice it. And SIGTERM (docker stop, kill,
    systemd) reaches only this process, not the remuxes it started."""
    _cancelled.set()
    _terminate_active_procs()
    raise Stopped(signum)


def _stop_reason(exc):
    """Return (signal number, message) for a stop: a Stopped from
    _stop_handler(), or a plain KeyboardInterrupt, which counts as Ctrl+C."""
    signum = getattr(exc, "signum", signal.SIGINT)
    if signum == signal.SIGINT:
        return signum, "Interrupted by user (Ctrl+C)"
    return signum, f"Stopped by {signal.Signals(signum).name}"


def run(cmd, **kw):
    """Run a quick command (e.g. ffprobe) and capture its output. Remuxes use
    run_with_progress() instead.

    Output is read as UTF-8, which ffprobe, ffmpeg and mkvmerge (given
    --output-charset UTF-8) all write, rather than in the system's own
    encoding: on Windows that's usually cp1252, which garbles non-English
    track names or fails on them outright. A byte that isn't valid UTF-8
    is replaced rather than stopping the run."""
    return subprocess.run(cmd, capture_output=True, encoding="utf-8", errors="replace", **kw)


def run_with_progress(cmd, label, show_progress, parse_pct, position=0, on_progress=None):
    """Run a remux, reading its output as it arrives so progress can be shown
    live. Returns (returncode, output), where output is the last 50 lines
    that aren't progress updates: that's where warnings and errors end up.

    parse_pct(line) returns 0-100 for a progress line and None for anything
    else. With show_progress, a tqdm bar titled label shows this file's
    progress at the given row position. on_progress(pct), if given, is
    called on every increase and with 100 on success; main() uses it to
    move the overall bar. Output is read as UTF-8, as in run().

    The process is listed in _active_procs while it runs and is killed if
    anything goes wrong, so it's never left running on its own. If a stop
    was already requested, it's killed straight away. It's listed before
    that check, and the stop handler sets _cancelled before reading the
    list, so a remux starting at the same moment as a stop can't slip
    through."""
    bar = None
    last_pct = 0
    lines = deque(maxlen=50)
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             encoding="utf-8", errors="replace", bufsize=1)
    try:
        with _active_procs_lock:
            _active_procs.add(proc)
        if _cancelled.is_set():
            proc.kill()
        if show_progress and HAVE_TQDM:
            bar = tqdm(total=100, desc=f"  {label}"[:40], unit="%", leave=False, position=position)
        for line in proc.stdout:
            pct = parse_pct(line)
            if pct is None:
                lines.append(line)
            else:
                pct = max(0, min(100, pct))
                if pct > last_pct:
                    if bar:
                        bar.update(pct - last_pct)
                    last_pct = pct
                    if on_progress:
                        on_progress(pct)
        proc.wait()
        if last_pct < 100 and proc.returncode == 0:
            if bar:
                bar.update(100 - last_pct)
            if on_progress:
                on_progress(100)
    except BaseException:
        proc.kill()
        proc.wait()
        raise
    finally:
        proc.stdout.close()
        with _active_procs_lock:
            _active_procs.discard(proc)
        if bar:
            bar.close()
    return proc.returncode, "".join(lines)


def check_tools(need_mkvmerge):
    """Exit with install links if ffmpeg or ffprobe isn't on PATH, or
    mkvmerge isn't when there are .mkv/.webm files to process."""
    missing = []
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            missing.append(tool)
    if need_mkvmerge and shutil.which("mkvmerge") is None:
        missing.append("mkvmerge (install MKVToolNix)")
    if missing:
        log.error("Missing required tool(s): " + ", ".join(missing))
        log.error("Install ffmpeg (https://ffmpeg.org) and, for .mkv/.webm files, "
                  "MKVToolNix (https://mkvtoolnix.download), then re-run.")
        sys.exit(1)


def probe_audio_streams(path):
    """Return (audio streams, duration in seconds) for path, or (None, None)
    if ffprobe can't read it. Each stream is a dict with its index, channel
    count, codec, language, names, and default/commentary/audio-description
    flags. The duration is None if unknown; ffmpeg's progress bar needs it,
    so it comes from this same ffprobe call rather than a second one."""
    res = run([
        "ffprobe", "-v", "error", "-print_format", "json",
        "-show_format", "-show_streams", "-select_streams", "a", str(path),
    ])
    if res.returncode != 0:
        log.error(f"  ffprobe failed on {path.name}: {res.stderr.strip()}")
        return None, None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        log.error(f"  Could not parse ffprobe output for {path.name}")
        return None, None

    streams = []
    for s in data.get("streams", []):
        tags = s.get("tags", {}) or {}
        disposition = s.get("disposition", {}) or {}
        streams.append({
            "index": s["index"],
            "channels": s.get("channels"),
            "default": bool(disposition.get("default", 0)),
            "comment": bool(disposition.get("comment", 0)),
            "visual_impaired": bool(disposition.get("visual_impaired", 0)),
            "language": tags.get("language", ""),
            "names": [tags[k] for k in NAME_TAGS if tags.get(k)],
            "codec": s.get("codec_name", ""),
        })

    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None

    return streams, duration


def is_commentary(stream):
    """True for a commentary or audio-description track. These are often
    stereo but should never play by default. They're recognized by the
    file's own flags, or failing that by the track's name."""
    return bool(stream.get("comment") or stream.get("visual_impaired")
                or any(COMMENTARY_NAME_RE.search(n) for n in stream.get("names", ())))


def normalize_language(code):
    """Return code in one standard form, so different tags for the same
    language compare equal: lowercased, with any region or script part
    dropped ("pt-BR" -> "pt"), then mapped to its ISO 639-2/T code through
    LANGUAGE_ALIASES ("de" and "ger" -> "deu"). Codes that aren't in the
    table come back lowercased; an empty tag stays empty."""
    base = re.split(r"[-_]", (code or "").strip().lower(), maxsplit=1)[0]
    return LANGUAGE_ALIASES.get(base, base)


def choose_target(streams, prefer_lang):
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
    def describe(ss):
        """List tracks for a skip note, e.g. "stream#2 (eng/aac), stream#3 (spa/ac3)"."""
        return ", ".join(f"stream#{s['index']} ({s['language'] or 'und'}/{s['codec']})" for s in ss)

    stereo = [s for s in streams if s["channels"] == 2]
    candidates = [s for s in stereo if not is_commentary(s)]
    if not candidates:
        if stereo:
            return None, f"only 2-channel tracks are commentary/audio description [{describe(stereo)}]"
        return None, "no 2-channel audio track found"

    current = next((s for s in streams if s["default"]), streams[0])
    shown = prefer_lang or current["language"]
    wanted = normalize_language(shown)
    if wanted not in ("", "und"):
        in_lang = [s for s in candidates
                   if normalize_language(s["language"]) in ("", "und", wanted)]
        if not in_lang:
            return None, (f"no 2-channel track in '{shown}' [found {describe(candidates)}] "
                          f"-- use --prefer-lang to pick another language")
        exact = [s for s in in_lang if normalize_language(s["language"]) == wanted]
        candidates = exact if len(exact) == 1 else in_lang

    if len(candidates) == 1:
        return candidates[0], None
    return None, (
        f"multiple 2-channel tracks found [{describe(candidates)}] -- use --prefer-lang to disambiguate"
    )


def needs_change(streams, target_index):
    """True unless the target is already the only audio track flagged default."""
    return any((s["index"] == target_index) != s["default"] for s in streams)


def probe_layout(path):
    """Return (streams, duration) for path: every stream (type, codec,
    channels, language and default flag) and the duration in seconds, or
    None if unknown. Returns (None, None) if ffprobe can't read the file.
    Used to check a remux."""
    res = run([
        "ffprobe", "-v", "error", "-print_format", "json", "-show_entries",
        "stream=index,codec_type,codec_name,channels:stream_disposition=default:stream_tags=language"
        ":format=duration",
        str(path),
    ])
    if res.returncode != 0:
        return None, None
    try:
        data = json.loads(res.stdout)
    except json.JSONDecodeError:
        return None, None
    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return data.get("streams", []), duration


def verify_remux(orig_path, tmp_path, streams, target_index, reordered):
    """Check a finished remux before it replaces the original. Returns None if
    it looks right, otherwise a short reason why not.

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
    target's codec, channel count and language."""
    before, before_duration = probe_layout(orig_path)
    after, after_duration = probe_layout(tmp_path)
    if before is None or after is None:
        return "ffprobe couldn't read the file"
    if len(after) != len(before):
        return f"stream count changed from {len(before)} to {len(after)}"
    if before_duration and after_duration is not None:
        allowed = max(before_duration * MAX_DURATION_LOSS, MIN_DURATION_LOSS)
        if after_duration < before_duration - allowed:
            return (f"duration dropped from {before_duration:.1f}s to {after_duration:.1f}s; "
                    f"the original may be incomplete")

    audio = [s for s in after if s.get("codec_type") == "audio"]
    if len(audio) != len(streams):
        return f"expected {len(streams)} audio tracks, found {len(audio)}"

    target = next(s for s in streams if s["index"] == target_index)
    if reordered:
        first = audio[0]
        lang = (first.get("tags") or {}).get("language", "")
        if (first.get("codec_name") != target["codec"] or first.get("channels") != target["channels"]
                or (target["language"] and lang != target["language"])):
            return "target audio track didn't end up first"
        return None

    expected = audio[streams.index(target)]["index"]
    defaults = [s["index"] for s in audio if (s.get("disposition") or {}).get("default")]
    if defaults != [expected]:
        found = ", ".join(f"stream#{i}" for i in defaults) or "no track"
        return f"default flag is on {found}, expected only stream#{expected}"
    return None


def backup_path(path, replace):
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


def make_backup(path, replace=False):
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


def copy_ownership(src, dst):
    """Give dst the same permissions and owner as src. A remux creates a new
    file owned by whoever ran the script, which could lock out tools that
    share your media through a group (Sonarr, Radarr, Plex, containers).

    Changing the owner needs root, and network shares often map root to
    "nobody", so that part can fail. If it does, a warning is logged once
    per run; the permissions are copied either way."""
    shutil.copymode(src, dst)
    if not hasattr(os, "chown"):
        return
    st = os.stat(src)
    try:
        os.chown(dst, st.st_uid, st.st_gid)
    except OSError as exc:
        if not _chown_warned.is_set():
            _chown_warned.set()
            log.warning(f"    Couldn't give remuxed files their original owner ({exc.strerror}); "
                        f"they'll belong to the user running this script. Their permissions "
                        f"still match the originals. Changing a file's owner needs root, and "
                        f"network shares often map root to 'nobody'.")


def swap_in(path, tmp_path, backup, keep_dates=False):
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


def check_and_swap_in(path, tmp_path, streams, target_index, reordered, backup, keep_dates=False):
    """Check a finished remux with verify_remux() and swap it in if it passes.
    Returns True if the original was replaced, False if the check failed
    (already logged).

    The temp file is removed if the check fails or anything interrupts this
    (Ctrl+C, SIGTERM, an unexpected error), so it's never left behind. After
    a successful swap there's no temp file left to remove."""
    try:
        problem = verify_remux(path, tmp_path, streams, target_index, reordered)
        if problem:
            log.error(f"    {path.name}: post-remux check failed ({problem}), keeping original untouched")
            tmp_path.unlink(missing_ok=True)
            return False
        swap_in(path, tmp_path, backup, keep_dates)
        return True
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise


def mkvmerge_audio_ids(path):
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


def apply_mkv(path, streams, target_index, dry_run, backup, show_progress=False, position=0,
              on_progress=None, keep_dates=False):
    """Remux an MKV/WebM file with mkvmerge so only target_index is flagged
    default. Returns True on success, False on failure (already logged).
    With dry_run, just logs the command.

    A full remux is used instead of an in-place mkvpropedit edit, which can
    move the track list to the end of the file and break Windows Explorer
    thumbnails. The remux is written to a temp file, and the original isn't
    touched until check_and_swap_in() has checked it.

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
    - Exit code 1 means it finished with warnings. They're logged, without
      mkvmerge's "#GUI#warning" and "Warning:" prefixes, and the file is
      still checked and used. A killed mkvmerge can also exit with 1 on
      Windows, so 1 only counts as finished if no stop was requested.
    """
    tmp_path = path.with_name(path.name + TMP_MARKER + path.suffix)

    ids = mkvmerge_audio_ids(path)
    if ids is None or len(ids) != len(streams):
        found = "couldn't read the file" if ids is None else f"sees {len(ids)} audio track(s)"
        log.error(f"    {path.name}: mkvmerge {found}, but ffprobe sees {len(streams)}; "
                  f"leaving the file alone")
        return False

    args = ["mkvmerge", "--gui-mode", "--output-charset", "UTF-8", "-o", str(tmp_path)]
    for s, track_id in zip(streams, ids):
        flag = "yes" if s["index"] == target_index else "no"
        args += ["--default-track", f"{track_id}:{flag}"]
    args += [str(path)]

    if dry_run:
        log.info("    [dry-run] " + shlex.join(args))
        return True

    pct_re = re.compile(r"#GUI#progress\s+(\d+)%")

    def parse_pct(line):
        """Read the percentage from mkvmerge's "#GUI#progress 42%" lines."""
        m = pct_re.search(line)
        return int(m.group(1)) if m else None

    try:
        returncode, output = run_with_progress(args, path.name, show_progress, parse_pct, position, on_progress)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    finished = returncode == 0 or (returncode == 1 and not _cancelled.is_set())
    if not finished or not tmp_path.exists():
        if _cancelled.is_set():
            log.info(f"    {path.name}: cancelled")
        else:
            log.error(f"    {path.name}: mkvmerge remux failed: {output.strip()}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return False

    if returncode == 1:
        warning_prefix_re = re.compile(r"^(?:#GUI#warning\s*)?(?:warning:\s*)?", re.IGNORECASE)
        warnings = [warning_prefix_re.sub("", line.strip()) for line in output.splitlines()
                    if "warning" in line.lower()]
        log.warning(f"    {path.name}: mkvmerge finished with warnings: "
                    + ("; ".join(warnings) or output.strip() or "(no details given)"))

    return check_and_swap_in(path, tmp_path, streams, target_index, False, backup, keep_dates)


def apply_remux(path, streams, target_index, dry_run, backup, reorder_for_avi,
                 duration=None, show_progress=False, position=0, on_progress=None,
                 keep_dates=False):
    """Remux any non-MKV file with ffmpeg (-c copy, so nothing is re-encoded)
    so only target_index is flagged default. Returns True on success, False
    on failure (already logged). With dry_run, just logs the command.

    With reorder_for_avi, an AVI file instead gets the target moved to the
    first audio track, since AVI has no default flag. MP4/M4V/MOV files get
    -movflags +faststart, which keeps the index at the front of the file
    where thumbnailers expect it. The remux is written to a temp file, and
    the original isn't touched until check_and_swap_in() has checked it.
    duration (seconds) drives the progress bar; without it, the bar only
    fills at the end.
    """
    suffix = path.suffix
    tmp_path = path.with_name(path.name + TMP_MARKER + suffix)

    audio_indices = [s["index"] for s in streams]
    reordered = reorder_for_avi and suffix.lower() in AVI_EXTS

    if reordered:
        others = [i for i in audio_indices if i != target_index]
        map_args = ["-map", "0:v?"]
        map_args += ["-map", f"0:{target_index}"]
        for i in others:
            map_args += ["-map", f"0:{i}"]
        map_args += ["-map", "0:s?", "-map", "0:d?"]
        disp_args = ["-disposition:a:0", "+default"]
        for out_idx in range(1, len(audio_indices)):
            disp_args += [f"-disposition:a:{out_idx}", "-default"]
    else:
        map_args = ["-map", "0"]
        disp_args = []
        for out_idx, s in enumerate(streams):
            flag = "+default" if s["index"] == target_index else "-default"
            disp_args += [f"-disposition:a:{out_idx}", flag]

    extra_args = []
    if suffix.lower() in MOV_FASTSTART_EXTS:
        extra_args += ["-movflags", "+faststart"]

    cmd = ["ffmpeg", "-y", "-v", "error", "-nostats", "-progress", "pipe:1",
           "-i", str(path)] + map_args + [
        "-c", "copy", "-map_metadata", "0",
    ] + disp_args + extra_args + [str(tmp_path)]

    if dry_run:
        log.info("    [dry-run] " + shlex.join(cmd))
        return True

    progress_re = re.compile(r"[a-z0-9_]+=")

    def parse_pct(line):
        """Read one line of ffmpeg's -progress output, a block of key=value
        lines per update. out_time_us gives the percentage done. Every
        other key returns 0: still a progress line, so it's kept out of
        error messages, but it doesn't move the bar."""
        line = line.strip()
        if not progress_re.match(line):
            return None
        if duration and line.startswith("out_time_us="):
            try:
                return int(int(line.split("=", 1)[1]) / 1_000_000 / duration * 100)
            except ValueError:
                pass
        return 0

    try:
        returncode, output = run_with_progress(cmd, path.name, show_progress, parse_pct, position, on_progress)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
    if returncode != 0 or not tmp_path.exists():
        if _cancelled.is_set():
            log.info(f"    {path.name}: cancelled")
        else:
            log.error(f"    {path.name}: ffmpeg remux failed: {output.strip()}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return False

    return check_and_swap_in(path, tmp_path, streams, target_index, reordered, backup, keep_dates)


def process_file(path, args, position=0, header="", on_progress=None):
    """Check one file, fix it if needed, and return "changed", "unchanged",
    "skipped" or "error". An unexpected error is logged and returned as
    "error" so one bad file doesn't stop the run.

    header is the file's "[i/N] path" line. It's logged in the same call
    as the outcome, so with --jobs > 1 another file's messages can't land
    between the two. position is the progress bar's row (--jobs 1 only).
    """
    try:
        return _process_file(path, args, position, header, on_progress)
    except Exception as exc:
        prefix = f"\n{header}\n" if header else ""
        log.error(f"{prefix}  {path.name}: unexpected error, skipping rest of file ({exc})")
        return "error"


def _process_file(path, args, position=0, header="", on_progress=None):
    """The work behind process_file(). For AVI files with --avi-reorder,
    "already correct" means the target is already the first audio track.
    --force remuxes even files that are already correct."""
    prefix = f"\n{header}\n" if header else ""
    ext = path.suffix.lower()
    is_avi_reorder = ext in AVI_EXTS and args.avi_reorder

    if ext in AVI_EXTS and not args.avi_reorder:
        log.info(f"{prefix}  {path.name}: SKIP (AVI has no reliable default-track flag; re-run "
                 f"with --avi-reorder to reorder streams instead, or convert to mkv)")
        return "skipped"

    streams, duration = probe_audio_streams(path)
    if streams is None:
        return "error"
    if not streams:
        log.info(f"{prefix}  {path.name}: no audio streams found, skipping")
        return "skipped"

    target, note = choose_target(streams, args.prefer_lang)
    if target is None:
        log.info(f"{prefix}  {path.name}: SKIP ({note})")
        return "skipped"

    if is_avi_reorder:
        changed = streams[0]["index"] != target["index"]
    else:
        changed = needs_change(streams, target["index"])

    if args.force:
        changed = True

    if not changed:
        what = "is first audio stream" if is_avi_reorder else "is default"
        log.info(f"{prefix}  {path.name}: already correct (stream#{target['index']} {what}), skipping")
        return "unchanged"

    action = "moving" if is_avi_reorder else "setting"
    outcome = "to the first audio track" if is_avi_reorder else "as default audio"
    log.info(f"{prefix}  {path.name}: {action} stream#{target['index']} "
             f"({target['language'] or 'und'}, {target['codec']}) {outcome}")

    show_progress = HAVE_TQDM and not args.no_progress and args.jobs == 1

    if ext in MKV_EXTS:
        ok = apply_mkv(path, streams, target["index"], args.dry_run, args.backup,
                        show_progress, position, on_progress, args.keep_dates)
    else:
        ok = apply_remux(path, streams, target["index"], args.dry_run,
                          args.backup, is_avi_reorder,
                          duration, show_progress, position, on_progress, args.keep_dates)

    return "changed" if ok else "error"


def _walk(folder, recursive, follow_symlinks):
    """Yield the path of every entry in folder that isn't a folder, and with
    recursive, in its subfolders too. os.walk() is used rather than
    Path.rglob(), whose handling of symlinked folders differs between
    Python versions.

    Symlinked subfolders are only searched with follow_symlinks; otherwise
    each one is logged, so it's clear why its files weren't found. Every
    folder is searched at most once, so a symlink loop can't make the
    search run forever."""
    visited = set()
    for dirpath, dirnames, filenames in os.walk(folder, followlinks=follow_symlinks):
        real = os.path.realpath(dirpath)
        if real in visited:
            dirnames[:] = []
            continue
        visited.add(real)
        if not recursive:
            dirnames[:] = []
        elif not follow_symlinks:
            for name in dirnames:
                if os.path.islink(os.path.join(dirpath, name)):
                    log.info(f"Not searching symlinked folder (use --follow-symlinks): "
                             f"{os.path.join(dirpath, name)}")
        for name in filenames:
            yield Path(dirpath) / name


def iter_files(paths, exts, recursive, skip_symlinks=False, follow_symlinks=False):
    """Yield every file in paths with an extension in exts. Files are used
    as given; folders are searched (into subfolders if recursive). The
    extension is checked before touching the disk, so non-video files
    (.nfo, .jpg, .srt, ...) cost nothing.

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
    for p in paths:
        p = Path(p)
        if p.is_file():
            candidates = [p]
        elif p.is_dir():
            candidates = _walk(p, recursive, follow_symlinks)
        elif not p.exists():
            log.warning(f"Skipping {p}: no such file or directory")
            continue
        else:
            log.warning(f"Skipping {p}: not a file or directory")
            continue
        for f in candidates:
            if f.suffix.lower() not in exts:
                continue
            if f.is_symlink():
                if skip_symlinks:
                    log.info(f"Skipping symlink (--skip-symlinks): {f}")
                    continue
                target = f.resolve()
                if target.suffix.lower() not in exts:
                    log.info(f"Skipping symlink to a file without a video extension: "
                             f"{f} -> {target}")
                    continue
                f = target
            if not f.is_file():
                continue
            if f.stem.endswith(TMP_MARKER):
                log.warning(f"Skipping leftover temp file from an interrupted run "
                            f"(safe to delete): {f}")
                continue
            real = f.resolve()
            if real in seen:
                continue
            seen.add(real)
            yield f


def ask_about_existing_backups(count):
    """Ask once what to do about files that already have a <name>.bak.
    Returns "replace", "number" or "quit". Asks again on any other answer;
    end of input (Ctrl+D) counts as quit."""
    question = (f"{count} file(s) already have a backup: [d]elete and replace them, "
                f"[n]umber new ones (.bak.1, .bak.2...), or [q]uit? ")
    choices = {"d": "replace", "n": "number", "q": "quit"}
    while True:
        try:
            answer = input(question).strip().lower()
        except EOFError:
            print()
            return "quit"
        if answer in choices:
            return choices[answer]


def main():
    """Run the command line: find the files, process each one, and print a
    summary. Exits 1 if no files were found, a tool is missing or any file
    had an error.

    How a run is shown:
    - --jobs 1 handles one file at a time, with a progress bar for the
      current file above the overall one. --jobs N > 1 handles N at once in
      threads (the work waits on disk, not CPU) and shows only the overall
      bar.
    - The overall bar counts fractions of files, so it keeps moving during
      a long remux. Its count is rounded and capped at the total, because
      adding up many small steps can drift just past it, which makes tqdm
      warn and show a negative time remaining.
    - The blank line between the bars is an empty tqdm bar, not a print():
      tqdm can't account for output it didn't write, and would draw the
      bars in the wrong place.
    - With --log-file, the "Found N file(s)" line and the summary are also
      printed, so they stay on the console.

    When stopped (Ctrl+C or SIGTERM), the stop handler has already killed
    every remux and each one has removed its temp file. What's left is to
    close the bars, print a partial summary that counts unfinished files as
    cancelled, and exit with 128 + the signal number (130 for Ctrl+C, 143
    for SIGTERM). With --jobs > 1, files still waiting in the queue return
    straight away once a stop is requested. A stop while still looking for
    files exits the same way, with no summary, since nothing has changed.
    """
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="Video files and/or folders to process")
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
                     help="Show what would change without touching any files")
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
                     help="Remux even files that are already correct, e.g. to fix thumbnails "
                          "on files an older version of this script edited in place")
    ap.add_argument("--log-file", default=None, metavar="PATH",
                     help="Write the details to this file instead of the console; warnings, "
                          "errors, the progress bar and the summary still show on the console")
    ap.add_argument("--no-progress", action="store_true",
                     help="Hide the progress bars (useful for logs from cron or CI)")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                     help="Remux up to N files at once (default: 1). The work is limited by "
                          "disk speed, not CPU, so choose N for what your storage can handle. "
                          "Above 1, only the overall progress bar is shown and log lines from "
                          "different files may interleave.")
    args = ap.parse_args()

    if args.jobs < 1:
        ap.error("--jobs must be >= 1")
    if args.prefer_lang is not None and not re.fullmatch("[a-z]{3}",
                                                         normalize_language(args.prefer_lang)):
        ap.error(f"--prefer-lang {args.prefer_lang!r} isn't a language code; use a 2- or "
                 f"3-letter code such as en or eng")

    setup_logging(args.log_file)
    signal.signal(signal.SIGINT, _stop_handler)
    signal.signal(signal.SIGTERM, _stop_handler)

    exts = {("." + e.strip().lstrip(".")).lower() for e in args.ext.split(",")} if args.ext else DEFAULT_EXTS
    try:
        files = sorted(set(iter_files(args.paths, exts, not args.no_recursive,
                                      args.skip_symlinks, args.follow_symlinks)))
    except KeyboardInterrupt as exc:
        signum, reason = _stop_reason(exc)
        log.error(f"{reason} while looking for files. No files were changed.")
        sys.exit(128 + signum)

    if not files:
        log.error("No matching files found.")
        sys.exit(1)

    need_mkv = any(f.suffix.lower() in MKV_EXTS for f in files)
    check_tools(need_mkv)

    header = f"Found {len(files)} file(s){' (dry run)' if args.dry_run else ''}."
    log.info(header)
    if args.log_file:
        print(header)

    if args.backup and not args.dry_run:
        with_backup = sum(1 for f in files if f.with_name(f.name + ".bak").exists())
        mode = args.existing_backups or "number"
        if with_backup and not args.existing_backups:
            if sys.stdin.isatty():
                try:
                    mode = ask_about_existing_backups(with_backup)
                except KeyboardInterrupt as exc:
                    signum, reason = _stop_reason(exc)
                    print()
                    log.error(f"{reason}. No files were changed.")
                    sys.exit(128 + signum)
            else:
                log.info(f"{with_backup} file(s) already have a backup; new backups will be "
                         f"numbered (.bak.1, .bak.2, ...). Use --existing-backups to choose.")
        if mode == "quit":
            log.info("Quit before changing any files.")
            if args.log_file:
                print("Quit before changing any files.")
            sys.exit(0)
        args.backup = mode

    stats = {"changed": 0, "unchanged": 0, "skipped": 0, "error": 0}
    use_bar = HAVE_TQDM and not args.no_progress
    show_fallback_counter = args.log_file and not use_bar

    def print_summary(partial=False, cancelled=0):
        """Log the counts, and print them too when the log goes to a file. In a
        dry run nothing was changed, so the heading says so and "changed"
        reads "would change"."""
        notes = (["partial -- interrupted"] if partial else []) + (
            ["dry run, nothing was changed"] if args.dry_run else [])
        heading = "Summary" + (" (" + "; ".join(notes) + ")" if notes else "")
        lines = ["", f"----- {heading} -----"]
        for k in ("changed", "unchanged", "skipped", "error"):
            name = "would change" if k == "changed" and args.dry_run else k
            lines.append(f"{name}: {stats[k]}")
        if cancelled:
            lines.append(f"cancelled: {cancelled}")
        for line in lines:
            log.info(line)
        if args.log_file:
            for line in lines:
                print(line)

    active_bars = []

    try:
        first_row = 1 if args.jobs == 1 else 0
        spacer = tqdm(total=1, position=first_row, bar_format="{desc}", desc="", leave=False) if use_bar else None
        overall = tqdm(total=len(files), unit="file", desc="Processing", position=first_row + 1,
                        bar_format="{l_bar}{bar}| {n:.2f}/{total_fmt} [{elapsed}<{remaining}, {rate_fmt}]",
                        leave=False) if use_bar else None
        if use_bar:
            active_bars += [spacer, overall]
        overall_lock = threading.Lock()

        def advance_overall(delta):
            """Move the overall bar by delta files. Several threads report at
            once with --jobs > 1, so updates go through a lock."""
            with overall_lock:
                overall.n = min(round(overall.n + delta, 6), overall.total)
                overall.refresh()

        def run_one(i, f):
            """Process file number i, keeping the overall bar in step. Returns
            "cancelled" without starting if a stop was already requested."""
            if _cancelled.is_set():
                return "cancelled"
            last_reported = 0.0

            def on_progress(pct):
                """Move the overall bar by however much this file has
                progressed since its last report."""
                nonlocal last_reported
                frac = pct / 100.0
                advance_overall(frac - last_reported)
                last_reported = frac

            result = process_file(f, args, header=f"[{i}/{len(files)}] {f}",
                                   on_progress=on_progress if overall else None)

            if overall:
                advance_overall(1.0 - last_reported)
            return result

        if args.jobs == 1:
            for i, f in enumerate(files, 1):
                if show_fallback_counter:
                    print(f"\rProcessing {i}/{len(files)}...", end="", flush=True)
                result = run_one(i, f)
                stats[result] = stats.get(result, 0) + 1
        else:
            completed = 0
            with ThreadPoolExecutor(max_workers=args.jobs) as pool:
                futures = {pool.submit(run_one, i, f): f for i, f in enumerate(files, 1)}
                for fut in as_completed(futures):
                    completed += 1
                    if show_fallback_counter:
                        print(f"\rProcessing {completed}/{len(files)}...", end="", flush=True)
                    result = fut.result()
                    stats[result] = stats.get(result, 0) + 1

        if overall:
            overall.close()
            spacer.close()
            print()
        if show_fallback_counter:
            print()
    except KeyboardInterrupt as exc:
        signum, reason = _stop_reason(exc)
        for bar in active_bars:
            try:
                bar.close()
            except Exception:
                pass
        print()
        log.error(f"{reason}. In-flight remuxes were stopped and their "
                  "partial temp files removed; already-finished files are unaffected.")
        print_summary(partial=True, cancelled=len(files) - sum(stats.values()))
        sys.exit(128 + signum)

    print_summary()

    if stats["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()