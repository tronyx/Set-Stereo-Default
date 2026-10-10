# 🔍 How it works

[← All documentation](README.md)

## 🎯 Picking the track

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

## 🧩 Changing the file

Every change is a **remux**: the audio and video are copied as-is into a new file with the flags fixed. That new file is checked before it replaces the original:

- every stream must still be there, with the same codec, language, name and flags (except an MP4 or MOV chapter track, which ffmpeg writes afresh: there must just still be one);
- the right track must be the default;
- it mustn't be noticeably shorter than the original.

If the check fails, the original is kept and the file is counted as an `Error`. The one exception is a flag such as "commentary" in an MP4, MOV or AVI file: ffmpeg can't write those flags to these formats at all, so losing one is a warning rather than an error (see [Troubleshooting](troubleshooting.md)).

The new file also isn't used if the original changed while it was being made, for example because Sonarr or Radarr imported an upgrade over it. Swapping it in would undo that change, so the file is left as it is now and counted as an `Error`; the next run fixes the new version.

> [!NOTE]
> A remux that comes out more than 1% shorter than the original (and at least 1 second shorter) is rejected. The tools copy everything they can read, so that almost always means the original is damaged, usually an incomplete download. The file is left alone so you can check it, and is reported as an `Error` on every run until it's replaced.

| Format | How it's changed |
| --- | --- |
| `.mkv` `.webm` | Remuxed with `mkvmerge`. The script doesn't edit the file in place, because in-place edits can move the track list to the end of the file, which breaks Windows Explorer thumbnails even though the video plays fine. |
| `.mp4` `.m4v` `.mov` | Remuxed with `ffmpeg`, with the file's index kept at the front where thumbnailers expect it (`-movflags +faststart`). The chapter track is written afresh from the file's chapters rather than copied. |
| `.avi` | AVI has no "default track" flag. With `--avi-reorder`, the stereo track is moved to the front instead, which most players treat the same way. Without it, AVI files are skipped. |

## 🛟 Backups

With `--backup`, each original is kept next to the new file as `<name>.bak`. If some files already have a `.bak` (from an earlier run, say), you're asked once, before any file is changed:

```text
3 file(s) already have a backup. If they're changed: [d]elete and replace the old backup, [n]umber the new one (.bak.1, .bak.2...), or [q]uit?
```

The question comes before any file is checked, so the count can include files that turn out to be correct already. Those are left alone whatever you answer; it only matters for files that get changed.

- **d** replaces each existing `<name>.bak` with the new backup.
- **n** keeps every existing backup and saves the new one as the first free `<name>.bak.1`, `<name>.bak.2`, ...
- **q** stops without changing anything.

To skip the question, pass `--existing-backups replace` or `--existing-backups number`. When there's no one to ask (cron, Docker without `-it`, Windows Task Scheduler, or input or output redirected), new backups are numbered, since that never deletes anything. The same happens if the question gets no answer at all (Ctrl+D).

## 🪢 Symlinks

A symlinked video is fixed through its link: the script changes the file the link points to, and the link keeps working. (Replacing the link itself would turn it into a separate copy and leave the real file unfixed.) A file reached through several links, or passed more than once, is only processed once. Use `--skip-symlinks` to leave linked files alone.

Symlinked subfolders aren't searched unless you add `--follow-symlinks`; each one that's skipped is logged. Folders you name on the command line are always searched, even if they're symlinks themselves. With `--follow-symlinks`, a link that loops back on itself is only searched once.

## 🔐 Permissions and ownership

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
> The warning suggests the right `sudo -u` for your files. In the Docker image, it suggests the matching `docker run --user` instead (see [Docker](docker.md)). To fix files that already ended up owned by `nobody`, run `chown` on the NFS server itself, where root isn't squashed.

## 📅 File dates

By default, a changed file gets the time of the run as its modification date, like any newly written file. Some media servers and players sort "recently added" or "recently modified" by that date, so fixed files can jump to the top of those lists.

With `--keep-dates`, each changed file gets the original's modification and access dates instead. It's off by default for a reason: tools that decide whether a file changed by comparing its size and date (rsync's default, and some backup software) could skip a fixed file whose size happens to come out the same, leaving your backup copy with the old version. If you use one, keep the default or make it compare file contents (`rsync --checksum`).

> [!NOTE]
> Some dates can't be kept either way: the "changed" time (ctime) on Linux and macOS always updates, and on Windows a file's "Date created" becomes the time of the run.

## 🧹 Leftover temp files

Ctrl+C and SIGTERM (what `docker stop`, `kill` and systemd send) stop the script cleanly: running remuxes are killed, their temp files are removed, and finished files are untouched. The partial summary counts every file that didn't finish as `Cancelled`.

If the script is killed outright instead (`kill -9`, a power cut, a container that doesn't stop in time), a `<name>.tmp_remux.<ext>` file can be left next to the original, or a `<name>.bak.tmp_remux.<ext>` one if it was making a backup. The next run skips these with a warning. They're safe to delete, since the original is only ever replaced by a finished, checked file, and a backup only takes its real name once it's complete.

## ⚠️ Limitations

> [!CAUTION]
> Both of these come from the same thing: every change replaces the original with a new file.

### 🔗 Hard links will be broken

If another path is hard-linked to the original (a common Sonarr/Radarr/qBittorrent setup, linking a download folder to a library folder), that path keeps pointing at the old, unfixed file, and the two no longer share disk space. If you rely on hard links, run this script *before* linking, or re-link the affected files afterwards. (Symlinks don't have this problem; see [Symlinks](#-symlinks).)

### 🌱 Seeding and cross-seeding will break

Even though nothing is re-encoded, the remuxed file's bytes are different, so it no longer matches the torrent's piece hashes. Any torrent seeding that file, including cross-seeds sharing it through a hard link, will fail its hash check. Don't run this on files you're seeding unless you're ready to re-download or re-check them, and check your private trackers' rules first, since a failed hash check can look like a hit-and-run.
