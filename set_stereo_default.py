#!/usr/bin/env python3
"""
set_stereo_default.py

Bulk-scan a folder of video files (.mkv, .mp4, .m4v, .mov, .avi, ...) and make
sure the 2-channel (stereo) audio track is the one flagged "default", clearing
the default flag from every other audio track.

Requires on PATH:
  - ffmpeg / ffprobe   (https://ffmpeg.org)
  - mkvmerge           (part of MKVToolNix, https://mkvtoolnix.download)
    -> only needed for .mkv/.webm files.

Optional:
  - tqdm (pip install tqdm) -> live progress bars. Without it you still get
    each file's "[i/N]" line, just no bars.

How it decides the target track:
  Each file's audio streams are inspected by channel count. The one stream
  with exactly 2 channels becomes "default"; every other audio stream has
  its default flag cleared. If zero or more than one 2-channel track
  exists, the file is skipped (use --prefer-lang to break ties).

How it applies the change:
  - .mkv/.webm -> a clean remux via mkvmerge (lossless, no re-encoding),
    not an in-place edit via mkvpropedit: editing in place can push the
    file's track metadata to the end of the file, which is exactly the
    shape of file that breaks Windows Explorer's thumbnail generation even
    though the file plays fine everywhere else.
  - .mp4/.m4v/.mov -> an ffmpeg remux (-c copy) with -movflags +faststart,
    so the moov atom stays at the front of the file for the same reason.
    Any other extension added via --ext is remuxed by ffmpeg the same way,
    minus faststart.
  - .avi -> AVI has no real "default track" flag. --avi-reorder instead
    makes the target stream the first audio stream (the closest
    equivalent most players honor); without that flag, AVI files are
    skipped.

Usage:
  python3 set_stereo_default.py /path/to/videos
  python3 set_stereo_default.py /path/to/videos --dry-run
  python3 set_stereo_default.py file1.mkv file2.mp4
  python3 set_stereo_default.py /path/to/videos --ext mkv,mp4 --no-recursive
  python3 set_stereo_default.py /path/to/videos --prefer-lang eng
  python3 set_stereo_default.py /path/to/videos --avi-reorder
  python3 set_stereo_default.py /path/to/videos --backup
  python3 set_stereo_default.py /path/to/videos --force
  python3 set_stereo_default.py /path/to/videos --no-progress
  python3 set_stereo_default.py /path/to/videos --log-file run.log
  python3 set_stereo_default.py /path/to/videos --jobs 4

Safe by default:
  - Already-correct files are skipped; --dry-run previews changes without
    touching anything.
  - Every remux goes to a temp file first and only replaces the original
    after a sanity check passes.
  - --backup keeps the pre-change original as "<name>.bak" (a hard link
    where supported, so it takes no extra space).
  - --force re-applies even to files that already look correct, e.g. to
    give the thumbnail-friendly layout above to files an older version of
    this script edited in place.
  - Ctrl+C stops cleanly: in-flight remuxes are killed and their partial
    temp files removed; already-finished files are unaffected.
"""

import argparse
import json
import logging
import os
import re
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
MKV_EXTS = {".mkv", ".webm"}
AVI_EXTS = {".avi"}
MOV_FASTSTART_EXTS = {".mp4", ".m4v", ".mov"}

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
    """Attach one handler to the module logger: a FileHandler when --log-file
    is given, otherwise a console handler that writes via tqdm.write() so it
    won't clobber an active progress bar."""
    log.setLevel(logging.INFO)
    log.propagate = False
    if log_file:
        handler = logging.FileHandler(log_file, mode="a", encoding="utf-8")
        handler.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    else:
        handler = TqdmLoggingHandler() if HAVE_TQDM else logging.StreamHandler(sys.stdout)
        handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)


_active_procs = set()
_active_procs_lock = threading.Lock()
_cancelled = threading.Event()


def _terminate_active_procs():
    """Best-effort stop of every running mkvmerge/ffmpeg subprocess:
    terminate() first, then kill() anything still alive after 5s. The 5s is
    one shared deadline for all of them, not 5s each, so Ctrl+C never waits
    longer than that however many --jobs are running."""
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


