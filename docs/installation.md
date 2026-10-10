# 📦 Installation

[← All documentation](README.md)

## 📋 Requirements

| What | Version | Needed for |
| --- | --- | --- |
| [Python](https://www.python.org) | 3.10 or newer | Everything |
| [ffmpeg and ffprobe](https://ffmpeg.org) | 4.4 or newer | Everything |
| [mkvmerge](https://mkvtoolnix.download) (part of MKVToolNix) | 45 or newer (58 or newer is recommended) | `.mkv` and `.webm` files |
| [tqdm](https://github.com/tqdm/tqdm) | 4.60 or newer | Progress bars (optional) |

The tests use ffmpeg 4.4, 5.1, 6.1, 7.1, and the newest release. They also use mkvmerge 45, 65, 74, 82, 92, and the newest release.

If you use Docker, you need only Docker. The image contains all of these tools. See [Docker](docker.md).

> [!NOTE]
> ffmpeg versions older than 4.4 cannot read the commentary flag and the audio-description flag (markers on a track) in MKV files. With these versions, the script can make a commentary track the default.
>
> When mkvmerge 52 or older remuxes (copies the tracks into a new file) an MKV file, it removes four flags. These are the commentary, audio-description, hearing-impaired, and original-language flags. The script finds this problem and does not change files that have these flags. So with an older MKVToolNix, the script cannot fix these files until you update MKVToolNix. mkvmerge 54 and newer keep these flags.
>
> mkvmerge 57 and older keep the default flag on only one track of each type. For example, if two subtitle tracks have the default flag, the second track loses it. The script finds this problem too and does not change these files. mkvmerge 58 and newer keep all the flags.

## 🛠️ Installing it

1. Download the script. The last command installs tqdm, which is optional and shows the progress bars:

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

3. Make sure that your system finds both tools:

   ```bash
   ffmpeg -version
   mkvmerge --version
   ```

> [!TIP]
> If Windows does not find `mkvmerge` after the installation, add the MKVToolNix folder to your `PATH`. The folder is usually `C:\Program Files\MKVToolNix`. Then open a new terminal.

To update the script, run `git pull` in the `Set-Stereo-Default` folder. The [changelog](../CHANGELOG.md) lists the changes.

## 💾 Disk space

The script writes each file to a temporary copy next to the original. Then it replaces the original with the copy. So while the script runs, you need free disk space of about the size of your largest file.

During the run, `--backup` usually does not need more space. Each backup starts as a hard link (a second name for the same file) to the original, not as a copy. Some file systems do not support hard links, for example exFAT drives and some network shares. On these file systems, each backup is a full copy from the start.

After the new file replaces the original, the backup keeps the data of the original. So each backup uses as much disk space as the file that it backs up, until you delete the backup. If you use `--backup` on your full library, you need about as much free space as the files that the script changes.
