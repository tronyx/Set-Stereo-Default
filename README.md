# 🔊 set_stereo_default

[![Tests](https://img.shields.io/github/actions/workflow/status/tronyx/Set-Stereo-Default/tests.yml?branch=master&label=tests)](https://github.com/tronyx/Set-Stereo-Default/actions/workflows/tests.yml?query=branch%3Amaster)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ftronyx%2FSet-Stereo-Default%2Fbadges%2Fcoverage-master.json&label=coverage)](docs/testing.md#-coverage)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](docs/installation.md#-requirements)
[![License: MIT](https://img.shields.io/github/license/tronyx/Set-Stereo-Default)](LICENSE.md)

**Make the stereo track play by default, across your whole video library.**

Plenty of ripped and downloaded videos flag a 5.1 or 7.1 surround track as the default audio. On a TV, soundbar or laptop, that often means quiet dialogue and booming effects. `set_stereo_default.py` finds the 2-channel stereo track in each file and makes it the default instead. Nothing is re-encoded, so picture and sound quality stay exactly the same.

Here's a typical movie before and after:

| Audio track | Before | After |
| --- | :---: | :---: |
| English, 5.1 surround | ✅ default | |
| English, stereo | | ✅ default |
| Director's commentary, stereo | | |

The commentary is stereo too, but it's never picked (see [Picking the track](docs/how-it-works.md#-picking-the-track)).

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
- 🐳 **Docker image.** Everything it needs in one image, for amd64 and arm64 (see [Docker](docs/docker.md)).

## 🚀 Quick start

### 🐳 With Docker

The image has the script and everything it needs, for `amd64` and `arm64`, on Docker Hub (`tronyx/set-stereo-default`) and GitHub (`ghcr.io/tronyx/set-stereo-default`). It's rebuilt every week for Alpine's security fixes, and scanned for known vulnerabilities before it's published.

```bash
# 1. See what would change, without touching anything
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --dry-run

# 2. Try it for real on one folder, keeping backups
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default "/videos/Some Show" --backup

# 3. Happy? Run it on everything, 3 files at a time
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --jobs 3
```

`-v` lets the container see your videos, at `/videos` inside it, and everything after the image name is passed to the script. `-it` shows the progress bars and lets the script ask the [backup question](docs/how-it-works.md#-backups). The [Docker guide](docs/docker.md) covers several folders, running as the files' owner (needed on most NFS shares), keeping the log, and running on a schedule.

### 🐍 With Python

You need Python 3.10 or newer, ffmpeg 4.4 or newer, and for `.mkv` and `.webm` files, MKVToolNix 45 or newer (54 or newer recommended). [tqdm](https://github.com/tqdm/tqdm) adds the progress bars.

1. Get the script, and optionally tqdm:

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

3. Run it:

   ```bash
   # 1. See what would change, without touching anything
   python3 set_stereo_default.py "/path/to/videos" --dry-run

   # 2. Try it for real on one folder, keeping backups
   python3 set_stereo_default.py "/path/to/videos/Some Show" --backup

   # 3. Happy? Run it on everything
   python3 set_stereo_default.py "/path/to/videos"
   ```

Folders are searched recursively, and you can pass individual files too. On Windows, type `py` instead of `python3`. [Installation](docs/installation.md) has the versions tested and a tip for Windows, and [Usage](docs/usage.md) has the rest.

## ⚙️ Common options

| Option | What it does |
| --- | --- |
| `--dry-run` | Show what would change without touching any files |
| `--backup` | Keep each original as `<name>.bak`. Each backup takes as much space as the original |
| `--prefer-lang LANG` | Use the stereo track in this language wherever a file has one, e.g. `en`. Files without one are handled as usual |
| `--jobs N` | Work on up to `N` files at once (default: `1`) |
| `--input-file FILE` | Also process the folders listed in `FILE`, one per line, in order |
| `--log-file PATH` | Write the details to a file. Warnings, errors and the summary still show on the console |

Every option is described in [Usage](docs/usage.md#%EF%B8%8F-all-options), and `--help` lists them too.

## ⚠️ Before you run it on your whole library

> [!CAUTION]
> Every change replaces the original with a new file, which has three consequences:
>
> - **Hard links break.** Another path hard-linked to the file, as Sonarr, Radarr and qBittorrent often set up, keeps the old, unfixed version, and the two stop sharing disk space. Run the script before linking, or re-link afterwards.
> - **Seeding breaks.** Nothing is re-encoded, but the new file's bytes differ, so any torrent seeding it, cross-seeds included, fails its hash check. On a private tracker that can look like a hit-and-run.
> - **It needs free space:** about the size of your largest file while it runs. With `--backup`, every backup also takes as much space as the file it backs up, until you delete it.
>
> See [Limitations](docs/how-it-works.md#%EF%B8%8F-limitations) and [Disk space](docs/installation.md#-disk-space) for the details.

## 📖 Documentation

| Page | What's in it |
| --- | --- |
| [📦 Installation](docs/installation.md) | Requirements and the versions tested, installing on Linux, macOS and Windows, and the disk space it needs |
| [🚀 Usage](docs/usage.md) | Which files are processed and in what order, lists of folders, every option, sample output and exit codes |
| [🐳 Docker](docs/docker.md) | Mounting your videos, several folders, running as the files' owner, keeping the log, running on a schedule, and how the image is kept up to date |
| [🔍 How it works](docs/how-it-works.md) | How the track is picked, how a file is changed and checked, backups, symlinks, ownership, file dates, leftover temp files, and the limitations |
| [🩺 Troubleshooting](docs/troubleshooting.md) | What each message means, and what to do about it |
| [🧪 How it's tested](docs/testing.md) | The tests, and what GitHub runs on every change |

## 🩺 Troubleshooting

Every file that's skipped or couldn't be fixed gets a line saying why, and every file that couldn't be fixed is listed again at the end of the run, grouped by what to do about it. [Troubleshooting](docs/troubleshooting.md) explains each message. If something still looks wrong, please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues).

## 🧪 How it's tested

Every change is tested on Linux, macOS and Windows, against ffmpeg versions from 4.4 to the newest release and mkvmerge versions from 45 to the newest, and inside the Docker image on `amd64` and `arm64`. Besides a test for each case the script handles, the tests try random track layouts, stop the script at random moments and kill it outright partway through a file, and every file must come out either fixed or exactly as it was. See [How it's tested](docs/testing.md).

## 🤝 Contributing

Bug reports, ideas and pull requests are welcome; see the [contributing guide](.github/CONTRIBUTING.md). To report a security vulnerability, please see the [security policy](.github/SECURITY.md) instead of opening an issue. The [changelog](CHANGELOG.md) lists what's changed in each release.

## 🤖 A note on how this was built

This script was largely written with [Claude](https://claude.ai), Anthropic's AI coding assistant. I described what I needed, directed the design, and asked for changes across many iterations rather than writing most of the code by hand. Every feature went through real testing before landing here, and the safety measures (dry-run mode, temp-file-first remuxing, checking every remux before it replaces the original) are exactly the kind of thing I insisted on, because this touches a media library I actually care about. Today the test suite covers both the script's logic and real video files across several ffmpeg and mkvmerge versions.

I'd rather say that plainly than let it pass as fully hand-written. There's a lot of AI-generated code floating around that hasn't been reviewed or tested and ends up breaking people's setups, and I don't want this to be mistaken for that. If something looks off, please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues).

## 📄 License

MIT, see [LICENSE.md](LICENSE.md).
