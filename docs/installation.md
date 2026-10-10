# 📦 Installation

[← All documentation](README.md)

## 📋 Requirements

| What | Version | Needed for |
| --- | --- | --- |
| [Python](https://www.python.org) | 3.10 or newer | Everything |
| [ffmpeg and ffprobe](https://ffmpeg.org) | 4.4 or newer | Everything |
| [mkvmerge](https://mkvtoolnix.download) (part of MKVToolNix) | 45 or newer; 54 or newer recommended | `.mkv` and `.webm` files |
| [tqdm](https://github.com/tqdm/tqdm) | 4.60 or newer | Progress bars (optional) |

The script is tested against ffmpeg 4.4, 5.1, 6.1, 7.1 and the newest release, and mkvmerge 45, 65, 74, 82, 92 and the newest release.

Using Docker? The image has all of these built in, so you only need Docker itself (see [Docker](docker.md)).

> [!NOTE]
> ffmpeg versions older than 4.4 can't read the commentary and audio-description flags in MKV files, so they might make a commentary track the default.
>
> mkvmerge 52 and older drop the commentary, audio-description, hearing-impaired and original-language flags from MKV files when remuxing. The script notices and leaves files with those flags untouched, so with an older MKVToolNix, those files can't be fixed until you update it. 54 and newer keep every flag.

## 🛠️ Installing it

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

To update later, run `git pull` in the `Set-Stereo-Default` folder. The [changelog](../CHANGELOG.md) lists what's changed.

## 💾 Disk space

Each file is rewritten to a temporary copy next to the original before it's swapped in, so you need free space about the size of your largest file while the script runs.

`--backup` usually doesn't add to that during the run, because each backup starts as a hard link to the original rather than a copy. (Where hard links aren't supported, such as on exFAT drives and some network shares, the backup is a full copy from the start.) Either way, once the new file replaces the original, the backup holds the original's data on its own, so **every backup takes as much space as the file it backs up** until you delete it. Running `--backup` over a whole library needs about as much free space as the files that get changed.
