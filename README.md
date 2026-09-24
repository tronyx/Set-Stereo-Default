# set_stereo_default

[![Tests](https://github.com/tronyx/Set-Stereo-Default/actions/workflows/tests.yml/badge.svg?branch=master)](https://github.com/tronyx/Set-Stereo-Default/actions/workflows/tests.yml)
[![Python 3.8+](https://img.shields.io/badge/python-3.8%2B-blue)](#requirements)
[![License: MIT](https://img.shields.io/github/license/tronyx/Set-Stereo-Default)](LICENSE.md)

> **Beta.** This has been tested pretty heavily on my own library, but it's still early — use it with caution. Try it on a handful of files (or with `--dry-run`) before pointing it at your whole collection, and keep backups (`--backup`) until you've seen it behave the way you expect.

A small command-line tool that bulk-fixes a common annoyance in ripped or downloaded video files: the audio track flagged as "default" isn't the 2-channel stereo track, so playback starts on a 5.1/7.1 track that sounds wrong on a TV soundbar or laptop speakers.

`set_stereo_default.py` scans a folder of video files, figures out which audio stream is stereo, and flips the default flag onto it — clearing it from every other audio track in the process.

## Features

- **Safe by default.** Files already in the correct state are skipped. `--dry-run` shows exactly what would change without touching anything. Every remux goes to a temp file first and is only swapped in after a check confirms no streams were lost and the right audio track is now the default; if that check fails, the original is left untouched and the file is counted as an error. Ctrl+C stops cleanly, too — in-flight remuxes are killed and their partial temp files removed, files that haven't started are skipped, and already-finished files are unaffected. The partial summary counts everything that didn't finish as `cancelled`. (Exception: hardlinks & cross-seeding — see [Limitations](#limitations).)
- **Format-aware.** Uses `mkvmerge` for `.mkv`/`.webm` and `ffmpeg` for everything else, each with format-specific fixes (see [How it works](#how-it-works)) so tools like Windows Explorer don't lose video thumbnails on the files it touches.
- **Live progress.** A live per-file `%` bar plus an overall batch bar (via `tqdm`, if installed) so you can see how a large batch is going. The per-file bar only shows up on sequential (`--jobs 1`) runs — see [Options](#options).
- **Flexible logging.** Send detailed output to a log file with `--log-file` while the console stays clean.
- **Concurrent processing.** `--jobs N` remuxes several files at once (default: `1`, one at a time) — useful since this work is mostly waiting on disk I/O, not CPU.

## Requirements

- Python 3.8+
- [ffmpeg / ffprobe](https://ffmpeg.org)
- [mkvmerge](https://mkvtoolnix.download) (part of MKVToolNix) — only needed if you have `.mkv`/`.webm` files
- `tqdm` — optional, enables the progress bar (see [requirements.txt](requirements.txt))
- Free disk space: every remux writes a full temp copy of the file next to the original before swapping it in, so you need free space roughly equal to your largest file. `--backup` normally costs no extra space, since the `.bak` is a hard link to the original; on filesystems without hard-link support it falls back to a full copy, which doubles that

## Installation

```bash
pip install -r requirements.txt
```

Then make sure `ffmpeg`, `ffprobe`, and (if you have MKV/WebM files) `mkvmerge` are installed and on your `PATH`.

## Usage

```bash
python3 set_stereo_default.py /path/to/videos
```

That's the basic case: scan a folder recursively and fix any file that needs it. A few other examples:

```bash
# Preview changes without touching any files
python3 set_stereo_default.py /path/to/videos --dry-run

# Process specific files instead of a folder
python3 set_stereo_default.py file1.mkv file2.mp4

# Prefer English when a file has more than one 2-channel track
python3 set_stereo_default.py /path/to/videos --prefer-lang eng

# Log details to a file, keep the console output to just the progress bar
python3 set_stereo_default.py /path/to/videos --log-file run.log

# Remux up to 4 files at once instead of one at a time
python3 set_stereo_default.py /path/to/videos --jobs 4
```

### Options

| Flag | Description |
| --- | --- |
| `--ext EXT1,EXT2` | Comma-separated extensions to include (default: `mkv,webm,mp4,m4v,mov,avi`) |
| `--no-recursive` | Don't recurse into subdirectories |
| `--dry-run` | Show what would change without touching any files |
| `--backup` | Keep the pre-change original as `<name>.bak` (a hard link where supported, so no extra disk space) |
| `--prefer-lang LANG` | If multiple 2-channel tracks exist, prefer this language code (e.g. `eng`) |
| `--avi-reorder` | For `.avi` files (which have no real "default" flag), reorder streams instead so the target track comes first |
| `--force` | Re-apply even to files that already look correct |
| `--log-file PATH` | Write detailed output to a file instead of the console |
| `--no-progress` | Disable the progress bar |
| `--jobs N` | Remux up to `N` files concurrently (default: `1`, sequential) |

`--ext` replaces the default extension list rather than adding to it — pass every extension you want included.

`--jobs` is I/O-bound work, not CPU-bound, so pick a value based on what your storage can sustain rather than core count. Above `1`, there's no per-file `%` bar — just the overall batch bar, which tracks the combined progress of every in-flight file — and log lines from different files may interleave, since several files are being remuxed at the same time.

Run `python3 set_stereo_default.py --help` for the full list with details.

## Sample output

### One Job At A Time

```text
$ python3 set_stereo_default.py /path/to/videos/TV\ Shows/Awesome Show (2026)/
Found 32 file(s).

[1/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 01/Awesome Show (2026) - S01E01 - Episode 1.mkv
  Awesome Show (2026) - S01E01 - Episode 1.mkv: setting stream#1 (eng, aac) as default audio

[2/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 01/Awesome Show (2026) - S01E02 - Episode 2.mkv
  Awesome Show (2026) - S01E02 - Episode 2.mkv: setting stream#1 (eng, aac) as default audio

...

[12/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 02/Awesome Show (2026) - S02E04 - Episode 12.mkv
  Awesome Show (2026) - S02E04 - Episode 12.mkv: setting stream#1 (eng, aac) as default audio
  Awesome Show (2026) - S02E04 - A Night at t:  45%|████████████▌               | 45/100 [00:09<00:11,  4.87%/s]

Processing:  36%|██████████████▊                                   | 11.45/32 [06:05<10:48, 31.56s/file]

...

[25/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E01 - Episode 25.mkv
  Awesome Show (2026) - S04E01 - Episode 25.mkv: already correct (stream#1 is default), skipping

[26/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E02 - Episode 26.mkv
  Awesome Show (2026) - S04E02 - Episode 26.mkv: already correct (stream#1 is default), skipping
```

Files that already have the right track marked default are left untouched — no remux, no per-file progress bar, just a one-line note before the script moves on. This example is a sequential (`--jobs 1`) run: the top bar tracks the file currently remuxing, and the bottom one tracks the whole batch and stays pinned to the last line. The batch bar moves as the current file progresses rather than waiting for it to finish, which is why its count is fractional (11.45 files done out of 32). Under `--jobs N > 1` there's no per-file bar, just the batch one (see [Options](#options)). Once every file's been processed, you'll get a summary like:

```text
----- Summary -----
changed: 30
unchanged: 2
skipped: 0
error: 0
```

### Multiple Jobs At A Time

```text
$ python3 scripts/fix_default_audio_track.py /path/to/videos/TV\ Shows/Awesome Show (2026)/Season 06/ --jobs 5
Found 5 file(s).

[5/5] /path/to/videos/TV\ Shows/Awesome Show (2026)/Season 06/Awesome Show (2026) - S06E05 - Episode 45.mkv                                                                                                                                            
  Awesome Show (2026) - S06E05 - Episode 52.mkv: setting stream#1 (eng, aac) as default audio

[3/5] /path/to/videos/TV\ Shows/Awesome Show (2026)/Season 06/Awesome Show (2026) - S06E03 - Episode 43.mkv                                                                                                                                
  Awesome Show (2026) - S06E03 - Episode 43.mkv: setting stream#1 (eng, aac) as default audio

[2/5] /path/to/videos/TV\ Shows/Awesome Show (2026)/Season 06/Awesome Show (2026) - S06E02 - Episode 42.mkv                                                                                                                               
  Awesome Show (2026) - S06E02 - Episode 42.mkv: setting stream#1 (eng, aac) as default audio

[1/5] /path/to/videos/TV\ Shows/Awesome Show (2026)/Season 06/Awesome Show (2026) - S06E01 - Episode 41.mkv                                                                                                                                   
  Awesome Show (2026) - S06E01 - Episode 41.mkv: setting stream#1 (eng, aac) as default audio

[4/5] /path/to/videos/TV\ Shows/Awesome Show (2026)/Season 06/Awesome Show (2026) - S06E04 - Episode 44.mkv                                                                                                                                
  Awesome Show (2026) - S06E04 - Episode 44.mkv: setting stream#1 (eng, aac) as default audio

Processing:  27%|██████████████████████████████████████████████████████▊                                                                                                                                                    | 1.35/5 [00:04<00:12,  3.56s/file]
```

### Dry run

`--dry-run` prints exactly what it would do without touching any files, including the `mkvmerge`/`ffmpeg` command it would run, quoted so you can paste it into a shell:

```text
$ python3 set_stereo_default.py "/path/to/videos/TV Shows/Awesome Show (2026)/Season 04/" --dry-run
Found 8 file(s) (dry run).

[1/8] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E01 - Episode 25.mkv
  Awesome Show (2026) - S04E01 - Episode 25.mkv: setting stream#1 (eng, aac) as default audio
    [dry-run] mkvmerge --gui-mode -o '.../S04E01 - Episode 25.mkv.tmp_remux.mkv' --default-track-flag 1:yes --default-track-flag 4:no '.../S04E01 - Episode 25.mkv'

[2/8] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E02 - Episode 26.mkv
  Awesome Show (2026) - S04E02 - Episode 26.mkv: setting stream#1 (eng, aac) as default audio
    [dry-run] mkvmerge --gui-mode -o '.../S04E02 - Episode 26.mkv.tmp_remux.mkv' --default-track-flag 1:yes --default-track-flag 3:no '.../S04E02 - Episode 26.mkv'

...

[8/8] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E08 - Episode 32.mkv
  Awesome Show (2026) - S04E08 - Episode 32.mkv: setting stream#1 (eng, aac) as default audio
    [dry-run] mkvmerge --gui-mode -o '.../S04E08 - Episode 32.mkv.tmp_remux.mkv' --default-track-flag 1:yes --default-track-flag 4:no '.../S04E08 - Episode 32.mkv'

----- Summary -----
changed: 8
unchanged: 0
skipped: 0
error: 0
```

## How it works

For each file, the script inspects every audio stream's channel count. The one stream with exactly 2 channels becomes "default"; every other audio stream gets its default flag cleared. If a file has zero or multiple 2-channel tracks, it's skipped and logged (use `--prefer-lang` to break ties).

A file is skipped (and counted under `skipped:` in the summary) when:

- It has no audio streams at all.
- It has zero or multiple 2-channel tracks and `--prefer-lang` doesn't resolve the ambiguity.
- It's an `.avi` file and `--avi-reorder` wasn't passed (AVI has no real "default" flag to set).

If a run is killed outright (`kill -9`, a reboot, a container stopping) rather than stopped with Ctrl+C, a `<name>.tmp_remux.<ext>` file can be left next to the original. The next run skips these with a warning instead of treating them as videos. They're safe to delete, since the original is only replaced after a remux fully succeeds.

How the change actually gets applied depends on the container:

- **`.mkv` / `.webm`** — a clean single-pass remux via `mkvmerge`, rather than editing the file header in place. In-place edits can push the file's track metadata to the very end of the file, which is exactly the shape of file that breaks Windows Explorer's thumbnail generation even though the video plays fine everywhere else.
- **`.mp4` / `.m4v` / `.mov`** — remuxed with `ffmpeg -c copy` (no re-encoding) with `-movflags +faststart`, so the index ffmpeg would otherwise leave at the end of the file stays at the front where thumbnailers expect it.
- **`.avi`** — AVI has no standard "default track" flag that players honor. With `--avi-reorder`, the script instead reorders streams so the target audio track comes first. Without that flag, AVI files are skipped with a warning.

Because a remux produces a brand-new file, the script copies the original's permissions and owner onto it before swapping it in, so tools that share your media through a group (Sonarr, Radarr, Plex, other containers) keep access to it. Changing a file's owner requires root. If the script can't do it, the new file belongs to whoever ran the script, and you'll see a warning once per run; the permissions are still copied. A common cause is a network share (NFS, for example) that maps root to `nobody`. In that case, run the script as the user that owns your media, or fix the owner afterwards with `chown`.

## Limitations

**Hardlinks will be broken.** Every remux (mkv, mp4, avi) works by building a brand-new file at a temp path and then replacing the original with it — that's what makes the dry-run/temp-file/sanity-check safety guarantees above possible, but it also means the original inode goes away. If another path on your filesystem is hardlinked to that same file — a common setup with Sonarr/Radarr/qBittorrent, which hardlink between a download folder and a library folder to avoid duplicating disk space — that other path will keep pointing at the old, unmodified file instead of the fixed one, and the two paths will no longer share disk space. If your setup relies on hardlinks, run this script *before* hardlinking rather than after, or re-hardlink the affected files afterward.

**Cross-seeding will break too, for a different reason.** A remux produces a new file with different bytes, even though it's a lossless stream copy — the container is rebuilt, not just patched. That means the file no longer matches the piece hashes your torrent client (and any tracker) expects, so any torrent seeding that file — including cross-seeded torrents sharing it via hardlink — will fail its hash check and get flagged as missing/corrupt data. Don't run this on files you're actively seeding or cross-seeding unless you're prepared to re-download or re-hash them, and check with your private trackers' rules before doing so, since a failed hash check can look like a hit-and-run.

## A note on how this was built

This script was largely written with [Claude](https://claude.ai), Anthropic's AI coding assistant — I described what I needed, directed the design, and asked for changes across many iterations rather than writing most of the code by hand myself. Every feature went through real testing before landing here, including dry-run checks and stubbed test runs simulating `ffmpeg`/`mkvmerge` output, and the safety measures baked into the script (dry-run mode, temp-file-first remuxing, post-remux sanity checks) are exactly the kind of thing I insisted on because this touches a media library I actually care about.

I'd rather say that plainly than let it pass as fully hand-written. There's a lot of AI-generated code floating around that hasn't been reviewed or tested and ends up breaking people's setups, and I don't want this to be mistaken for that. If something looks off, please open an issue.

## Exit status

The script exits `1` if no matching files are found, a required tool is missing, or one or more files ended up in the `error:` bucket of the summary; `130` if you interrupt it with Ctrl+C; and `0` otherwise. If you're scripting this (cron, CI, etc.), the exit code alone tells you whether anything went wrong, but check the printed summary for the `changed`/`unchanged`/`skipped`/`error` breakdown.

## Running the tests

The tests cover the script's own logic: choosing the track, finding files, backups, checking a remux before it replaces the original, progress reporting, and Ctrl+C cleanup. Anything that would call ffmpeg, ffprobe or mkvmerge is replaced with a stand-in, so none of those need to be installed:

```bash
pip install -r requirements-dev.txt
python -m pytest
```

GitHub also runs them automatically on every push and pull request, on the oldest and newest supported Python versions (see [.github/workflows/tests.yml](.github/workflows/tests.yml)).

Because the real tools are never run, the tests can't tell you whether ffmpeg or mkvmerge will accept a changed command. If you change how the script calls them, also try it on a few real files, starting with `--dry-run`.

## License

MIT — see [LICENSE.md](LICENSE.md).
