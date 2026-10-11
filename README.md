# 🔊 set_stereo_default

[![Tests](https://img.shields.io/github/actions/workflow/status/tronyx/Set-Stereo-Default/tests.yml?branch=master&label=tests)](https://github.com/tronyx/Set-Stereo-Default/actions/workflows/tests.yml?query=branch%3Amaster)
[![Coverage](https://img.shields.io/endpoint?url=https%3A%2F%2Fraw.githubusercontent.com%2Ftronyx%2FSet-Stereo-Default%2Fbadges%2Fcoverage-master.json&label=coverage)](docs/testing.md#-coverage)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)](docs/installation.md#-requirements)
[![License: MIT](https://img.shields.io/github/license/tronyx/Set-Stereo-Default)](LICENSE.md)

set_stereo_default makes the stereo audio track the default track in each video file of your library.

Many ripped and downloaded videos set a 5.1 or 7.1 surround track as the default audio track. On a TV, a soundbar, or a laptop, the dialogue is then often quiet and the effects are loud. `set_stereo_default.py` finds the stereo track (the audio track with 2 channels) in each file and makes it the default track. The script does not re-encode (compress again) the audio or the video, so the picture and the sound do not change.

This table shows the audio tracks of a typical movie before and after the change:

| Audio track | Before | After |
| --- | :---: | :---: |
| English, 5.1 surround | ✅ Default | |
| English, stereo | | ✅ Default |
| Director's commentary, stereo | | |

The commentary track is also stereo, but the script never picks a commentary track. For the rules, see [Picking the track](docs/how-it-works.md#-picking-the-track).

> [!WARNING]
> Use the script on a few files first. Start with `--dry-run`, and use `--backup` until you are satisfied with the results. This is beta software. I tested it carefully on my own library, but it is new.

## ✨ Features

The script has these features:

- 🛡️ It does not change a file that is already correct. `--dry-run` shows each change before the script makes it. Before it replaces an original file, it makes sure that the new file is correct.
- 🎯 It never picks a commentary track or an audio-description track. A stereo dub (a track in another language) never replaces the original language.
- 🧩 It works with MKV, WebM, MP4, M4V, MOV, and AVI files. For each format, it changes the file in a way that keeps the thumbnails in Windows Explorer.
- 📊 It shows a progress bar for the current file and a progress bar for the full batch.
- ⚡ `--jobs N` processes several files at the same time.
- 📝 `--log-file` writes the details to a file, so the console shows less.
- 🛑 If you press Ctrl+C or run `docker stop`, the script stops and does not leave incomplete files.
- 🐳 A Docker image for amd64 and arm64 contains everything that the script needs. See [Docker](docs/docker.md).

## 🚀 Quick start

### 🐳 With Docker

The Docker image contains the script and all the tools that it needs. It is available for `amd64` and `arm64` on Docker Hub (`tronyx/set-stereo-default`) and on GitHub (`ghcr.io/tronyx/set-stereo-default`). Each week, a new image is built with the security fixes from Alpine (the Linux system in the image). Before an image is published, a scanner looks for known vulnerabilities in it.

```bash
# 1. See what would change, without touching anything
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --dry-run

# 2. Try it for real on one folder, keeping backups
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default "/videos/Some Show" --backup

# 3. Happy? Run it on everything, 3 files at a time
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --jobs 3
```

The `-v` option makes your video folder available inside the container at `/videos`. Docker gives everything after the image name to the script. The `-it` options show the progress bars and let the script ask the [backup question](docs/how-it-works.md#-backups). The [Docker guide](docs/docker.md) tells you how to use several folders, keep the log, and run the script on a schedule. It also tells you how to run the script as the owner of the files, which most NFS shares need.

### 🐍 With Python

You must have Python 3.10 or newer and ffmpeg 4.4 or newer. For `.mkv` and `.webm` files, you must also have MKVToolNix 45 or newer. MKVToolNix 58 or newer is recommended. The [tqdm](https://github.com/tqdm/tqdm) package is optional. It shows the progress bars.

1. Download the script, and install tqdm:

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

3. Run the script:

   ```bash
   # 1. See what would change, without touching anything
   python3 set_stereo_default.py "/path/to/videos" --dry-run

   # 2. Try it for real on one folder, keeping backups
   python3 set_stereo_default.py "/path/to/videos/Some Show" --backup

   # 3. Happy? Run it on everything
   python3 set_stereo_default.py "/path/to/videos"
   ```

The script also searches the subfolders of each folder. You can also give it individual files. On Windows, type `py` instead of `python3`. [Installation](docs/installation.md) lists the tested versions and has a tip for Windows. [Usage](docs/usage.md) has all the other information.

## ⚙️ Common options

| Option | What it does |
| --- | --- |
| `--dry-run` | Show the changes, but do not make them |
| `--backup` | Keep each original file as `<name>.bak`. Each backup uses as much disk space as its original file |
| `--prefer-lang LANG` | Use the stereo track in this language (for example, `en`) if the file has one. If it does not, the usual rules apply |
| `--jobs N` | Process up to `N` files at the same time (default: `1`) |
| `--input-file FILE` | Also process the folders that `FILE` lists, one folder on each line, in that order |
| `--log-file PATH` | Write the details to a file. The console still shows warnings, errors, and the summary |

[Usage](docs/usage.md#%EF%B8%8F-all-options) describes all the options. The `--help` option also lists them.

## ⚠️ Before you run it on your whole library

> [!CAUTION]
> Each change replaces the original file with a new file. Read these three results before you run the script on your full library:
>
> - If another path is a hard link (a second name for the same file) to the file, that path keeps the old version. The two paths also stop sharing disk space. Sonarr, Radarr, and qBittorrent often make hard links. Run the script before you make the links, or make the links again after the script runs.
> - If a torrent seeds the file, its hash check (the test that the data did not change) fails. The script does not re-encode the file, but the bytes of the new file are different. A cross-seed that uses the same path also fails. On a private tracker, this failure can look like a hit-and-run (a download that you did not seed).
> - The script needs free disk space. While it runs, it needs about the size of your largest file. With `--backup`, each backup also uses as much disk space as its file, until you delete the backup.
>
> For more information, see [Limitations](docs/how-it-works.md#%EF%B8%8F-limitations) and [Disk space](docs/installation.md#-disk-space).

## 🤔 Why not edit the file in place?

Tools such as mkvpropedit can change the flags directly in the original file. This is much faster than writing a new file. But in MKV files, such an edit can move the track list to the end of the file. Then Windows Explorer cannot show a thumbnail. A new file also keeps the original safe, because the script tests the new file before it replaces the original in one step. [Why not edit the file in place?](docs/how-it-works.md#-why-not-edit-the-file-in-place) explains all the reasons.

## 📖 Documentation

| Page | Contents |
| --- | --- |
| [📦 Installation](docs/installation.md) | Requirements, the tested versions, installation on Linux, macOS, and Windows, and the disk space that the script needs |
| [🚀 Usage](docs/usage.md) | The files that the script processes and their order, lists of folders, all the options, sample output, and exit codes |
| [🐳 Docker](docs/docker.md) | Your videos in the container, several folders, file owners, the log, schedules, and image updates |
| [🔍 How it works](docs/how-it-works.md) | How the script picks the track and changes a file, why it does not edit files in place, backups, symlinks, file owners, file dates, temporary files, and limitations |
| [🩺 Troubleshooting](docs/troubleshooting.md) | What each message means, and what to do |
| [🧪 How it is tested](docs/testing.md) | The tests, and the tests that GitHub runs on each change |

## 🩺 Troubleshooting

For each file that the script skips or does not fix, it writes a line with the reason. At the end of the run, it lists again each file that got a warning and each file that it did not fix. It groups the files that it did not fix by the action that you must take. [Troubleshooting](docs/troubleshooting.md) explains each message. If a result still looks wrong, [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues).

## 🧪 How it is tested

Each change goes through tests on Linux, macOS, and Windows, and in the Docker image on `amd64` and `arm64`. The tests use ffmpeg versions from 4.4 to the newest release, and mkvmerge versions from 45 to the newest release. There is a test for each case that the script handles. Other tests use random track layouts, stop the script at random times, and kill the script while it changes a file. After each of these tests, each file must be correct or exactly the same as before. For more information, see [How it is tested](docs/testing.md).

## 🤝 Contributing

You can send bug reports, ideas, and pull requests. The [contributing guide](.github/CONTRIBUTING.md) tells you how. Do not open an issue to report a security vulnerability, because issues are public. Use the procedure in the [security policy](.github/SECURITY.md) instead. The [changelog](CHANGELOG.md) lists the changes in each release.

## 🤖 A note on how this was built

This script was largely written with [Claude](https://claude.ai), Anthropic's AI coding assistant. I described what I needed, directed the design, and asked for changes across many iterations rather than writing most of the code by hand. Every feature went through real testing before landing here, and the safety measures (dry-run mode, temp-file-first remuxing, checking every remux before it replaces the original) are exactly the kind of thing I insisted on, because this touches a media library I actually care about. Today the test suite covers both the script's logic and real video files across several ffmpeg and mkvmerge versions.

I'd rather say that plainly than let it pass as fully hand-written. There's a lot of AI-generated code floating around that hasn't been reviewed or tested and ends up breaking people's setups, and I don't want this to be mistaken for that. If something looks off, please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues).

## 📄 License

The license is MIT. See [LICENSE.md](LICENSE.md).
