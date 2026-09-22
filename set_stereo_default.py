#!/usr/bin/env python3
"""
set_stereo_default.py

Bulk-scan a folder of video files (.mkv, .mp4, .m4v, .mov, .avi, ...) and make
sure the 2-channel (stereo) audio track is the one flagged "default", clearing
the default flag from every other audio track.

Requires on PATH:
  - ffmpeg / ffprobe   (https://ffmpeg.org)
  - mkvmerge           (part of MKVToolNix, https://mkvtoolnix.download)
    -> only needed if you have .mkv/.webm files; not required for mp4/avi-only use.

Optional:
  - tqdm (pip install tqdm) -> enables a live progress bar while files are
    processed. Without it, a simple "[i/N]" counter is used instead.

How it decides the target track:
  For each file, it inspects every audio stream's channel count. The stream
  with exactly 2 channels becomes "default"; every other audio stream has its
  default flag cleared. If no 2-channel track exists, or more than one does,
  the file is skipped and logged (use --prefer-lang to break ties).

How it applies the change:
  - .mkv / .webm  -> mkvmerge does a clean single-pass remux (-c copy,
                     lossless) with --default-track-flag set per track.
                     Earlier versions of this script used mkvpropedit to
                     edit the header in place instead, which is faster but
                     has a real downside: when it needs to grow the Tracks
                     element (almost always, since most muxers don't bother
                     writing an explicit "default" flag when the default is
                     already true), it doesn't shift the file -- it voids
                     out the old Tracks slot and appends the new Tracks
                     element at the very end of the file, after the Cues,
                     reachable only via a SeekHead pointer. Full players
                     resolve that fine, but it's exactly the shape of file
                     that breaks a lightweight/bounded-read thumbnail
                     extractor -- which is why Windows Explorer thumbnails
                     can disappear for mkvpropedit-edited files even though
                     the file plays perfectly. mkvmerge always lays a fresh
                     mux out in the normal order (Tracks stays right after
                     the segment info, near the front), so this doesn't
                     happen. Attachments (cover art), subtitles and chapters
                     all survive the remux unchanged.
  - .mp4/.m4v/.mov and everything else ffmpeg can mux
                  -> ffmpeg remuxes with `-c copy` (no re-encode) into a temp
                     file and atomically replaces the original, with
                     `-movflags +faststart` so the moov atom (the index
                     players/thumbnailers read) stays at the front of the
                     file instead of the end -- the same class of "plays
                     fine, no thumbnail" problem as above, just mp4's
                     version of it.
  - .avi          -> AVI has no standard "default track" flag that players
                     reliably honor. With --avi-reorder, the script instead
                     re-muxes so the target audio stream is the FIRST audio
                     stream (closest real-world equivalent to "default" for
                     AVI, since most players that only look at one audio
                     track in an AVI use the first one). Without that flag,
                     AVI files are skipped with a warning.

Usage:
  python3 set_stereo_default.py /path/to/videos
  python3 set_stereo_default.py /path/to/videos --dry-run
  python3 set_stereo_default.py file1.mkv file2.mp4
  python3 set_stereo_default.py /path/to/videos --avi-reorder
  python3 set_stereo_default.py /path/to/videos --prefer-lang eng
  python3 set_stereo_default.py /path/to/videos --no-recursive --ext mkv,mp4
  python3 set_stereo_default.py /path/to/videos --backup
  python3 set_stereo_default.py /path/to/videos --force
  python3 set_stereo_default.py /path/to/videos --no-progress
  python3 set_stereo_default.py /path/to/videos --log-file run.log
  python3 set_stereo_default.py /path/to/videos --log-file run.log --no-progress
  python3 set_stereo_default.py /path/to/videos --jobs 4

Safe by default:
  - Skips any file already in the correct state (no unnecessary work).
  - --dry-run shows exactly what would change without touching anything.
  - Every remux (mkv, mp4, avi) goes to a temp file first and only replaces
    the original after the tool exits successfully and a sanity check
    passes; nothing is overwritten mid-write.
  - Use --backup to keep the pre-change original as "<name>.bak" too.
  - Use --force to re-apply to files that already look correct -- e.g. to
    fix up mkv/mp4 files an older version of this script already touched
    (their disposition flag was already right, so a normal run would skip
    them and leave the Tracks-at-the-end / moov-at-the-end problem in place).
"""

