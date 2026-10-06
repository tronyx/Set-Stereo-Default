# 🔊 set_stereo_default

[![Tests: master](https://img.shields.io/github/actions/workflow/status/tronyx/Set-Stereo-Default/tests.yml?branch=master&label=tests%3A%20master)](https://github.com/tronyx/Set-Stereo-Default/actions/workflows/tests.yml?query=branch%3Amaster)
[![Tests: develop](https://img.shields.io/github/actions/workflow/status/tronyx/Set-Stereo-Default/tests.yml?branch=develop&label=tests%3A%20develop)](https://github.com/tronyx/Set-Stereo-Default/actions/workflows/tests.yml?query=branch%3Adevelop)
[![Coverage: master](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ftronyx%2FSet-Stereo-Default%2Fbadges%2Fcoverage-master.json)](#-running-the-tests)
[![Coverage: develop](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ftronyx%2FSet-Stereo-Default%2Fbadges%2Fcoverage-develop.json)](#-running-the-tests)
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
- 🐳 **Docker image.** Everything it needs in one image, for amd64 and arm64 (see [Docker](#-docker)).

## 📋 Requirements

| What | Version | Needed for |
| --- | --- | --- |
| [Python](https://www.python.org) | 3.10 or newer | Everything |
| [ffmpeg and ffprobe](https://ffmpeg.org) | 4.4 or newer | Everything |
| [mkvmerge](https://mkvtoolnix.download) (part of MKVToolNix) | 45 or newer; 54 or newer recommended | `.mkv` and `.webm` files |
| [tqdm](https://github.com/tqdm/tqdm) | 4.60 or newer | Progress bars (optional) |

The script is tested against ffmpeg 4.4, 5.1, 6.1, 7.1 and the newest release, and mkvmerge 45, 65, 74, 82, 92 and the newest release.

Using Docker? The image has all of these built in, so you only need Docker itself (see [Docker](#-docker)).

> [!NOTE]
> ffmpeg versions older than 4.4 can't read the commentary and audio-description flags in MKV files, so they might make a commentary track the default.
>
> mkvmerge 52 and older drop the commentary, audio-description, hearing-impaired and original-language flags from MKV files when remuxing. The script notices and leaves files with those flags untouched, so with an older MKVToolNix, those files can't be fixed until you update it. 54 and newer keep every flag.

### 💾 Disk space

Each file is rewritten to a temporary copy next to the original before it's swapped in, so you need free space about the size of your largest file while the script runs.

`--backup` usually doesn't add to that during the run, because each backup starts as a hard link to the original rather than a copy. (Where hard links aren't supported, such as on exFAT drives and some network shares, the backup is a full copy from the start.) Either way, once the new file replaces the original, the backup holds the original's data on its own, so **every backup takes as much space as the file it backs up** until you delete it. Running `--backup` over a whole library needs about as much free space as the files that get changed.

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

To update later, run `git pull` in the `Set-Stereo-Default` folder. The [changelog](CHANGELOG.md) lists what's changed.

## 🚀 Quick start

```bash
# 1. See what would change, without touching anything
python3 set_stereo_default.py "/path/to/videos" --dry-run

# 2. Try it for real on one folder, keeping backups
python3 set_stereo_default.py "/path/to/videos/Some Show" --backup

# 3. Happy? Run it on everything
python3 set_stereo_default.py "/path/to/videos"
```

Folders are searched recursively. You can also pass individual files, or a mix of files and folders. They're processed in the order you give them, each folder's files in alphabetical order, so you can put the shows you care about most first. A path that doesn't exist, such as a mistyped one, is reported (`Skipping /path/to/vidoes: no such file or directory`) and the rest still run.

On Windows, type `py` instead of `python3`.

### 📃 A list of folders

To work through many shows or movies, list their folders in a text file, one per line, and pass it with `--input-file`:

```text
# Shows to fix this week
/mnt/data/media/Videos/TV Shows/Awesome Show 1 (2020)
'/mnt/data/media/Videos/TV Shows/Someone'\''s Awesome Show (1996)'
TV Shows/Cartoons
```

```bash
python3 set_stereo_default.py --input-file shows.txt --dry-run
```

The folders are processed in the list's order, after any paths on the command line. Each line can be:

- a plain path, spaces and all;
- a path quoted the way a shell or `ls` shows it, in single or double quotes (with `'\''` for an apostrophe) or with backslash-escaped spaces, so you can paste a listing straight from your terminal;
- relative to the list file's folder, like `TV Shows/Cartoons` above, or starting with `~` for your home folder.

Blank lines and lines starting with `#` are skipped. A path that doesn't exist is reported and skipped, and the rest still run.

To pipe a list in instead, use `--input-file -`, e.g. `ls -d "/mnt/data/media/Videos/TV Shows/B"* | python3 set_stereo_default.py --input-file - --dry-run`. The [backup question](#-backups) can't be asked then, so new backups are numbered.

## 🐳 Docker

The image has the script, Python, ffmpeg, MKVToolNix and tqdm, so there's nothing else to install. It runs on `amd64` and `arm64` (e.g. a Raspberry Pi 4 or 5, or an ARM-based NAS), and is published in two places:

| Registry | Image |
| --- | --- |
| Docker Hub | `tronyx/set-stereo-default` |
| GitHub | `ghcr.io/tronyx/set-stereo-default` |

`latest` is built from `master`, and rebuilt every week for Alpine's security fixes. `develop` is built from the `develop` branch.

```bash
# 1. See what would change, without touching anything
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --dry-run

# 2. Try it for real on one folder, keeping backups
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default "/videos/Some Show" --backup

# 3. Happy? Run it on everything, 3 files at a time
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --jobs 3
```

Everything after the image name is passed to the script, so every [option](#%EF%B8%8F-options) works the same way. The script works on `/videos` unless you name other paths, such as `"/videos/Some Show"` above.

With nothing at all after the image name, the image shows the built-in help instead of changing anything, so a bare `docker run` can never touch your files by accident. To fix everything with no other options, name the folder: `docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default /videos`.

**What the `docker run` options do:**

- **`-v "/path/to/videos:/videos"`** lets the container see your videos: the folder on your computer before the `:`, at `/videos` inside the container. The container can only see folders you mount, and the script works with paths inside it, so the output shows `/videos/...` rather than your real paths. Don't mount it read-only (`:ro`), since the script has to replace the files.
- **`-it`** gives the script a terminal, which shows the progress bars and lets it ask the [backup question](#-backups). Leave it out for cron and other scheduled runs: the bars are hidden and new backups are numbered.
- **`--rm`** removes the container once the run finishes.

**Several folders.** Mount each one under `/videos`, and the script works on all of them:

```bash
docker run --rm -it -v "/mnt/media/Movies:/videos/Movies" -v "/mnt/media/TV Shows:/videos/TV Shows" tronyx/set-stereo-default --dry-run
```

**A list of folders.** Keep the [list](#-a-list-of-folders) in the folder you mount, with paths relative to it, and give its path inside the container:

```bash
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --input-file /videos/shows.txt --dry-run
```

A list of your computer's own paths, like `/mnt/data/media/...`, works too if you mount that folder at the same path: `-v "/mnt/data/media:/mnt/data/media"`. To pipe a list in, use `-i` instead of `-it`.

**Running as the files' owner.** The container runs as root by default, which lets it give every remuxed file its original owner on a local disk. On an NFS share, root usually becomes `nobody` and can't change owners (see [Permissions and ownership](#-permissions-and-ownership)), so run the container as the user that owns your videos instead. `id -u` and `id -g` show your own IDs, and `stat -c '%u:%g' "/path/to/a/video.mkv"` shows a file's:

```bash
docker run --rm -it --user 1000:100 -v "/path/to/videos:/videos" tronyx/set-stereo-default /videos
```

**Keeping the log.** Anything written inside the container is gone when it exits, so point `--log-file` at the mounted folder. If remuxed files can't be given their owner, the list of them is saved next to the log too:

```bash
docker run --rm -v "/path/to/videos:/videos" tronyx/set-stereo-default --log-file /videos/set_stereo_default.log
```

**Running it on a schedule,** e.g. every night at 3 AM from cron, as the files' owner and with no terminal:

```text
0 3 * * * docker run --rm --user 1000:100 -v "/path/to/videos:/videos" tronyx/set-stereo-default --log-file /videos/set_stereo_default.log
```

**Good to know:**

- **Updating:** `docker pull tronyx/set-stereo-default` gets the newest image.
- **Stopping:** `docker stop` and Ctrl+C both stop the script cleanly, the same as without Docker.
- **Forgot the `-v`?** The script says `Nothing is mounted at /videos` and shows the `-v` to add, then exits with code `1` without changing anything.
- **Symlinks:** inside the container, a symlink is followed to the path it holds, and that path has to exist there too. A relative link within the mounted folder works. One that points elsewhere, or uses an absolute path from your computer (`/mnt/media/...`), only works if that path is also mounted, at the same place.
- **Docker Desktop (Windows and macOS):** the path before the `:` is one on your computer, e.g. `-v "D:\Videos:/videos"`.
- **Building it yourself:** `docker build -t set-stereo-default .` in this repository, then use `set-stereo-default` as the image name.

## ⚙️ Options

| Option | What it does |
| --- | --- |
| `--dry-run` | Show what would change without touching any files |
| `--backup` | Keep each original as `<name>.bak`. Each backup takes as much space as the original (see [Disk space](#-disk-space)) |
| `--existing-backups MODE` | With `--backup`, what to do when `<name>.bak` already exists: `replace` it, or `number` the new one (`.bak.1`, `.bak.2`, ...). Without this you're asked once (see [Backups](#-backups)) |
| `--prefer-lang LANG` | Language to use whenever a file has a stereo track in it, as a 2- or 3-letter code (`en`, `eng`, `de`, `ger` and `deu` all work). Also picks between several stereo tracks. Files without one are handled as usual, in the language of the track that plays by default now |
| `--input-file FILE` | Also process the paths listed in `FILE`, one per line, in order; `-` reads them from a pipe (see [A list of folders](#-a-list-of-folders)) |
| `--jobs N` | Work on up to `N` files at once (default: `1`) |
| `--log-file PATH` | Write the details to a file. Warnings, errors, the progress bar and the summary still show on the console |
| `--ext EXT1,EXT2` | Extensions to process (default: `mkv,webm,mp4,m4v,mov,avi`). This replaces the default list, so list every extension you want |
| `--no-recursive` | Don't look inside subfolders |
| `--skip-symlinks` | Skip symlinked files. By default, the file a link points to is fixed and the link keeps working |
| `--follow-symlinks` | Also look inside symlinked subfolders. By default they're skipped (folders you name on the command line are always searched) |
| `--avi-reorder` | For `.avi` files, move the stereo track to the front (see [AVI files](#-changing-the-file)) |
| `--force` | Remux even files that are already correct, e.g. to restore thumbnails (see [Troubleshooting](#-troubleshooting)) |
| `--keep-dates` | Give each changed file the original's modification date, so it doesn't look newly changed (see [File dates](#-file-dates)) |
| `--no-progress` | Hide the progress bars. They're already hidden when the output isn't a terminal (cron, `docker run` without `-t`, a pipe) |

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
    [dry-run] mkvmerge --gui-mode --output-charset UTF-8 -o '.../S04E01 - Episode 25.mkv.tmp_remux.mkv' --track-order 0:0,0:1,0:2,0:3,0:4 --default-track 1:yes --default-track 4:no '.../S04E01 - Episode 25.mkv'

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
- **Other languages.** The stereo track has to be in the same language as the track that plays by default now, so an English 5.1 film with a Spanish stereo dub keeps playing in English. Tracks with no language tag (or `und`) match any language, but a track tagged with the right language wins over them.

  `--prefer-lang` picks a language to use whenever a file has a stereo track in it. Files without one are handled as if you hadn't given it, so you can run it over a whole mixed library: with `--prefer-lang en`, an anime with an English stereo dub switches to English, while a French film with only a French stereo track still gets its French stereo track. The file's line says when that happened:

  ```text
  Le Film (2024).mkv: setting stream#2 (fre, aac) as default audio (no 2-channel track in 'en', so picked as if --prefer-lang wasn't given)
  ```

  Language tags are compared by meaning, not spelling. The same language can be tagged several ways (MKV files use `ger` for German, MP4 files often `deu`, and you might type `de`), so two- and three-letter codes are treated as equal, and region parts like the `-BR` in `pt-BR` are ignored.

A file is **skipped** (and counted under `Skipped` in the summary) when:

- It has no audio at all.
- It has no stereo track, or only commentary/audio-description ones.
- None of its stereo tracks is in the `--prefer-lang` language or the current one.
- Several stereo tracks qualify and `--prefer-lang` doesn't settle it (several in the `--prefer-lang` language skip the file, rather than falling back to another language).
- It's an `.avi` file and `--avi-reorder` wasn't given.

### 🧩 Changing the file

Every change is a **remux**: the audio and video are copied as-is into a new file with the flags fixed. That new file is checked before it replaces the original:

- every stream must still be there, with the same codec, language, name and flags;
- the right track must be the default;
- it mustn't be noticeably shorter than the original.

If the check fails, the original is kept and the file is counted as an `Error`. The one exception is a flag such as "commentary" in an MP4, MOV or AVI file: ffmpeg can't write those flags to these formats at all, so losing one is a warning rather than an error (see [Troubleshooting](#-troubleshooting)).

The new file also isn't used if the original changed while it was being made, for example because Sonarr or Radarr imported an upgrade over it. Swapping it in would undo that change, so the file is left as it is now and counted as an `Error`; the next run fixes the new version.

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

To skip the question, pass `--existing-backups replace` or `--existing-backups number`. When there's no one to ask (cron, Docker without `-it`, Windows Task Scheduler, or input or output redirected), new backups are numbered, since that never deletes anything. The same happens if the question gets no answer at all (Ctrl+D).

### 🪢 Symlinks

A symlinked video is fixed through its link: the script changes the file the link points to, and the link keeps working. (Replacing the link itself would turn it into a separate copy and leave the real file unfixed.) A file reached through several links, or passed more than once, is only processed once. Use `--skip-symlinks` to leave linked files alone.

Symlinked subfolders aren't searched unless you add `--follow-symlinks`; each one that's skipped is logged. Folders you name on the command line are always searched, even if they're symlinks themselves. With `--follow-symlinks`, a link that loops back on itself is only searched once.

### 🔐 Permissions and ownership

A remux creates a brand-new file, so the script copies the original's permissions and owner onto it. That way tools that share your media through a group (Sonarr, Radarr, Plex, other containers) keep access to it. If the new file already has the right owner, it's left as it is.

Changing a file's owner requires root. If the script can't do it, the permissions are still copied, and one warning at the end of the run, just before the summary, says how many files are affected, where the full list is, and which owner they should have:

```text
Couldn't give 5 remuxed files their original owner (Operation not permitted). You can view the full list of files here: /home/tronyx/set_stereo_default-owners-20261002-153012-48213.log

These files should belong to tronyx:users (1000:100) but belong to nobody:nogroup (65534:65534). Permissions were still copied. ...
```

The list has one full path per line, sorted. It's saved next to your `--log-file` if you use one, otherwise in the folder you ran the script from (or your system's temp folder if that one isn't writable). Its name has the date, time and the script's process ID, so each run gets its own list and an earlier one is never overwritten. If only one file is affected, the warning names it instead.

> [!IMPORTANT]
> **On an NFS share, run the script as the user that owns your media, not as root.** NFS servers usually turn root into `nobody` ("root squashing"), so files the script creates as root end up owned by `nobody`, and root can't change that from the client. The tools that manage your media may then be unable to rename or replace those files. Running as the media's owner avoids it, because NFS keeps that user's ID:
>
> ```bash
> sudo -u tronyx python3 set_stereo_default.py /mnt/media
> ```
>
> The warning suggests the right `sudo -u` for your files. In the Docker image, it suggests the matching `docker run --user` instead (see [Docker](#-docker)). To fix files that already ended up owned by `nobody`, run `chown` on the NFS server itself, where root isn't squashed.

### 📅 File dates

By default, a changed file gets the time of the run as its modification date, like any newly written file. Some media servers and players sort "recently added" or "recently modified" by that date, so fixed files can jump to the top of those lists.

With `--keep-dates`, each changed file gets the original's modification and access dates instead. It's off by default for a reason: tools that decide whether a file changed by comparing its size and date (rsync's default, and some backup software) could skip a fixed file whose size happens to come out the same, leaving your backup copy with the old version. If you use one, keep the default or make it compare file contents (`rsync --checksum`).

> [!NOTE]
> Some dates can't be kept either way: the "changed" time (ctime) on Linux and macOS always updates, and on Windows a file's "Date created" becomes the time of the run.

### 🧹 Leftover temp files

Ctrl+C and SIGTERM (what `docker stop`, `kill` and systemd send) stop the script cleanly: running remuxes are killed, their temp files are removed, and finished files are untouched. The partial summary counts every file that didn't finish as `Cancelled`.

If the script is killed outright instead (`kill -9`, a power cut, a container that doesn't stop in time), a `<name>.tmp_remux.<ext>` file can be left next to the original, or a `<name>.bak.tmp_remux.<ext>` one if it was making a backup. The next run skips these with a warning. They're safe to delete, since the original is only ever replaced by a finished, checked file, and a backup only takes its real name once it's complete.

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

**`post-remux check failed (stream#N lost its ... flag; ...)`.** Your mkvmerge is too old to keep a track flag (commentary, hearing impaired, ...) that the file has, so the file is left untouched. Update MKVToolNix to 54 or newer and run the script again. A similar check rejects a remux that changed a track's codec or language or lost its name.

**`stream#N lost its ... flag, which ffmpeg can't write to .mp4 files`.** A warning, not an error: the file was fixed, but ffmpeg can't store flags such as "commentary" in MP4, MOV or AVI files at all, so that flag is gone. The tracks themselves are unchanged, but players can no longer tell, for example, that a track is commentary unless its name says so.

**`changed by another program during the remux, so it's left as it is now`.** Another program, such as Sonarr or Radarr importing an upgrade, replaced or edited the file while the script was remuxing it. The remux was made from the old version, so it was discarded and the file was left as it is now. Run the script again to fix the new version. See [Changing the file](#-changing-the-file).

**`couldn't swap the new file in (Access is denied), so it's left as it was -- is it read-only, or open in another program?`.** On Windows, a file another program has open can't be replaced: a media player, or Plex, Jellyfin or Emby while it scans the file. The remux was discarded and the file left as it was, with no backup made. Close the other program, or wait for the scan to finish, and run the script again. A read-only file gives the same error; clear the attribute first. A file another program has opened exclusively can't even be read, and is reported as `ffprobe failed on <name>: ... Permission denied` instead.

**`can't use the temp name <name>.tmp_remux.<ext> (...); the name may be too long for this file system`.** The remux is written next to the original under its name plus `.tmp_remux.<ext>`, and most file systems allow names of 255 characters at most, so a file whose name is already close to that can't be fixed. Shorten the name and run the script again.

**`mkvmerge sees N audio track(s), but ffprobe sees M`.** The two tools disagree about the file, so the script won't guess which track is which and leaves it alone. Please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues) with the file's `mkvmerge -J` output.

**`Couldn't give remuxed files their original owner`.** The new files play fine, but belong to the wrong user, which can stop Sonarr, Radarr and similar tools from renaming or replacing them. On an NFS share, run the script as the user that owns your media; the warning shows the `sudo -u` command to use, or in the Docker image, the `docker run --user` one. See [Permissions and ownership](#-permissions-and-ownership).

**`Skipping leftover temp file`.** An earlier run was killed mid-file. The temp file is safe to delete. See [Leftover temp files](#-leftover-temp-files).

**No thumbnail in Windows Explorer.** If another tool (such as `mkvpropedit`) edited the file in place, its track list may have moved to the end of the file, where Explorer doesn't look. `--force` remuxes the file even when the right track is already default, which puts everything back where it belongs.

**`Missing required tool(s)`.** ffmpeg, ffprobe or mkvmerge isn't on your `PATH`. See [Installation](#-installation).

## 🧪 Running the tests

```bash
pip install -r requirements-dev.txt
python -m pytest          # the tests
python -m ruff check .    # lint the code
python -m mypy            # check the type hints
```

GitHub runs all three on every push, so a pull request needs to pass each one.

There are two sets of tests:

- **Logic tests** ([tests/test_set_stereo_default.py](tests/test_set_stereo_default.py)) cover the script's own decisions: picking the track, finding files, backups, checking a remux, progress reporting and clean stopping. They stand in for ffmpeg and mkvmerge, so they run anywhere.
- **Real-file tests** ([tests/test_real_files.py](tests/test_real_files.py)) use ffmpeg to create small MKV, MP4, WebM and AVI files for each case the script handles, run the script on them, and check the results. Some files also have subtitles, chapters, track names, a font attachment or cover art, and others use each common audio codec (AAC, AC3, E-AC3, DTS, TrueHD, FLAC and Opus); all of it must come through the remux unchanged. They also try 30 random layouts each run (up to four audio tracks with random channels, codecs, languages, names and flags, in random order, with or without subtitles and a font), checking what the script does against a separate restatement of the [Picking the track](#-picking-the-track) rules; GitHub tries 300 every week. On Linux and macOS they also stop the script with Ctrl+C or SIGTERM at 10 random moments of a run (100 weekly), with random `--jobs` and sometimes `--backup`, and check that it exits cleanly, that every file is left either as it was or properly fixed, and that a second run finishes the job. The layouts and moments come from a seed that each test's ID shows, so `SSD_FUZZ_SEED=<seed>` replays a failure and `SSD_FUZZ_CASES=<count>` sets how many to try. Run as a user who isn't root, they also try a folder that user can't write to and a file they can't read, and in the Docker image, where GitHub runs them as `nobody`, a 2 MB disk the remux doesn't fit on (set `SSD_FULL_DISK` to a folder on a small file system to try that elsewhere): each is reported, the file left as it was, and nothing left behind. They need ffmpeg, ffprobe and mkvmerge on your `PATH` (and, for a few of them, an ffmpeg that can encode Opus and VP8, as most builds can), and are skipped if those aren't installed. Set `REQUIRE_MEDIA_TOOLS=1` to make a missing tool fail them instead, as GitHub does.

GitHub runs both on every push and pull request, plus once a week, so a new ffmpeg release that breaks something gets noticed (see [.github/workflows/tests.yml](.github/workflows/tests.yml)). The logic tests run on the oldest and newest supported Python versions. The real-file tests run on Linux against every ffmpeg and mkvmerge version listed under [Requirements](#-requirements). Both also run on Windows and macOS, with the newest ffmpeg and MKVToolNix. A third job lints the code (`python -m ruff check .`) and checks its type hints (`python -m mypy`), for Linux and for Windows, then lints the workflow, the `Dockerfile` and the Markdown files. A fourth builds the Docker image on both `amd64` and `arm64`, runs both sets of tests inside it with the image's own tools, then runs the image the ways people will: as a regular user, as root (checking each file keeps its owner and permissions), and stopped with `docker stop` partway through a remux.

To see which lines of the script the logic tests reach, run them under coverage. GitHub does the same on every run and shows the result on the run's summary page; it's for information only and never fails a build. The coverage badges at the top show the total for the latest push to each branch:

```bash
python -m coverage run -m pytest
python -m coverage report
```

Coverage only counts code that runs inside the test process. The logic tests reach almost every line, because they drive the script's own code for running ffmpeg and mkvmerge with small Python stand-ins. The real-file tests run the script as a separate program, so they don't add to the number.

## 🤖 A note on how this was built

This script was largely written with [Claude](https://claude.ai), Anthropic's AI coding assistant. I described what I needed, directed the design, and asked for changes across many iterations rather than writing most of the code by hand. Every feature went through real testing before landing here, and the safety measures (dry-run mode, temp-file-first remuxing, checking every remux before it replaces the original) are exactly the kind of thing I insisted on, because this touches a media library I actually care about. Today the test suite covers both the script's logic and real video files across several ffmpeg and mkvmerge versions.

I'd rather say that plainly than let it pass as fully hand-written. There's a lot of AI-generated code floating around that hasn't been reviewed or tested and ends up breaking people's setups, and I don't want this to be mistaken for that. If something looks off, please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues).

## 📄 License

MIT, see [LICENSE.md](LICENSE.md).