def _sigint_handler(signum, frame):
    """Ctrl+C handler, always run in the main thread. Kills every
    in-flight subprocess before raising the normal KeyboardInterrupt --
    necessary because Python only ever delivers KeyboardInterrupt to the
    main thread, so under --jobs > 1 a worker thread blocked reading its
    own subprocess's output would otherwise never notice a Ctrl+C and
    would keep that subprocess running as an orphan."""
    _cancelled.set()
    _terminate_active_procs()
    signal.default_int_handler(signum, frame)


def run(cmd, **kw):
    """Run cmd to completion and capture its output; for short,
    non-interactive calls (ffprobe queries, sanity checks). For a
    long-running remux that should drive a live progress bar, use
    run_with_progress() instead."""
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def run_with_progress(cmd, label, show_progress, parse_pct, position=0, on_progress=None):
    """Run cmd, streaming stdout+stderr line by line so a live bar can be
    driven while the subprocess is still running. Returns (returncode,
    output), where output is only the last 50 lines -- that's where a
    failure's error message ends up, and ffmpeg's -progress output would
    otherwise pile up thousands of lines on a long file.

    parse_pct(line) returns an int 0-100 for progress lines, else None.
    show_progress/position control an optional tqdm bar; label (truncated)
    is shown on it. on_progress(pct), if given, fires on every increase
    (and once more with 100 on success) independently of the bar -- used
    by main()'s concurrent path to keep the overall bar's count live even
    though individual files get no bar of their own there.

    The subprocess is tracked in _active_procs for the duration of the
    call and killed on any exception -- including a Ctrl+C landing in
    this thread directly, which only happens when running sequentially --
    so it's never left running as an orphan.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
    with _active_procs_lock:
        _active_procs.add(proc)
    bar = None
    if show_progress and HAVE_TQDM:
        bar = tqdm(total=100, desc=f"  {label}"[:40], unit="%", leave=False, position=position)
    last_pct = 0
    lines = deque(maxlen=50)
    try:
        for line in proc.stdout:
            lines.append(line)
            pct = parse_pct(line)
            if pct is not None:
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
        with _active_procs_lock:
            _active_procs.discard(proc)
        if bar:
            bar.close()
    return proc.returncode, "".join(lines)


def check_tools(need_mkvmerge):
    """Exit with an install hint if ffmpeg/ffprobe (or mkvmerge, when the
    file set includes .mkv/.webm) aren't on PATH."""
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
    """Return (list of audio stream dicts, container duration in seconds or
    None) in file order, or (None, None) if ffprobe fails.

    -show_format rides along on this same call so apply_remux() gets the
    duration it needs to report progress, without a second ffprobe per file."""
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
        streams.append({
            "index": s["index"],
            "channels": s.get("channels"),
            "default": bool(s.get("disposition", {}).get("default", 0)),
            "language": tags.get("language", ""),
            "title": tags.get("title", ""),
            "codec": s.get("codec_name", ""),
        })

    try:
        duration = float(data.get("format", {}).get("duration"))
    except (TypeError, ValueError):
        duration = None

    return streams, duration


def choose_target(streams, prefer_lang):
    """Return (stream, note): the 2-channel audio stream to mark default.
    stream is None if none qualify -- either no 2-channel track exists, or
    multiple do and --prefer-lang didn't narrow it to exactly one; note
    explains the skip in both cases."""
    candidates = [s for s in streams if s["channels"] == 2]
    if not candidates:
        return None, "no 2-channel audio track found"
    if len(candidates) == 1:
        return candidates[0], None
    if prefer_lang:
        lang_matches = [s for s in candidates if s["language"].lower() == prefer_lang.lower()]
        if len(lang_matches) == 1:
            return lang_matches[0], None
    desc = ", ".join(
        f"stream#{s['index']} ({s['language'] or 'und'}/{s['codec']})" for s in candidates
    )
    return None, (
        f"multiple 2-channel tracks found [{desc}] -- use --prefer-lang to disambiguate"
    )


def needs_change(streams, target_index):
    """Return True if any audio stream's default flag differs from what it
    should be (set on the target stream, cleared on every other one)."""
    return any((s["index"] == target_index) != s["default"] for s in streams)