import argparse
import json
import logging
import os
import re
import shutil
import subprocess
import sys
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


def run(cmd, **kw):
    """Run cmd to completion and capture its output; for short,
    non-interactive calls (ffprobe queries, sanity checks). For a
    long-running remux that should drive a live progress bar, use
    run_with_progress() instead."""
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def run_with_progress(cmd, label, show_progress, parse_pct):
    """Run cmd, streaming its combined stdout+stderr line by line so a
    per-file progress bar can be driven while the subprocess is still
    running (plain subprocess.run only returns output after it exits).

    parse_pct(line) should return an int 0-100 for lines that carry
    progress info, or None otherwise. Returns (returncode, combined_output).

    The per-file bar renders at position=0, above the batch-level rows
    main() manages (see main()'s docstring for that row layout), so it's
    always the topmost line while a file is being processed.
    """
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                             text=True, bufsize=1)
    bar = None
    if show_progress and HAVE_TQDM:
        bar = tqdm(total=100, desc=f"  {label}"[:40], unit="%", leave=False, position=0)
    last_pct = 0
    lines = []
    for line in proc.stdout:
        lines.append(line)
        pct = parse_pct(line)
        if pct is not None and bar:
            pct = max(0, min(100, pct))
            if pct > last_pct:
                bar.update(pct - last_pct)
                last_pct = pct
    proc.wait()
    if bar:
        if last_pct < 100 and proc.returncode == 0:
            bar.update(100 - last_pct)
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

    -show_format rides along on this same call so apply_remux's progress bar
    has a duration to work with without spawning a second ffprobe per file."""
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


def plan_changes(streams, target_index):
    """Return dict stream_index -> desired bool default, and whether any change is needed."""
    desired = {s["index"]: (s["index"] == target_index) for s in streams}
    changed = any(desired[s["index"]] != s["default"] for s in streams)
    return desired, changed


def apply_mkv(path, streams, target_index, dry_run, backup, show_progress=False):
    """Clean single-pass remux via mkvmerge, setting --default-track-flag per
    track. Deliberately NOT mkvpropedit: see the module docstring -- editing
    in place can relocate the Tracks element to the end of the file in a way
    that breaks Windows Explorer's video thumbnail generation even though
    the file still plays fine.

    mkvmerge track IDs are 0-based across all tracks, which happens to match
    ffprobe's stream index for mkv containers, so each stream's ffprobe
    index doubles as its mkvmerge TID below.
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

    returncode, output = run_with_progress(args, path.name, show_progress, parse_pct)
    if returncode != 0 or not tmp_path.exists():
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
        shutil.copy2(path, path.with_name(path.name + ".bak"))

    os.replace(tmp_path, path)
    return True


