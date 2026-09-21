# set_stereo_default

> **Beta.** This has been tested pretty heavily on my own library, but it's still early — use it with caution. Try it on a handful of files (or with `--dry-run`) before pointing it at your whole collection, and keep backups (`--backup`) until you've seen it behave the way you expect.

A small command-line tool that bulk-fixes a common annoyance in ripped or downloaded video files: the audio track flagged as "default" isn't the 2-channel stereo track, so playback starts on a 5.1/7.1 track that sounds wrong on a TV soundbar or laptop speakers.

`set_stereo_default.py` scans a folder of video files, figures out which audio stream is stereo, and flips the default flag onto it — clearing it from every other audio track in the process.

## Features

- **Safe by default.** Files already in the correct state are skipped. `--dry-run` shows exactly what would change without touching anything. Every remux goes to a temp file first and is only swapped in after a sanity check passes. (Exception: hardlinks & cross-seeding — see [Limitations](#limitations).)
- **Format-aware.** Uses `mkvmerge` for `.mkv`/`.webm` and `ffmpeg` for everything else, each with format-specific fixes (see [How it works](#how-it-works)) so tools like Windows Explorer don't lose video thumbnails on the files it touches.
- **Live progress.** A per-file and overall progress bar (via `tqdm`, if installed) so you can see how a large batch is going.
- **Flexible logging.** Send detailed output to a log file with `--log-file` while the console stays clean.

## Requirements

- Python 3.8+
- [ffmpeg / ffprobe](https://ffmpeg.org)
- [mkvmerge](https://mkvtoolnix.download) (part of MKVToolNix) — only needed if you have `.mkv`/`.webm` files
- `tqdm` — optional, enables the progress bar (see [requirements.txt](requirements.txt))

## Installation

```bash
pip install -r requirements.txt
```

Then make sure `ffmpeg`, `ffprobe`, and (if you have MKV/WebM files) `mkvmerge` are on your `PATH`.

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
```

### Options

| Flag | Description |
| --- | --- |
| `--ext EXT1,EXT2` | Comma-separated extensions to include (default: `mkv,webm,mp4,m4v,mov,avi`) |
| `--no-recursive` | Don't recurse into subdirectories |
| `--dry-run` | Show what would change without touching any files |
| `--backup` | Keep the pre-change original as `<name>.bak` |
| `--prefer-lang LANG` | If multiple 2-channel tracks exist, prefer this language code (e.g. `eng`) |
| `--avi-reorder` | For `.avi` files (which have no real "default" flag), reorder streams instead so the target track comes first |
| `--force` | Re-apply even to files that already look correct |
| `--log-file PATH` | Write detailed output to a file instead of the console |
| `--no-progress` | Disable the progress bar |

Run `python3 set_stereo_default.py --help` for the full list with details.

## Sample output

```text
$ python3 set_stereo_default.py /path/to/videos/TV\ Shows/Awesome Show/
Found 32 file(s).

[1/32] /path/to/videos/TV Shows/Awesome Show/Season 01/Awesome Show (2026) - S01E01 - Episode 1.mkv
  Awesome Show (2026) - S01E01 - Episode 1.mkv: setting stream#1 (eng, aac) as default audio

[2/32] /path/to/videos/TV Shows/Awesome Show/Season 01/Awesome Show (2026) - S01E02 - Episode 2.mkv
  Awesome Show (2026) - S01E02 - Episode 2.mkv: setting stream#1 (eng, aac) as default audio

...

[12/32] /path/to/videos/TV Shows/Awesome Show/Season 02/Awesome Show (2026) - S02E04 - Episode 12.mkv
  Awesome Show (2026) - S02E04 - Episode 12.mkv: setting stream#1 (eng, aac) as default audio
  Awesome Show (2026) - S02E04 - A Night at t: 100%|████████████████████████████| 100/100 [00:21<00:00,  7.07%/s]

Processing:  34%|██████████████                                    | 11/32 [06:05<09:51, 28.14s/file]

...

[25/32] /path/to/videos/TV Shows/Awesome Show/Season 04/Awesome Show (2026) - S04E01 - Episode 25.mkv
  Awesome Show (2026) - S04E01 - Episode 25.mkv: already correct (stream#1 is default), skipping

[26/32] /path/to/videos/TV Shows/Awesome Show/Season 04/Awesome Show (2026) - S04E02 - Episode 26.mkv
  Awesome Show (2026) - S04E02 - Episode 26.mkv: already correct (stream#1 is default), skipping
```

Files that already have the right track marked default are left untouched — no remux, no per-file progress bar, just a one-line note before the script moves on. The top bar tracks the file currently remuxing; the bottom one tracks the whole batch and stays pinned to the last line. Once every file's been processed, you'll get a summary like:

```text
----- Summary -----
changed: 30
unchanged: 2
skipped: 0
error: 0
```

### Dry run

`--dry-run` prints exactly what it would do — including the literal `mkvmerge`/`ffmpeg` command it would run — without touching any files:

```text
$ python3 set_stereo_default.py "/path/to/videos/TV Shows/Awesome Show/Season 04/" --dry-run
Found 8 file(s) (dry run).

[1/8] /path/to/videos/TV Shows/Awesome Show/Season 04/Awesome Show (2026) - S04E01 - Episode 25.mkv
  Awesome Show (2026) - S04E01 - Episode 25.mkv: setting stream#1 (eng, aac) as default audio
    [dry-run] mkvmerge --gui-mode -o .../S04E01 - Episode 25.mkv.tmp_remux.mkv --default-track-flag 1:yes --default-track-flag 4:no .../S04E01 - Episode 25.mkv

[2/8] /path/to/videos/TV Shows/Awesome Show/Season 04/Awesome Show (2026) - S04E02 - Episode 26.mkv
  Awesome Show (2026) - S04E02 - Episode 26.mkv: setting stream#1 (eng, aac) as default audio
    [dry-run] mkvmerge --gui-mode -o .../S04E02 - Episode 26.mkv.tmp_remux.mkv --default-track-flag 1:yes --default-track-flag 3:no .../S04E02 - Episode 26.mkv

...

[8/8] /path/to/videos/TV Shows/Awesome Show/Season 04/Awesome Show (2026) - S04E08 - Episode 32.mkv
  Awesome Show (2026) - S04E08 - Episode 32.mkv: setting stream#1 (eng, aac) as default audio
    [dry-run] mkvmerge --gui-mode -o .../S04E08 - Episode 32.mkv.tmp_remux.mkv --default-track-flag 1:yes --default-track-flag 4:no .../S04E08 - Episode 32.mkv

----- Summary -----
changed: 8
unchanged: 0
skipped: 0
error: 0
```

## How it works

For each file, the script inspects every audio stream's channel count. The one stream with exactly 2 channels becomes "default"; every other audio stream gets its default flag cleared. If a file has zero or multiple 2-channel tracks, it's skipped and logged (use `--prefer-lang` to break ties).

How the change actually gets applied depends on the container:

- **`.mkv` / `.webm`** — a clean single-pass remux via `mkvmerge`, rather than editing the file header in place. In-place edits can push the file's track metadata to the very end of the file, which is exactly the shape of file that breaks Windows Explorer's thumbnail generation even though the video plays fine everywhere else.
- **`.mp4` / `.m4v` / `.mov`** — remuxed with `ffmpeg -c copy` (no re-encoding) with `-movflags +faststart`, so the index ffmpeg would otherwise leave at the end of the file stays at the front where thumbnailers expect it.
- **`.avi`** — AVI has no standard "default track" flag that players honor. With `--avi-reorder`, the script instead reorders streams so the target audio track comes first. Without that flag, AVI files are skipped with a warning.

## Limitations

**Hardlinks will be broken.** Every remux (mkv, mp4, avi) works by building a brand-new file at a temp path and then replacing the original with it — that's what makes the dry-run/temp-file/sanity-check safety guarantees above possible, but it also means the original inode goes away. If another path on your filesystem is hardlinked to that same file — a common setup with Sonarr/Radarr/qBittorrent, which hardlink between a download folder and a library folder to avoid duplicating disk space — that other path will keep pointing at the old, unmodified file instead of the fixed one, and the two paths will no longer share disk space. If your setup relies on hardlinks, run this script *before* hardlinking rather than after, or re-hardlink the affected files afterward.

**Cross-seeding will break too, for a different reason.** A remux produces a new file with different bytes, even though it's a lossless stream copy — the container is rebuilt, not just patched. That means the file no longer matches the piece hashes your torrent client (and any tracker) expects, so any torrent seeding that file — including cross-seeded torrents sharing it via hardlink — will fail its hash check and get flagged as missing/corrupt data. Don't run this on files you're actively seeding or cross-seeding unless you're prepared to re-download or re-hash them, and check with your private trackers' rules before doing so, since a failed hash check can look like a hit-and-run.

## A note on how this was built

This script was largely written with [Claude](https://claude.ai), Anthropic's AI coding assistant — I described what I needed, directed the design, and asked for changes across many iterations rather than writing most of the code by hand myself. Every feature went through real testing before landing here, including dry-run checks and stubbed test runs simulating `ffmpeg`/`mkvmerge` output, and the safety measures baked into the script (dry-run mode, temp-file-first remuxing, post-remux sanity checks) are exactly the kind of thing I insisted on because this touches a media library I actually care about.

I'd rather say that plainly than let it pass as fully hand-written. There's a lot of AI-generated code floating around that hasn't been reviewed or tested and ends up breaking people's setups, and I don't want this to be mistaken for that. If something looks off, please open an issue.

## License

MIT — see [LICENSE.md](LICENSE.md).
