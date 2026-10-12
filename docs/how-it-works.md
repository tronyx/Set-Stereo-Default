# 🔍 How it works

[← All documentation](README.md)

## 🎯 Picking the track

The script looks for the audio track that has exactly 2 channels (a stereo track). It makes this track the default track, and it removes the default flag (a marker on a track) from all other audio tracks. The script does not pick two types of stereo track:

- Commentary tracks and audio-description tracks (a voice that describes the picture). These tracks are often stereo, but they must not play by default. A track is of this type if it has the commentary flag or the audio-description flag. It is also of this type if its name contains "commentary", "audio description", "descriptive", "described", or "DVS".
- Stereo tracks in other languages. The stereo track must be in the same language as the track that plays by default now. So an English 5.1 film with a Spanish stereo dub (a translated soundtrack) continues to play in English. A track without a language tag, or with the tag `und`, matches all languages. But a track with the correct language tag has priority over such a track.

  `--prefer-lang` sets a language to use when a file has a stereo track in that language. The script handles other files as if you did not give `--prefer-lang`. So you can use it on a library with many languages. For example, with `--prefer-lang en`, an anime with an English stereo dub changes to English. A French film with only a French stereo track still gets its French stereo track. When the script cannot use the `--prefer-lang` language, the line for the file says so:

  ```text
  Le Film (2024).mkv: setting stream#2 (fre, aac) as default audio (no 2-channel track in 'en', so picked as if --prefer-lang wasn't given)
  ```

  The script compares language tags by their meaning, not by their spelling. A language can have different tags. For example, MKV files use `ger` for German, MP4 files often use `deu`, and you can type `de`. So the script treats the two-letter code and the three-letter codes of a language as equal. It also ignores region parts, such as the `-BR` in `pt-BR`.

The script skips a file, and counts it under `Skipped` in the summary, in these cases:

- The file has no audio.
- The file has no stereo track, or only commentary or audio-description stereo tracks.
- No stereo track is in the `--prefer-lang` language or in the language of the current default track.
- Several stereo tracks are possible, and `--prefer-lang` does not pick one of them. If several stereo tracks are in the `--prefer-lang` language, the script skips the file. It does not use another language instead.
- The file is an `.avi` file, and you did not give `--avi-reorder`.

## 🧩 Changing the file

Each change is a remux. The script copies the audio and video without changes into a new file, with the correct flags. Before the new file replaces the original, the script tests it. The new file passes the tests only if:

- All streams (the tracks in the file) are still there, with the same codec (data format), language, name, and flags. A chapter track in an MP4 or MOV file is an exception. Because ffmpeg writes a new chapter track, the file must only still have a chapter track. If the original has chapters but lost its chapter track (ffprobe shows "Referenced QT chapter track not found"), the new file gets a chapter track. This repairs the file.
- The correct track is the default track.
- The new file is not noticeably shorter than the original.

If a test fails, the script keeps the original and counts the file as an `Error`. There is one exception: a flag such as "commentary" in an MP4, MOV, or AVI file. ffmpeg cannot write these flags to these formats. So if the new file loses such a flag, the script shows a warning, not an error (see [Troubleshooting](troubleshooting.md)).

In MP4, M4V, and MOV files, ffmpeg also sets one flag by itself. If no subtitle track has the default flag, ffmpeg gives it to the first subtitle track. It does the same with video tracks. The script cannot prevent this, so it uses the new file and shows a warning (see [Troubleshooting](troubleshooting.md)).

The script also does not use the new file if the original changed during the remux. For example, Sonarr or Radarr can import an upgrade (a better version of the video) over the original. The new file does not have this change. So the script leaves the file as it is and counts it as an `Error`. The next run fixes the new version.

> [!NOTE]
> The script rejects a new file if it is more than 1% shorter than the original and at least 1 second shorter. The tools copy all the data that they can read. So a shorter file almost always means that the original is damaged, usually because the download is incomplete. The script does not change the file, so you can examine it. Each run reports the file as an `Error` until the file is replaced.