def apply_remux(path, streams, target_index, dry_run, backup, reorder_for_avi,
                 duration=None, show_progress=False):
    """Remux with ffmpeg -c copy, setting disposition flags (and, for AVI,
    reordering so the target audio stream comes first).

    AVI reorder mode (reorder_for_avi): the target audio stream is remapped
    to be first and the rest follow; subtitle/data streams (-map 0:s? -map
    0:d?) are carried over too, since the explicit -map list built for the
    reorder would otherwise drop them. Disposition ordinals are then rebuilt
    to match the new output order, since the target is always a:0 there.

    For .mp4/.m4v/.mov, -movflags +faststart is added so the moov atom (the
    index players/thumbnailers read) stays at the front of the file instead
    of the end -- see the module docstring for why that matters.

    After a successful remux, a quick ffprobe sanity check confirms the
    output has the same stream count as the input before the original is
    replaced -- os.replace is atomic on the same filesystem, so the original
    is never left partially written.
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

    duration = duration if show_progress else None

    def parse_pct(line):
        line = line.strip()
        if duration and line.startswith("out_time_us="):
            try:
                out_time_us = int(line.split("=", 1)[1])
            except ValueError:
                return None
            return int(out_time_us / 1_000_000 / duration * 100)
        return None

    returncode, output = run_with_progress(cmd, path.name, show_progress, parse_pct)
    if returncode != 0 or not tmp_path.exists():
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
        bak_path = path.with_name(path.name + ".bak")
        shutil.copy2(path, bak_path)

    os.replace(tmp_path, path)
    return True


def process_file(path, args, need_mkv):
    """Probe one file, decide whether its default-audio flag needs fixing,
    apply the fix, and return "changed"/"unchanged"/"skipped"/"error" for
    the run's summary counts.

    AVI carries no default-flag disposition, so idempotency there is judged
    by stream order instead: is the target already the first audio stream?

    --force re-applies even when the disposition/order already looks
    correct, e.g. to backfill -movflags faststart onto mp4/mov files an
    older version of this script already touched (their disposition flag
    was already right, so a normal run would have skipped them and left the
    moov-at-the-end problem in place).
    """
    try:
        return _process_file(path, args)
    except Exception as exc:
        log.error(f"  {path.name}: unexpected error, skipping rest of file ({exc})")
        return "error"


def _process_file(path, args):
    """Does the actual work for process_file(); split out so process_file can
    wrap it in one try/except without duplicating the wrapper's logic."""
    streams, duration = probe_audio_streams(path)
    if streams is None:
        return "error"
    if not streams:
        log.info(f"  {path.name}: no audio streams found, skipping")
        return "skipped"

    target, note = choose_target(streams, args.prefer_lang)
    if target is None:
        log.info(f"  {path.name}: SKIP ({note})")
        return "skipped"

    ext = path.suffix.lower()
    is_avi_reorder = ext in AVI_EXTS and args.avi_reorder

    if is_avi_reorder:
        changed = streams[0]["index"] != target["index"]
    else:
        desired, changed = plan_changes(streams, target["index"])

    if args.force:
        changed = True

    if not changed:
        what = "is first audio stream" if is_avi_reorder else "is default"
        log.info(f"  {path.name}: already correct (stream#{target['index']} {what}), skipping")
        return "unchanged"

    log.info(f"  {path.name}: setting stream#{target['index']} "
             f"({target['language'] or 'und'}, {target['codec']}) as default audio")

    # Per-file % bars all render at the same tqdm position, so they only make
    # sense with one file in flight at a time -- disabled under --jobs > 1.
    show_progress = HAVE_TQDM and not args.no_progress and args.jobs == 1

    if ext in MKV_EXTS:
        ok = apply_mkv(path, streams, target["index"], args.dry_run, args.backup,
                        show_progress)
    elif ext in AVI_EXTS and not args.avi_reorder:
        log.info(f"    SKIP: AVI has no reliable default-track flag; re-run with "
                 f"--avi-reorder to reorder streams instead (or convert to mkv).")
        return "skipped"
    else:
        ok = apply_remux(path, streams, target["index"], args.dry_run,
                          args.backup, args.avi_reorder and ext in AVI_EXTS,
                          duration, show_progress)

    return "changed" if ok else "error"


def iter_files(paths, exts, recursive):
    """Yield files from paths (files passed through directly, directories
    walked) whose extension is in exts."""
    for p in paths:
        p = Path(p)
        if p.is_file():
            if p.suffix.lower() in exts:
                yield p
        elif p.is_dir():
            it = p.rglob("*") if recursive else p.glob("*")
            for f in it:
                if f.is_file() and f.suffix.lower() in exts:
                    yield f


