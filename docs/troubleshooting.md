# 🩺 Troubleshooting

[← All documentation](README.md)

For each file that the script skips or cannot change, the script shows a line with the reason. This page tells you what the common lines mean.

At the end of the run, before the summary, the script shows a list of the files that it was not able to fix. The list gives the full path of each file. So no file gets lost in the long output of a run. The script puts these files into groups by what you must do:

- Damaged files.
- Files that another program has open.
- Files that another program changed during the run.
- All other files.

Before this list, the script shows a list of the files that got a warning. Each file has a short note about its warning. The script puts these files into groups by the type of warning:

- Files in which ffmpeg gave the default flag to a subtitle or video track.
- Files that lost flags that ffmpeg cannot write to MP4, M4V, MOV, or AVI files.
- Files for which mkvmerge showed warnings.
- Leftover temp files from an interrupted run, which the script skipped.

## `SKIP (...)` on a file that you expected to change

Usually, the stereo track is in a different language from the current default track, or several stereo tracks are possible. You can solve both problems with `--prefer-lang`. See [Picking the track](how-it-works.md#-picking-the-track).

## `only 9:41 of this 41:52 video could be read, so the file seems to be damaged or an incomplete download`

The script cannot read the file after a point in the video. So the script cannot fix the file, and it does not change it. Usually, the file is an incomplete download. Some download clients give a file its full size before they download it. So an incomplete download can have the correct size, but contain no data after a point. Such a file stops at the same point in each player.

Replace the file, for example with a new search in Sonarr or Radarr. Then run the script again.

In rare cases, the file plays to the end. Then no data is missing. Instead, the header of the file (the information at its start) gives a length that is too long. Some tools write such headers by mistake. To correct the header, remux the file yourself (copy its tracks into a new file), for example with `mkvmerge -o fixed.mkv original.mkv` for an MKV file. Then the script can fix the file. See [Changing the file](how-it-works.md#-changing-the-file).

## `post-remux check failed (stream#N lost its ... flag; ...)`

Your mkvmerge is too old to keep a flag (a marker on a track) that the file has. Examples are the commentary flag, the hearing-impaired flag, and the default flag on a second subtitle track. So the script does not change the file. Update MKVToolNix to version 58 or newer. Then run the script again.

A similar test rejects a remux that changed the codec or the language of a track, or that lost the name of a track.

## `stream#N lost its ... flag, which ffmpeg can't write to .mp4 files`

This line is a warning, not an error. The script fixed the file. But ffmpeg cannot store flags such as "commentary" in MP4, MOV, or AVI files, so the flag is gone. The tracks did not change. But now a player cannot know that a track is a commentary track, for example, unless the name of the track says so.

## `stream#N gained the default flag, which ffmpeg gives the first subtitle track in .mp4 files when no subtitle track has it`

This line is a warning, not an error. The script fixed the file. But in the original file, no subtitle track had the default flag. In such a file, ffmpeg always gives the default flag to the first subtitle track. Some players and media servers start subtitles automatically when a subtitle track has the default flag. With these players, the subtitles can now appear.

To turn the subtitles off, change the subtitle setting in your player. To remove the flag from the file, use MP4Box, which is part of [GPAC](https://gpac.io). The track ID is the stream number plus 1. For example, for `stream#3`, run this command:

```bash
MP4Box -disable 4 "file.mp4"
```

A later run of the script does not change the file again, because the correct audio track is already the default track. The warning can also name a video track. In that case, the original video track had no default flag, and ffmpeg gave it one.

## `changed by another program during the remux, so it's left as it is now`

Another program replaced or edited the file while the script remuxed it. For example, Sonarr or Radarr imported an upgrade (a better version of the video). The script made its new file from the old version. So the script deleted its new file and left the file as it is now. To fix the new version, run the script again. See [Changing the file](how-it-works.md#-changing-the-file).

## `couldn't swap the new file in (Access is denied), so it's left as it was -- is it read-only, or open in another program?`

On Windows, the script cannot replace a file that another program has open. Examples are a media player, or Plex, Jellyfin, or Emby while it scans the file. The script deleted its new file and did not change the original. It also did not make a backup. Close the other program, or wait until the scan is done. Then run the script again.

A read-only file causes the same error. Before you run the script again, remove the read-only attribute. If another program has exclusive access to the file (it blocks all other programs), the script cannot even read the file. Then the script shows `ffprobe failed on <name>: ... Permission denied` instead.

## `can't use the temp name <name>.tmp_remux.<ext> (...); the name may be too long for this file system`

The script writes the new file next to the original. The name of the new file is the original name plus `.tmp_remux.<ext>`. Most file systems allow names of 255 characters or fewer. So if the name of a file is already almost that long, the script cannot fix the file. Make the name shorter. Then run the script again.

## `mkvmerge sees N audio track(s), but ffprobe sees M`

The two tools do not agree about the file. The script does not guess which track is which, so it does not change the file. Please [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues), and include the `mkvmerge -J` output for the file.

## `Couldn't give remuxed files their original owner`

The new files play correctly, but they belong to the wrong user. Then Sonarr, Radarr, and similar tools can fail to rename or replace them. On an NFS share, run the script as the user that owns your media. The warning shows the `sudo -u` command to use. In the Docker image, it shows the `docker run --user` option instead. See [Permissions and ownership](how-it-works.md#-permissions-and-ownership).

## `Skipping leftover temp file`

An earlier run stopped suddenly while it processed a file. You can safely delete the temporary file. See [Leftover temp files](how-it-works.md#-leftover-temp-files).

## No thumbnail in Windows Explorer

Another tool, such as `mkvpropedit`, can edit a file in place (change the original file directly). After such an edit, the track list can be at the end of the file. Explorer does not look for it there. `--force` remuxes the file also when the correct track is already the default track. The remux puts the track list back where Explorer looks for it.

## `Missing required tool(s)`

ffmpeg, ffprobe, or mkvmerge is not on your `PATH` (the list of folders where your system looks for programs). See [Installation](installation.md).