def make_backup(path):
    """Keep the pre-change original as <name>.bak. A hard link is instant
    and takes no extra space -- once os.replace() swaps the new file in,
    the original's data stays reachable through the .bak link -- so a full
    copy is only made where hard links aren't supported."""
    bak_path = path.with_name(path.name + ".bak")
    bak_path.unlink(missing_ok=True)
    try:
        os.link(path, bak_path)
    except OSError:
        shutil.copy2(path, bak_path)


def apply_mkv(path, streams, target_index, dry_run, backup, show_progress=False, position=0,
              on_progress=None):
    """Clean single-pass remux via mkvmerge (not an in-place mkvpropedit
    edit -- see the module docstring). mkvmerge track IDs happen to match
    ffprobe's stream index for mkv containers, so each stream's ffprobe
    index doubles as its mkvmerge TID below. Returns True on success,
    False on failure (already logged).
    """
    tmp_path = path.with_name(path.name + ".tmp_remux" + path.suffix)

    args = ["mkvmerge", "--gui-mode", "-o", str(tmp_path)]
    for s in streams:
        flag = "yes" if s["index"] == target_index else "no"
        args += ["--default-track-flag", f"{s['index']}:{flag}"]
    args += [str(path)]

    if dry_run:
        log.info("    [dry-run] " + " ".join(args))
        return True

    pct_re = re.compile(r"#GUI#progress\s+(\d+)%")

    def parse_pct(line):
        m = pct_re.search(line)
        return int(m.group(1)) if m else None

    try:
        returncode, output = run_with_progress(args, path.name, show_progress, parse_pct, position, on_progress)
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # never the original file; always safe to discard
        raise
    if returncode != 0 or not tmp_path.exists():
        if _cancelled.is_set():
            log.info("    cancelled (Ctrl+C)")
        else:
            log.error(f"    mkvmerge remux failed: {output.strip()}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return False

    check = run(["ffprobe", "-v", "error", "-show_entries", "format=nb_streams",
                 "-of", "json", str(tmp_path)])
    if check.returncode != 0:
        log.error("    post-remux verification failed, keeping original untouched")
        tmp_path.unlink(missing_ok=True)
        return False

    if backup:
        make_backup(path)

    os.replace(tmp_path, path)
    return True


def apply_remux(path, streams, target_index, dry_run, backup, reorder_for_avi,
                 duration=None, show_progress=False, position=0, on_progress=None):
    """Remux with ffmpeg -c copy, setting disposition flags per stream (or,
    with reorder_for_avi, reordering streams so the target audio track is
    first, since AVI has no real "default" flag). mp4/m4v/mov gets
    -movflags +faststart so the moov atom stays at the front of the file
    (see the module docstring). A post-remux ffprobe sanity check runs
    before the atomic os.replace() that swaps the temp file in. Returns
    True on success, False on failure (already logged).
    """
    suffix = path.suffix
    tmp_path = path.with_name(path.name + ".tmp_remux" + suffix)

    audio_indices = [s["index"] for s in streams]

    if reorder_for_avi and suffix.lower() in AVI_EXTS:
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
        log.info("    [dry-run] " + " ".join(cmd))
        return True

    def parse_pct(line):
        line = line.strip()
        if duration and line.startswith("out_time_us="):
            try:
                out_time_us = int(line.split("=", 1)[1])
            except ValueError:
                return None
            return int(out_time_us / 1_000_000 / duration * 100)
        return None

    try:
        returncode, output = run_with_progress(cmd, path.name, show_progress, parse_pct, position, on_progress)
    except BaseException:
        tmp_path.unlink(missing_ok=True)  # never the original file; always safe to discard
        raise
    if returncode != 0 or not tmp_path.exists():
        if _cancelled.is_set():
            log.info("    cancelled (Ctrl+C)")
        else:
            log.error(f"    ffmpeg remux failed: {output.strip()}")
        if tmp_path.exists():
            tmp_path.unlink(missing_ok=True)
        return False

    check = run(["ffprobe", "-v", "error", "-show_entries", "format=nb_streams",
                 "-of", "json", str(tmp_path)])
    ok = check.returncode == 0
    if not ok:
        log.error("    post-remux verification failed, keeping original untouched")
        tmp_path.unlink(missing_ok=True)
        return False

    if backup:
        make_backup(path)

    os.replace(tmp_path, path)
    return True


def process_file(path, args, position=0, header="", on_progress=None):
    """Probe one file, decide whether its default-audio flag needs fixing,
    apply the fix, and return "changed"/"unchanged"/"skipped"/"error".

    AVI has no default flag, so for AVI files "already correct" means the
    target is already the first audio stream. --force re-applies even when
    the file already looks correct.

    header, if given, is the file's "[i/N] path" line, logged together
    with -- not separately from -- whatever outcome follows: under
    --jobs > 1 several files are decided concurrently, and logging the
    header and outcome as two separate calls let another file's messages
    land in between them. position is passed straight through to the
    per-file progress bar (--jobs 1 only; see main()'s docstring).
    """
    try:
        return _process_file(path, args, position, header, on_progress)
    except Exception as exc:
        prefix = f"\n{header}\n" if header else ""
        log.error(f"{prefix}  {path.name}: unexpected error, skipping rest of file ({exc})")
        return "error"


def _process_file(path, args, position=0, header="", on_progress=None):
    """Does the actual work for process_file(); split out so process_file can
    wrap it in one try/except without duplicating the wrapper's logic."""
    prefix = f"\n{header}\n" if header else ""

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

    ext = path.suffix.lower()
    is_avi_reorder = ext in AVI_EXTS and args.avi_reorder

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

    log.info(f"{prefix}  {path.name}: setting stream#{target['index']} "
             f"({target['language'] or 'und'}, {target['codec']}) as default audio")

    show_progress = HAVE_TQDM and not args.no_progress and args.jobs == 1  # per-file bar: --jobs 1 only

    if ext in MKV_EXTS:
        ok = apply_mkv(path, streams, target["index"], args.dry_run, args.backup,
                        show_progress, position, on_progress)
    elif ext in AVI_EXTS and not args.avi_reorder:
        log.info(f"    SKIP: AVI has no reliable default-track flag; re-run with "
                 f"--avi-reorder to reorder streams instead (or convert to mkv).")
        return "skipped"
    else:
        ok = apply_remux(path, streams, target["index"], args.dry_run,
                          args.backup, args.avi_reorder and ext in AVI_EXTS,
                          duration, show_progress, position, on_progress)

    return "changed" if ok else "error"


def iter_files(paths, exts, recursive):
    """Yield files from paths (files passed through directly, directories
    walked) whose extension is in exts. The extension is checked first so
    non-video files (.nfo, .jpg, .srt, ...) skip the is_file() disk check."""
    for p in paths:
        p = Path(p)
        if p.is_file():
            if p.suffix.lower() in exts:
                yield p
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            for f in it:
                if f.suffix.lower() in exts and f.is_file():
                    yield f


def main():
    """Parse args, discover matching files, process them (sequentially or
    concurrently, depending on --jobs), and print a final summary.

    The "Found N file(s)" header is logged (so it lands in --log-file too)
    and then printed again directly when --log-file is set, since it should
    stay visible on the console even though the per-file details are being
    routed to the log file instead.

    --jobs 1 processes files one at a time with a live per-file bar.
    --jobs N>1 submits every file to a ThreadPoolExecutor up front
    (threads, since the work waits on subprocesses rather than being
    CPU-bound) and shows only the overall bar -- with several files in
    flight there's no single row a per-file bar could usefully occupy.
    Either way, each file's "[i/N] path" header and outcome are logged
    together as one call (see process_file()'s docstring for why).

    The overall bar's count is a live, fractional sum of every in-flight
    file's own progress (via on_progress, see run_with_progress()) rather
    than only jumping by whole files as each completes, so it keeps moving
    during a long remux instead of sitting still until the file finishes.
    Its bar_format pins the count to 2 decimals to avoid binary-float
    noise (e.g. "2.4300000000000006"), and updates are serialized with a
    lock since under --jobs N>1 several worker threads report at once.

    tqdm row layout (top to bottom): under --jobs N>1, a spacer then the
    overall bar; under --jobs 1, the per-file bar then that same spacer
    and overall bar. The spacer is a real blank tqdm row rather than a
    plain print(), since tqdm doesn't know about output it didn't produce
    and would miscalculate cursor offsets around it.

    Ctrl+C is caught around the whole processing section: by then
    _sigint_handler (see above run()) has already killed every in-flight
    subprocess and each apply_mkv()/apply_remux() call has cleaned up its
    own partial temp file, so this just closes any open bars, prints a
    partial summary, and exits 130.
    """
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="Files and/or directories to process")
    ap.add_argument("--ext", default=None,
                     help="Comma-separated extensions to include (default: mkv,webm,mp4,m4v,mov,avi)")
    ap.add_argument("--no-recursive", action="store_true", help="Don't recurse into subdirectories")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, don't touch files")
    ap.add_argument("--backup", action="store_true",
                     help="Keep the pre-change original as <name>.bak for any remux "
                          "(a hard link where supported, so no extra disk space)")
    ap.add_argument("--prefer-lang", default=None,
                     help="If multiple 2-channel tracks exist, prefer this language code (e.g. eng)")
    ap.add_argument("--avi-reorder", action="store_true",
                     help="For .avi files, remux to put the target audio stream first "
                          "(AVI has no real 'default' flag)")
    ap.add_argument("--force", action="store_true",
                     help="Re-apply even to files that already look correct, e.g. to give "
                          "files an older version of this script edited in place the "
                          "thumbnail-friendly layout (a normal run would skip them, since "
                          "their default flags are already right).")
    ap.add_argument("--log-file", default=None, metavar="PATH",
                     help="Write detailed log output to this file instead of the console. "
                          "The console still shows a progress bar and the final summary.")
    ap.add_argument("--no-progress", action="store_true",
                     help="Disable the progress bar (e.g. for non-interactive/CI logs)")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                     help="Number of files to remux concurrently (default: 1 = sequential). "
                          "This work is I/O-bound, not CPU-bound, so pick a value based on "
                          "what your storage can sustain rather than core count. Above 1, "
                          "there's no per-file %% bar, just the overall batch bar; log lines "
                          "from different files may interleave.")
    args = ap.parse_args()

    if args.jobs < 1:
        ap.error("--jobs must be >= 1")

    setup_logging(args.log_file)
    signal.signal(signal.SIGINT, _sigint_handler)

    exts = {("." + e.strip().lstrip(".")).lower() for e in args.ext.split(",")} if args.ext else DEFAULT_EXTS
    files = sorted(set(iter_files(args.paths, exts, not args.no_recursive)))

    if not files:
        log.error("No matching files found.")
        sys.exit(1)

    need_mkv = any(f.suffix.lower() in MKV_EXTS for f in files)
    check_tools(need_mkv)

    header = f"Found {len(files)} file(s){' (dry run)' if args.dry_run else ''}."
    log.info(header)
    if args.log_file:
        print(header)

    stats = {"changed": 0, "unchanged": 0, "skipped": 0, "error": 0}
    use_bar = HAVE_TQDM and not args.no_progress
    show_fallback_counter = args.log_file and not use_bar

    def print_summary(label="Summary"):
        lines = ["", f"----- {label} -----"]
        for k in ("changed", "unchanged", "skipped", "error"):
            lines.append(f"{k}: {stats[k]}")
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

        def run_one(i, f):
            last_reported = 0.0

            def on_progress(pct):
                nonlocal last_reported
                frac = pct / 100.0
                with overall_lock:
                    overall.n += frac - last_reported
                    overall.refresh()
                last_reported = frac

            result = process_file(f, args, header=f"[{i}/{len(files)}] {f}",
                                   on_progress=on_progress if overall else None)

            if overall:
                with overall_lock:
                    overall.n += 1.0 - last_reported
                    overall.refresh()
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
    except KeyboardInterrupt:
        for bar in active_bars:
            try:
                bar.close()
            except Exception:
                pass
        print()
        log.error("Interrupted by user (Ctrl+C). In-flight remuxes were stopped and their "
                  "partial temp files removed; already-finished files are unaffected.")
        print_summary("Summary (partial -- interrupted)")
        sys.exit(130)

    print_summary()

    if stats["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()