def main():
    """Parse args, discover matching files, process them (sequentially or
    concurrently, depending on --jobs), and print a final summary.

    The "Found N file(s)" header is logged (so it lands in --log-file too)
    and then printed again directly when --log-file is set, since it should
    stay visible on the console even though the per-file details are being
    routed to the log file instead.

    --jobs 1 (default) uses the sequential path described below. --jobs N>1
    instead submits every file to a ThreadPoolExecutor up front (threads,
    not processes, since the work is waiting on ffmpeg/mkvmerge subprocesses
    rather than CPU-bound) and only the main thread -- iterating via
    as_completed() -- touches stats/the progress bar, so no locking is
    needed. There's no live per-file % bar in that path (N files in flight
    would fight over the same bar position), just one overall bar updated
    as each file finishes; log lines from different files' workers can
    interleave, though each line itself stays intact since logging is
    thread-safe.

    Progress-bar row stack (top to bottom) when a bar is active and --jobs 1:
      position=0  per-file bar (created/closed per file by run_with_progress)
      position=1  spacer: a real, blank tqdm row so the gap below the
                  per-file bar survives every redraw -- a plain print() here
                  gets swallowed, since tqdm doesn't know about it and
                  recalculates cursor offsets on its own.
      position=2  overall "Processing" bar, pinned to the bottom line.
    Both bars use leave=False: tqdm's leave=True close() path force-redraws
    via display(pos=0), ignoring the bar's actual position -- with multiple
    positioned bars that produces a garbled duplicate render. The summary
    printed afterward is what's meant to persist on screen, not the bar
    itself.

    Both bars are explicitly closed before the summary is printed, since a
    still-"active" bar keeps getting redrawn under every subsequent log
    line. A closed bar's last write also ends mid-line (a carriage return,
    not a newline), so one more bare print() forces a real newline first --
    otherwise the summary's own blank-line separator just terminates that
    dangling line instead of adding a new one.

    When --no-progress is combined with --log-file (no tqdm bar, but still
    writing details to a file), a plain "Processing i/N..." counter takes
    the bar's place on the console; the print() right after the loop clears
    that counter line.
    """
    ap = argparse.ArgumentParser(description=__doc__,
                                  formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("paths", nargs="+", help="Files and/or directories to process")
    ap.add_argument("--ext", default=None,
                     help="Comma-separated extensions to include (default: mkv,webm,mp4,m4v,mov,avi)")
    ap.add_argument("--no-recursive", action="store_true", help="Don't recurse into subdirectories")
    ap.add_argument("--dry-run", action="store_true", help="Show what would change, don't touch files")
    ap.add_argument("--backup", action="store_true",
                     help="Keep the pre-change original as <name>.bak for any remux")
    ap.add_argument("--prefer-lang", default=None,
                     help="If multiple 2-channel tracks exist, prefer this language code (e.g. eng)")
    ap.add_argument("--avi-reorder", action="store_true",
                     help="For .avi files, remux to put the target audio stream first "
                          "(AVI has no real 'default' flag)")
    ap.add_argument("--force", action="store_true",
                     help="Re-apply even to files that already look correct. Use this to "
                          "backfill -movflags faststart onto mp4/mov files that an older "
                          "version of this script already touched (their disposition flags "
                          "were already right, so a normal run would skip them).")
    ap.add_argument("--log-file", default=None, metavar="PATH",
                     help="Write detailed log output to this file instead of the console. "
                          "The console still shows a progress bar and the final summary.")
    ap.add_argument("--no-progress", action="store_true",
                     help="Disable the progress bar (e.g. for non-interactive/CI logs)")
    ap.add_argument("--jobs", type=int, default=1, metavar="N",
                     help="Number of files to remux concurrently (default: 1 = sequential). "
                          "This work is I/O-bound, not CPU-bound, so pick a value based on "
                          "what your storage can sustain rather than core count. The live "
                          "per-file %% bar is disabled when N > 1; log lines from different "
                          "files may interleave.")
    args = ap.parse_args()

    if args.jobs < 1:
        ap.error("--jobs must be >= 1")

    setup_logging(args.log_file)

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

    if args.jobs == 1:
        spacer = tqdm(total=1, position=1, bar_format="{desc}", desc="", leave=False) if use_bar else None
        iterator = tqdm(files, unit="file", desc="Processing", position=2, leave=False) if use_bar else files

        for i, f in enumerate(iterator, 1):
            if show_fallback_counter:
                print(f"\rProcessing {i}/{len(files)}...", end="", flush=True)
            log.info(f"\n[{i}/{len(files)}] {f}")
            result = process_file(f, args, need_mkv)
            stats[result] = stats.get(result, 0) + 1

        if use_bar:
            iterator.close()
            spacer.close()
            print()
        if show_fallback_counter:
            print()
    else:
        # Concurrent path: no per-file bar (they'd all fight over the same tqdm
        # position), just one overall bar/counter updated as each file finishes.
        # Files are submitted in list order but may complete out of order.
        def run_one(i, f):
            log.info(f"\n[{i}/{len(files)}] {f}")
            return process_file(f, args, need_mkv)

        overall = tqdm(total=len(files), unit="file", desc="Processing", leave=False) if use_bar else None
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
                    overall.update(1)

        if overall:
            overall.close()
            print()
        if show_fallback_counter:
            print()

    summary_lines = ["", "----- Summary -----"]
    for k in ("changed", "unchanged", "skipped", "error"):
        summary_lines.append(f"{k}: {stats[k]}")
    for line in summary_lines:
        log.info(line)
    if args.log_file:
        for line in summary_lines:
            print(line)

    if stats["error"]:
        sys.exit(1)


if __name__ == "__main__":
    main()