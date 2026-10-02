# 🔊 set_stereo_default

[![tests: master](https://img.shields.io/github/actions/workflow/status/tronyx/Set-Stereo-Default/tests.yml?branch=master&label=tests%3A%20master)](https://github.com/tronyx/Set-Stereo-Default/actions/workflows/tests.yml?query=branch%3Amaster)
[![tests: develop](https://img.shields.io/github/actions/workflow/status/tronyx/Set-Stereo-Default/tests.yml?branch=develop&label=tests%3A%20develop)](https://github.com/tronyx/Set-Stereo-Default/actions/workflows/tests.yml?query=branch%3Adevelop)
[![coverage: master](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ftronyx%2FSet-Stereo-Default%2Fbadges%2Fcoverage-master.json)](#-running-the-tests)
[![coverage: develop](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ftronyx%2FSet-Stereo-Default%2Fbadges%2Fcoverage-develop.json)](#-running-the-tests)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](#-requirements)
[![License: MIT](https://img.shields.io/github/license/tronyx/Set-Stereo-Default)](LICENSE.md)

**Make the stereo track play by default, across your whole video library.**

Plenty of ripped and downloaded videos flag a 5.1 or 7.1 surround track as the default audio. On a TV, soundbar or laptop, that often means quiet dialogue and booming effects. `set_stereo_default.py` finds the 2-channel stereo track in each file and makes it the default instead. Nothing is re-encoded, so picture and sound quality stay exactly the same.

Here's a typical movie before and after:

| Audio track | Before | After |
| --- | :---: | :---: |
| English, 5.1 surround | ✅ default | |
| English, stereo | | ✅ default |
| Director's commentary, stereo | | |

The commentary is stereo too, but it's never picked (see [Picking the track](#-picking-the-track)).

> [!WARNING]
> **This is beta software.** It's been tested heavily on my own library, but it's still early. Try it on a few files first, start with `--dry-run`, and use `--backup` until you're happy with how it behaves.

## ✨ Features

- 🛡️ **Safe by default.** Files that are already right are left alone, `--dry-run` previews every change, and each new file is checked before it replaces the original.
- 🎯 **Picks the right track.** Commentary and audio-description tracks are never chosen, and a stereo dub in another language never replaces the original language.
- 🧩 **Format-aware.** MKV, WebM, MP4, M4V, MOV and AVI, each handled in a way that keeps Windows Explorer thumbnails working.
- 📊 **Live progress.** A bar for the current file and one for the whole batch.
- ⚡ **Parallel.** `--jobs N` works on several files at once.
- 📝 **Quiet logging.** `--log-file` keeps the details in a file and the console tidy.
- 🛑 **Stops cleanly.** Ctrl+C or `docker stop` halts mid-run without leaving half-written files.

## 📋 Requirements

| What | Version | Needed for |
| --- | --- | --- |
| [Python](https://www.python.org) | 3.10 or newer | Everything |
| [ffmpeg and ffprobe](https://ffmpeg.org) | 4.4 or newer | Everything |
| [mkvmerge](https://mkvtoolnix.download) (part of MKVToolNix) | Any | `.mkv` and `.webm` files |
| [tqdm](https://github.com/tqdm/tqdm) | 4.60 or newer | Progress bars (optional) |

The script is tested against ffmpeg 4.4, 5.1, 6.1, 7.1 and the newest release, and mkvmerge 45 through 102.

> [!NOTE]
> ffmpeg versions older than 4.4 can't read the commentary and audio-description flags in MKV files, so they might make a commentary track the default.

### 💾 Disk space

Each file is rewritten to a temporary copy next to the original before it's swapped in, so you need free space about the size of your largest file while the script runs.

`--backup` doesn't add to that during the run, because each backup starts as a hard link to the original rather than a copy. But once the new file replaces the original, the backup holds the original's data on its own, so **every backup takes as much space as the file it backs up** until you delete it. Running `--backup` over a whole library needs about as much free space as the files that get changed.

## 📦 Installation

1. Get the script, and optionally tqdm for the progress bars:

   ```bash
   git clone https://github.com/tronyx/Set-Stereo-Default.git
   cd Set-Stereo-Default
   pip install -r requirements.txt
   ```

2. Install ffmpeg and MKVToolNix:

   | System | Command |
   | --- | --- |
   | Debian / Ubuntu | `sudo apt install ffmpeg mkvtoolnix` |
   | macOS ([Homebrew](https://brew.sh)) | `brew install ffmpeg mkvtoolnix` |
   | Windows | `winget install Gyan.FFmpeg` and `winget install MoritzBunkus.MKVToolNix` |

3. Check that everything is found:

   ```bash
   ffmpeg -version
   mkvmerge --version
   ```

> [!TIP]
> On Windows, if `mkvmerge` isn't found after installing, add the MKVToolNix folder (usually `C:\Program Files\MKVToolNix`) to your `PATH` and open a new terminal.

## 🚀 Quick start

```bash
# 1. See what would change, without touching anything
python3 set_stereo_default.py "/path/to/videos" --dry-run

# 2. Try it for real on one folder, keeping backups
python3 set_stereo_default.py "/path/to/videos/Some Show" --backup

# 3. Happy? Run it on everything
python3 set_stereo_default.py "/path/to/videos"
```

Folders are searched recursively. You can also pass individual files, or a mix of files and folders. A path that doesn't exist is reported (`Skipping /path/to/vidoes: no such file or directory`) and the rest still run.

On Windows, type `py` instead of `python3`.

## ⚙️ Options

| Option | What it does |
| --- | --- |
| `--dry-run` | Show what would change without touching any files |
| `--backup` | Keep each original as `<name>.bak`. Each backup takes as much space as the original (see [Disk space](#-disk-space)) |
| `--existing-backups MODE` | With `--backup`, what to do when `<name>.bak` already exists: `replace` it, or `number` the new one (`.bak.1`, `.bak.2`, ...). Without this you're asked once (see [Backups](#-backups)) |
| `--prefer-lang LANG` | Language the stereo track must be in, as a 2- or 3-letter code (`en`, `eng`, `de`, `ger` and `deu` all work). Also picks between several stereo tracks. Default: the language of the track that plays by default now |
| `--jobs N` | Work on up to `N` files at once (default: `1`) |
| `--log-file PATH` | Write the details to a file. Warnings, errors, the progress bar and the summary still show on the console |
| `--ext EXT1,EXT2` | Extensions to process (default: `mkv,webm,mp4,m4v,mov,avi`). This replaces the default list, so list every extension you want |
| `--no-recursive` | Don't look inside subfolders |
| `--skip-symlinks` | Skip symlinked files. By default, the file a link points to is fixed and the link keeps working |
| `--follow-symlinks` | Also look inside symlinked subfolders. By default they're skipped (folders you name on the command line are always searched) |
| `--avi-reorder` | For `.avi` files, move the stereo track to the front (see [AVI files](#-changing-the-file)) |
| `--force` | Remux even files that are already correct, e.g. to restore thumbnails (see [Troubleshooting](#-troubleshooting)) |
| `--keep-dates` | Give each changed file the original's modification date, so it doesn't look newly changed (see [File dates](#-file-dates)) |
| `--no-progress` | Hide the progress bars, e.g. for cron or CI logs |

> [!TIP]
> **Choosing `--jobs`:** the work is limited by disk speed, not CPU, so pick a number your storage can keep up with rather than your core count. Above `1`, you'll see only the overall progress bar. Lines from different files print as they happen, so when a file's line follows another file's, its `[i/N]` header is printed again first to show which file it belongs to.

Run `python3 set_stereo_default.py --help` for the full built-in help.

## 👀 Sample output

<details>
<summary>One file at a time (the default)</summary>

```text
$ python3 set_stereo_default.py "/path/to/videos/TV Shows/Awesome Show (2026)"
Found 32 file(s).

[1/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 01/Awesome Show (2026) - S01E01 - Episode 1.mkv
  Awesome Show (2026) - S01E01 - Episode 1.mkv: setting stream#1 (eng, aac) as default audio

...

[12/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 02/Awesome Show (2026) - S02E04 - Episode 12.mkv
  Awesome Show (2026) - S02E04 - Episode 12.mkv: setting stream#1 (eng, aac) as default audio
  Awesome Show (2026) - S02E04 - Episode:  45%|████████████▌               | 45/100 [00:09<00:11,  4.87%/s]

Processing:  36%|██████████████▊                                   | 11.45/32 [06:05<10:48, 31.56s/file]

...

[25/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E01 - Episode 25.mkv
  Awesome Show (2026) - S04E01 - Episode 25.mkv: already correct (stream#1 is default), skipping

[26/32] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E02 - Episode 26.mkv
  Awesome Show (2026) - S04E02 - Episode 26.mkv: SKIP (no 2-channel track in 'eng' [found stream#2 (spa/aac)] -- use --prefer-lang to pick another language)

...

----- Summary -----
Changed: 30
Unchanged: 1
Skipped: 1
Error: 0
```

The top bar follows the file being remuxed, and the bottom one follows the whole batch. The batch count moves during each file rather than jumping when it finishes, which is why it shows fractions like `11.45/32`.

</details>

<details>
<summary>Several files at once (--jobs 5)</summary>

```text
$ python3 set_stereo_default.py "/path/to/videos/TV Shows/Awesome Show (2026)/Season 06" --jobs 5
Found 5 file(s).

[2/5] /path/to/videos/TV Shows/Awesome Show (2026)/Season 06/Awesome Show (2026) - S06E02 - Episode 42.mkv
  Awesome Show (2026) - S06E02 - Episode 42.mkv: setting stream#1 (eng, aac) as default audio

[1/5] /path/to/videos/TV Shows/Awesome Show (2026)/Season 06/Awesome Show (2026) - S06E01 - Episode 41.mkv
  Awesome Show (2026) - S06E01 - Episode 41.mkv: setting stream#1 (eng, aac) as default audio

[4/5] /path/to/videos/TV Shows/Awesome Show (2026)/Season 06/Awesome Show (2026) - S06E04 - Episode 44.mkv
  Awesome Show (2026) - S06E04 - Episode 44.mkv: setting stream#1 (eng, aac) as default audio

Processing:  27%|██████████████▊                                        | 1.35/5 [00:04<00:12,  3.56s/file]
```

Files finish in whatever order they're done, so the `[i/N]` numbers aren't in sequence.

</details>

<details>
<summary>Dry run (--dry-run)</summary>

```text
$ python3 set_stereo_default.py "/path/to/videos/TV Shows/Awesome Show (2026)/Season 04" --dry-run
Found 8 file(s) (dry run).

[1/8] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E01 - Episode 25.mkv
  Awesome Show (2026) - S04E01 - Episode 25.mkv: setting stream#1 (eng, aac) as default audio
    [dry-run] mkvmerge --gui-mode --output-charset UTF-8 -o '.../S04E01 - Episode 25.mkv.tmp_remux.mkv' --default-track 1:yes --default-track 4:no '.../S04E01 - Episode 25.mkv'

...

----- Summary (Dry run, nothing was changed) -----
Would change: 8
Unchanged: 0
Skipped: 0
Error: 0
```

Each `[dry-run]` line is the exact command the script would run, quoted so you can paste it into a shell.

</details>

## 🔍 How it works

### 🎯 Picking the track

The script looks for the audio track with exactly 2 channels and makes it the default, clearing the default flag from every other audio track. Two kinds of stereo track are passed over:

- **Commentary and audio description.** These are often stereo but shouldn't play by default. A track counts as one if the file flags it that way, or if its name contains "commentary", "audio description", "descriptive", "described" or "DVS".
- **Other languages.** The stereo track has to be in the same language as the track that plays by default now, so an English 5.1 film with a Spanish stereo dub keeps playing in English. Use `--prefer-lang` to choose the language yourself. Tracks with no language tag (or `und`) match any language, but a track tagged with the right language wins over them.

  Language tags are compared by meaning, not spelling. The same language can be tagged several ways (MKV files use `ger` for German, MP4 files often `deu`, and you might type `de`), so two- and three-letter codes are treated as equal, and region parts like the `-BR` in `pt-BR` are ignored.

A file is **skipped** (and counted under `Skipped` in the summary) when:

- it has no audio at all
- it has no stereo track, or only commentary/audio-description ones
- none of its stereo tracks is in the wanted language
- several stereo tracks qualify and `--prefer-lang` doesn't settle it
- it's an `.avi` file and `--avi-reorder` wasn't given

### 🧩 Changing the file

Every change is a **remux**: the audio and video are copied as-is into a new file with the flags fixed. That new file is checked (same number of streams, the right track flagged, and not noticeably shorter than the original) before it replaces the original. If the check fails, the original is kept and the file is counted as an `Error`.

> [!NOTE]
> A remux that comes out more than 1% shorter than the original (and at least 1 second shorter) is rejected. That usually means the original contains less than its header claims, such as an incomplete download. The file is left alone so you can check it, and is reported as an `Error` on every run until it's replaced.

| Format | How it's changed |
| --- | --- |
| `.mkv` `.webm` | Remuxed with `mkvmerge`. The script doesn't edit the file in place, because in-place edits can move the track list to the end of the file, which breaks Windows Explorer thumbnails even though the video plays fine. |
| `.mp4` `.m4v` `.mov` | Remuxed with `ffmpeg`, with the file's index kept at the front where thumbnailers expect it (`-movflags +faststart`). |
| `.avi` | AVI has no "default track" flag. With `--avi-reorder`, the stereo track is moved to the front instead, which most players treat the same way. Without it, AVI files are skipped. |

### 🛟 Backups

With `--backup`, each original is kept next to the new file as `<name>.bak`. If some files already have a `.bak` (from an earlier run, say), you're asked once, before any file is changed:

```text
3 file(s) already have a backup. If they're changed: [d]elete and replace the old backup, [n]umber the new one (.bak.1, .bak.2...), or [q]uit?
```

The question comes before any file is checked, so the count can include files that turn out to be correct already. Those are left alone whatever you answer; it only matters for files that get changed.

- **d** replaces each existing `<name>.bak` with the new backup.
- **n** keeps every existing backup and saves the new one as the first free `<name>.bak.1`, `<name>.bak.2`, ...
- **q** stops without changing anything.

To skip the question, pass `--existing-backups replace` or `--existing-backups number`. When there's no one to ask (cron, Docker, Windows Task Scheduler, or input or output redirected), new backups are numbered, since that never deletes anything. The same happens if the question gets no answer at all (Ctrl+D).

### 🪢 Symlinks

A symlinked video is fixed through its link: the script changes the file the link points to, and the link keeps working. (Replacing the link itself would turn it into a separate copy and leave the real file unfixed.) A file reached through several links, or passed more than once, is only processed once. Use `--skip-symlinks` to leave linked files alone.

Symlinked subfolders aren't searched unless you add `--follow-symlinks`; each one that's skipped is logged. Folders you name on the command line are always searched, even if they're symlinks themselves. With `--follow-symlinks`, a link that loops back on itself is only searched once.

### 🔐 Permissions and ownership

A remux creates a brand-new file, so the script copies the original's permissions and owner onto it. That way tools that share your media through a group (Sonarr, Radarr, Plex, other containers) keep access to it. If the new file already has the right owner, it's left as it is.

Changing a file's owner requires root. If the script can't do it, the permissions are still copied, and one warning at the end of the run, just before the summary, says how many files are affected, where the full list is, and which owner they should have:

```text
Couldn't give 5 remuxed files their original owner (Operation not permitted). You can view the full list of files here: /home/tronyx/set_stereo_default-owners-20261002-153012.log

These files should belong to tronyx:users (1000:100) but belong to nobody:nogroup (65534:65534). Permissions were still copied. ...
```

The list has one full path per line, sorted. It's saved next to your `--log-file` if you use one, otherwise in the folder you ran the script from (or your system's temp folder if that one isn't writable). Each run gets its own list, so an earlier one is never overwritten. If only one file is affected, the warning names it instead.

> [!IMPORTANT]
> **On an NFS share, run the script as the user that owns your media, not as root.** NFS servers usually turn root into `nobody` ("root squashing"), so files the script creates as root end up owned by `nobody`, and root can't change that from the client. The tools that manage your media may then be unable to rename or replace those files. Running as the media's owner avoids it, because NFS keeps that user's ID:
>
> ```bash
> sudo -u tronyx python3 set_stereo_default.py /mnt/media
> ```
>
> The warning suggests the right `sudo -u` for your files. To fix files that already ended up owned by `nobody`, run `chown` on the NFS server itself, where root isn't squashed.

### 📅 File dates

By default, a changed file gets the time of the run as its modification date, like any newly written file. Some media servers and players sort "recently added" or "recently modified" by that date, so fixed files can jump to the top of those lists.

With `--keep-dates`, each changed file gets the original's modification and access dates instead. It's off by default for a reason: tools that decide whether a file changed by comparing its size and date (rsync's default, and some backup software) could skip a fixed file whose size happens to come out the same, leaving your backup copy with the old version. If you use one, keep the default or make it compare file contents (`rsync --checksum`).

> [!NOTE]
> Some dates can't be kept either way: the "changed" time (ctime) on Linux and macOS always updates, and on Windows a file's "Date created" becomes the time of the run.

### 🧹 Leftover temp files

Ctrl+C and SIGTERM (what `docker stop`, `kill` and systemd send) stop the script cleanly: running remuxes are killed, their temp files are removed, and finished files are untouched. The partial summary counts every file that didn't finish as `Cancelled`.

If the script is killed outright instead (`kill -9`, a power cut, a container that doesn't stop in time), a `<name>.tmp_remux.<ext>` file can be left next to the original. The next run skips these with a warning. They're safe to delete, since the original is only ever replaced by a finished, checked file.

## ⚠️ Limitations

> [!CAUTION]
> Both of these come from the same thing: every change replaces the original with a new file.

### 🔗 Hard links will be broken

If another path is hard-linked to the original (a common Sonarr/Radarr/qBittorrent setup, linking a download folder to a library folder), that path keeps pointing at the old, unfixed file, and the two no longer share disk space. If you rely on hard links, run this script *before* linking, or re-link the affected files afterwards. (Symlinks don't have this problem; see [Symlinks](#-symlinks).)

### 🌱 Seeding and cross-seeding will break

Even though nothing is re-encoded, the remuxed file's bytes are different, so it no longer matches the torrent's piece hashes. Any torrent seeding that file, including cross-seeds sharing it through a hard link, will fail its hash check. Don't run this on files you're seeding unless you're ready to re-download or re-check them, and check your private trackers' rules first, since a failed hash check can look like a hit-and-run.

## 🚦 Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Finished with no errors, or you chose **q** at the [backup question](#-backups) |
| `1` | No matching files found, a required tool is missing, or at least one file ended up as an `Error` |
| `2` | Invalid command-line options |
| `130` | Stopped with Ctrl+C |
| `143` | Stopped with SIGTERM |

If you're running this from cron or another script, the exit code tells you whether anything went wrong. The printed summary has the details.

## 🩺 Troubleshooting

Every skipped or failed file gets a line saying why. Here's what the common ones mean.

**`SKIP (...)` on a file you expected to change.** Usually the stereo track is in a different language from the current default, or several stereo tracks qualify. Both are settled with `--prefer-lang`. See [Picking the track](#-picking-the-track).

**`post-remux check failed (duration dropped from ...)`.** The original contains less than its header claims, usually because it's an incomplete download. It's left untouched; re-download it or check it in a player. See [Changing the file](#-changing-the-file).

**`mkvmerge sees N audio track(s), but ffprobe sees M`.** The two tools disagree about the file, so the script won't guess which track is which and leaves it alone. Please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues) with the file's `mkvmerge -J` output.

**`Couldn't give remuxed files their original owner`.** The new files play fine, but belong to the wrong user, which can stop Sonarr, Radarr and similar tools from renaming or replacing them. On an NFS share, run the script as the user that owns your media; the warning shows the `sudo -u` command. See [Permissions and ownership](#-permissions-and-ownership).

**`Skipping leftover temp file`.** An earlier run was killed mid-file. The temp file is safe to delete. See [Leftover temp files](#-leftover-temp-files).

**No thumbnail in Windows Explorer.** If another tool (such as `mkvpropedit`) edited the file in place, its track list may have moved to the end of the file, where Explorer doesn't look. `--force` remuxes the file even when the right track is already default, which puts everything back where it belongs.

**`Missing required tool(s)`.** ffmpeg, ffprobe or mkvmerge isn't on your `PATH`. See [Installation](#-installation).

## 🧪 Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest
```

There are two sets of tests:

- **Logic tests** ([tests/test_set_stereo_default.py](tests/test_set_stereo_default.py)) cover the script's own decisions: picking the track, finding files, backups, checking a remux, progress reporting and clean stopping. They stand in for ffmpeg and mkvmerge, so they run anywhere.
- **Real-file tests** ([tests/test_real_files.py](tests/test_real_files.py)) use ffmpeg to create small MKV, MP4 and AVI files for each case the script handles, run the script on them, and check the results. They need ffmpeg, ffprobe and mkvmerge on your `PATH`, and are skipped if those aren't installed. Set `REQUIRE_MEDIA_TOOLS=1` to make a missing tool fail them instead, as GitHub does.

GitHub runs both on every push and pull request, plus once a week, so a new ffmpeg release that breaks something gets noticed (see [.github/workflows/tests.yml](.github/workflows/tests.yml)). The logic tests run on the oldest and newest supported Python versions. The real-file tests run against every ffmpeg version listed under [Requirements](#-requirements) on Linux. Both also run on Windows, with the newest ffmpeg and MKVToolNix.

To see which lines of the script the logic tests reach, run them under coverage. GitHub does the same on every run and shows the result on the run's summary page; it's for information only and never fails a build. The coverage badges at the top show the total for the latest push to each branch:

```bash
python -m coverage run -m pytest
python -m coverage report
```

The real-file tests run the script as a separate program, so the lines only they reach (the actual ffmpeg and mkvmerge runs, for example) show as missing in that report.

## 🤖 A note on how this was built

This script was largely written with [Claude](https://claude.ai), Anthropic's AI coding assistant. I described what I needed, directed the design, and asked for changes across many iterations rather than writing most of the code by hand. Every feature went through real testing before landing here, and the safety measures (dry-run mode, temp-file-first remuxing, checking every remux before it replaces the original) are exactly the kind of thing I insisted on, because this touches a media library I actually care about. Today the test suite covers both the script's logic and real video files across several ffmpeg and mkvmerge versions.

I'd rather say that plainly than let it pass as fully hand-written. There's a lot of AI-generated code floating around that hasn't been reviewed or tested and ends up breaking people's setups, and I don't want this to be mistaken for that. If something looks off, please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues).

## 📄 License

MIT, see [LICENSE.md](LICENSE.md).
