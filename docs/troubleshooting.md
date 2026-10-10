# 🩺 Troubleshooting

[← All documentation](README.md)

Every skipped or failed file gets a line saying why. Here's what the common ones mean. Every file that couldn't be fixed is also listed again at the end of the run, just before the summary, with its full path, grouped by what to do about it: damaged files, files another program has open, files another program changed during the run, and everything else. So none gets lost in a long run's output.

**`SKIP (...)` on a file you expected to change.** Usually the stereo track is in a different language from the current default, or several stereo tracks qualify. Both are settled with `--prefer-lang`. See [Picking the track](how-it-works.md#-picking-the-track).

**`only 9:41 of this 41:52 video could be read, so the file seems to be damaged or an incomplete download`.** The file stops being readable partway through, so it can't be fixed, and it was left as it is. It's usually an incomplete download: some download clients make a file its full size before downloading it, so one that never finished can be the right size with nothing after a point. Such a file stops playing at the same spot in any player. Replace it (in Sonarr or Radarr, search for it again), then run the script again. Rarely, the file plays to the end anyway: then nothing is missing, and its header just claims it's longer than it is, which some tools write by mistake. Remuxing it once yourself (e.g. `mkvmerge -o fixed.mkv original.mkv` for an MKV) gives it a correct header, and the script can then fix it. See [Changing the file](how-it-works.md#-changing-the-file).

**`post-remux check failed (stream#N lost its ... flag; ...)`.** Your mkvmerge is too old to keep a track flag (commentary, hearing impaired, ...) that the file has, so the file is left untouched. Update MKVToolNix to 54 or newer and run the script again. A similar check rejects a remux that changed a track's codec or language or lost its name.

**`stream#N lost its ... flag, which ffmpeg can't write to .mp4 files`.** A warning, not an error: the file was fixed, but ffmpeg can't store flags such as "commentary" in MP4, MOV or AVI files at all, so that flag is gone. The tracks themselves are unchanged, but players can no longer tell, for example, that a track is commentary unless its name says so.

**`changed by another program during the remux, so it's left as it is now`.** Another program, such as Sonarr or Radarr importing an upgrade, replaced or edited the file while the script was remuxing it. The remux was made from the old version, so it was discarded and the file was left as it is now. Run the script again to fix the new version. See [Changing the file](how-it-works.md#-changing-the-file).

**`couldn't swap the new file in (Access is denied), so it's left as it was -- is it read-only, or open in another program?`.** On Windows, a file another program has open can't be replaced: a media player, or Plex, Jellyfin or Emby while it scans the file. The remux was discarded and the file left as it was, with no backup made. Close the other program, or wait for the scan to finish, and run the script again. A read-only file gives the same error; clear the attribute first. A file another program has opened exclusively can't even be read, and is reported as `ffprobe failed on <name>: ... Permission denied` instead.

**`can't use the temp name <name>.tmp_remux.<ext> (...); the name may be too long for this file system`.** The remux is written next to the original under its name plus `.tmp_remux.<ext>`, and most file systems allow names of 255 characters at most, so a file whose name is already close to that can't be fixed. Shorten the name and run the script again.

**`mkvmerge sees N audio track(s), but ffprobe sees M`.** The two tools disagree about the file, so the script won't guess which track is which and leaves it alone. Please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues) with the file's `mkvmerge -J` output.

**`Couldn't give remuxed files their original owner`.** The new files play fine, but belong to the wrong user, which can stop Sonarr, Radarr and similar tools from renaming or replacing them. On an NFS share, run the script as the user that owns your media; the warning shows the `sudo -u` command to use, or in the Docker image, the `docker run --user` one. See [Permissions and ownership](how-it-works.md#-permissions-and-ownership).

**`Skipping leftover temp file`.** An earlier run was killed mid-file. The temp file is safe to delete. See [Leftover temp files](how-it-works.md#-leftover-temp-files).

**No thumbnail in Windows Explorer.** If another tool (such as `mkvpropedit`) edited the file in place, its track list may have moved to the end of the file, where Explorer doesn't look. `--force` remuxes the file even when the right track is already default, which puts everything back where it belongs.

**`Missing required tool(s)`.** ffmpeg, ffprobe or mkvmerge isn't on your `PATH`. See [Installation](installation.md).