| Format | How the script changes it |
| --- | --- |
| `.mkv` `.webm` | The script remuxes these files with `mkvmerge`. It does not edit the file in place (change the original file directly). An edit in place can move the track list to the end of the file. Then Windows Explorer cannot show a thumbnail, but the video still plays correctly (see [Why not edit the file in place?](#-why-not-edit-the-file-in-place)). |
| `.mp4` `.m4v` `.mov` | The script remuxes these files with `ffmpeg`. It keeps the index of the file at the start, where thumbnail programs look for it (`-movflags +faststart`). ffmpeg makes a new chapter track from the chapters of the file. It does not copy the old chapter track. |
| `.avi` | AVI files have no "default track" flag. With `--avi-reorder`, the script moves the stereo track to the front instead. Most players use the first track in the same way as a default track. Without `--avi-reorder`, the script skips AVI files. |

## 🤔 Why not edit the file in place?

An edit in place changes the flags directly in the original file. For MKV files, the mkvpropedit tool from MKVToolNix can do this. It is much faster than a remux, because it writes only a few kilobytes, not a copy of the full file. It also needs no free disk space. But the script uses a remux, for four reasons.

The first reason is the thumbnails in Windows Explorer. In an MKV file, a track that does not store the default flag counts as a default track. So many files do not store the default flag on their default track. To remove the flag from such a track, mkvpropedit must add data to the track list, so the track list becomes larger. If the larger track list does not fit in its old place, mkvpropedit writes it at the end of the file. The video still plays correctly, but Windows Explorer reads only the start of a file to make a thumbnail, so the thumbnail disappears.

Earlier versions of the script used mkvpropedit and had this problem. If another tool edited a file in place, `--force` remuxes it and puts the track list back where Windows Explorer looks for it (see [Troubleshooting](troubleshooting.md)).

The second reason is the protection of the original. With a remux, the script writes a new file and tests it. Then it replaces the original in one step, which is either complete or not done at all. An edit in place changes the original directly. If the power fails during the write, the original can be damaged. Also, the script can test the result only after the change.

The third reason is the backups. `--backup` keeps the original as a hard link (a second name for the same file). So a backup needs no extra disk space while the script runs (see [Backups](#-backups)). An edit in place changes the file under all its names, so such a backup changes too. With an edit in place, each backup must be a full copy.

The fourth reason is the hard links to other folders. Sonarr, Radarr, and qBittorrent often use a hard link to connect the file in the library folder to the file in the download folder. A remux gives the library path a new file. The download path keeps the old file, so a torrent that seeds from the download path continues to work. An edit in place changes the file under both paths, so this torrent fails its hash check too.

For MP4, M4V, and MOV files, the default flag is one bit in the header of each track. So an edit in place never moves data in these files. But the second, third, and fourth reasons apply to them too.

The remux also has costs. It copies the full file, so it is slower, and it needs free disk space (see [Disk space](installation.md#-disk-space)). The new file must get the owner of the original (see [Permissions and ownership](#-permissions-and-ownership)). And other hard links to the file break (see [Limitations](#%EF%B8%8F-limitations)). The script accepts these costs, because the original stays safe until a tested file replaces it.

## 🛟 Backups

With `--backup`, the script keeps each original file next to the new file, as `<name>.bak`. If some files already have a `.bak` file, for example from an earlier run, the script asks you once, before it changes any file:

```text
3 file(s) already have a backup. If they're changed: [d]elete and replace the old backup, [n]umber the new one (.bak.1, .bak.2...), or [q]uit?
```

The script asks the question before it examines the files. So the count can include files that are already correct. The script does not change these files, whatever your answer is. The answer applies only to the files that the script changes.

- `d` replaces each existing `<name>.bak` with the new backup.
- `n` keeps all existing backups. It saves the new backup with the first free number: `<name>.bak.1`, `<name>.bak.2`, and so on.
- `q` stops the script before it changes any file.

To prevent the question, give `--existing-backups replace` or `--existing-backups number`. If the script cannot ask you, it gives a number to each new backup, because numbered backups never delete anything. The script cannot ask you when it runs from cron, Windows Task Scheduler, or Docker without `-it`. It also cannot ask you when its input or output is redirected (connected to a file or a pipe). The script also uses numbers when the question gets no answer (Ctrl+D).

## 🪢 Symlinks

If a video is a symlink (a link to another file), the script fixes the file that the link points to. The link continues to work. The script does not replace the link itself, because the link then becomes a separate copy and the real file stays incorrect. The script processes a file only once, also when several links point to it or when you give it more than once. To leave linked files unchanged, use `--skip-symlinks`.

The script does not search subfolders that are symlinks, unless you add `--follow-symlinks`. The script writes a log line for each subfolder that it skips. It always searches the folders that you name on the command line, also when they are symlinks. With `--follow-symlinks`, the script searches a link that makes a loop only one time.

## 🔐 Permissions and ownership

A remux makes a new file. So the script copies the permissions and the owner of the original to the new file. Then the tools that share your media through a group (Sonarr, Radarr, Plex, or other containers) keep access to it. If the new file already has the correct owner, the script does not change it.

Only root can change the owner of a file. If the script cannot change the owner, it still copies the permissions. At the end of the run, before the summary, one warning tells you about these files. It gives the number of files, the location of the full list, and the correct owner:

```text
Couldn't give 5 remuxed files their original owner (Operation not permitted). You can view the full list of files here: /home/tronyx/set_stereo_default-owners-20261002-153012-48213.log

These files should belong to tronyx:users (1000:100) but belong to nobody:nogroup (65534:65534). Permissions were still copied. ...
```

The list has one full path on each line, in sorted order. If you use `--log-file`, the script saves the list next to the log. If not, it saves the list in the folder where you started the script. If the script cannot write to that folder, it uses the temporary folder of your system.

The name of the list has the date, the time, and the process ID of the script. So each run makes a new list and never writes over an earlier list. If only one file has this problem, the warning gives its name instead of a list.

> [!IMPORTANT]
> On an NFS share, run the script as the user that owns your media, not as root. NFS servers usually change root into the user `nobody` ("root squashing"). So the files that the script makes as root belong to `nobody`. Root cannot change this from the client (the computer that uses the share). Then the tools that manage your media can fail to rename or replace these files. If you run the script as the owner of the media, this problem does not occur, because NFS keeps the ID of that user:
>
> ```bash
> sudo -u tronyx python3 set_stereo_default.py /mnt/media
> ```
>
> The warning shows the correct `sudo -u` for your files. In the Docker image, it shows the matching `docker run --user` instead (see [Docker](docker.md)). If some files already belong to `nobody`, run `chown` on the NFS server. On the server, root is not squashed.

## 📅 File dates

By default, a changed file gets the time of the run as its modification date, as each new file does. Some media servers and players use this date to sort their "recently added" or "recently modified" lists. So the fixed files can move to the top of these lists.

With `--keep-dates`, each changed file gets the modification date and the access date of the original instead. This option is off by default because some tools find changed files by their size and date. Examples are rsync with its default configuration, and some backup software. If a fixed file has the same size as before, these tools can skip it. Then your backup copy keeps the old version. If you use such a tool, keep the default, or set the tool to compare the contents of files (`rsync --checksum`).

> [!NOTE]
> The script cannot keep some dates, with or without `--keep-dates`. On Linux and macOS, the "changed" time (ctime) always updates. On Windows, the "Date created" of a file becomes the time of the run.

## 🧹 Leftover temp files

Ctrl+C and SIGTERM (the signal that `docker stop`, `kill`, and systemd send) stop the script safely. The script stops the running remuxes and deletes their temporary files. It does not touch the files that are finished. The partial summary counts each file that did not finish as `Cancelled`.

Sometimes the script stops without time to clean up. Examples are `kill -9`, a power failure, or a container that does not stop in time. Then a `<name>.tmp_remux.<ext>` file can stay next to the original. If the script was making a backup, a `<name>.bak.tmp_remux.<ext>` file can stay instead. The next run skips these files and shows a warning.

You can safely delete these files. Only a finished file that passed all the tests replaces the original. A backup gets its real name only when it is complete.

## ⚠️ Limitations

> [!CAUTION]
> Both limitations have the same cause: each change replaces the original with a new file.

### 🔗 Hard links will be broken

A hard link is a second name for the same file. Sonarr, Radarr, and qBittorrent often use hard links to connect a download folder to a library folder. If another path is a hard link to the original, that path continues to point to the old, unfixed file. The two paths then do not share disk space any more.

If you use hard links, run this script before you make the links. Or make the links again after the script runs. Symlinks do not have this problem (see [Symlinks](#-symlinks)).

### 🌱 Seeding and cross-seeding will break

The script does not re-encode the file (convert the audio or video to a new format). But the bytes of the remuxed file are different. So the file does not match the piece hashes (checksums) of the torrent any more. Each torrent that seeds (shares) the file from the path that the script changed will fail its hash check. This includes a cross-seed that uses the same path. A torrent that seeds from another path, which is a hard link to the original, continues to work, because that path keeps the old file (see [Hard links will be broken](#-hard-links-will-be-broken)).

Do not run this script on files that you seed, unless you are ready to download them again or re-check them. Before you do this, read the rules of your private trackers. A failed hash check can look like a hit-and-run (a download without the required seeding).
