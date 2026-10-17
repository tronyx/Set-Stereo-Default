# 🚀 Usage

[← All documentation](README.md)

## ▶️ Running it

```bash
# 1. See what would change, without touching anything
python3 set_stereo_default.py "/path/to/videos" --dry-run

# 2. Try it for real on one folder, keeping backups
python3 set_stereo_default.py "/path/to/videos/Some Show" --backup

# 3. Happy? Run it on everything
python3 set_stereo_default.py "/path/to/videos"
```

The script searches each folder and its subfolders. You can also give it individual files, or files and folders together. The script processes the paths in the order that you give them, and the files of each folder in alphabetical order. So you can put the shows that are most important to you first. If a path does not exist, for example because of a typing error, the script reports it (`Skipping /path/to/vidoes: no such file or directory`). Then the script continues with the other paths.

On Windows, type `py` instead of `python3`.

## 📃 A list of folders

To process many shows or movies, write their folders in a text file, one folder on each line. Then give the file to the script with `--input-file`:

```text
# Shows to fix this week
/mnt/data/media/Videos/TV Shows/Awesome Show 1 (2020)
'/mnt/data/media/Videos/TV Shows/Someone'\''s Awesome Show (1996)'
TV Shows/Cartoons
```

```bash
python3 set_stereo_default.py --input-file shows.txt --dry-run
```

The script processes the folders in the order of the list, after the paths on the command line. Each line can be one of these:

- A plain path, with its spaces.
- A path in quotes, as a shell or `ls` shows it. It can have single or double quotes (with `'\''` for an apostrophe), or a backslash before each space. So you can paste a list from your terminal.
- A path relative to the folder of the list file, like `TV Shows/Cartoons` in the example.
- A path that starts with `~` for your home folder.

The script skips empty lines and lines that start with `#`. If a path does not exist, the script reports it, skips it, and continues with the other paths.

To send the list through a pipe (the output of one command sent to another) instead, use `--input-file -`. For example: `ls -d "/mnt/data/media/Videos/TV Shows/B"* | python3 set_stereo_default.py --input-file - --dry-run`. The script then cannot ask the [backup question](how-it-works.md#-backups), so it gives a number to each new backup.

## ⚙️ All options

| Option | What it does |
| --- | --- |
| `--dry-run` | Show the changes, but do not make them |
| `--backup` | Keep each original file as `<name>.bak`. Each backup uses as much disk space as its original file (see [Disk space](installation.md#-disk-space)) |
| `--existing-backups MODE` | With `--backup`, what to do when `<name>.bak` already exists. `replace` replaces it. `number` gives the new backup a number (`.bak.1`, `.bak.2`, and so on). Without this option, the script asks you once (see [Backups](how-it-works.md#-backups)) |
| `--prefer-lang LANG` | The language to use when a file has a stereo track in it. Give a code of 2 or 3 letters: `en`, `eng`, `de`, `ger`, and `deu` all work. It also picks one of several stereo tracks. The script handles files without such a track as usual, in the language of the track that plays by default now |
| `--input-file FILE` | Also process the paths that `FILE` lists, one path on each line, in that order. `-` reads them from a pipe (see [A list of folders](#-a-list-of-folders)) |
| `--jobs N` | Process up to `N` files at the same time (default: `1`) |
| `--log-file PATH` | Write the details to a file. The console still shows warnings, errors, the progress bar, and the summary |
| `--ext EXT1,EXT2` | The file extensions to process (default: `mkv,webm,mp4,m4v,mov,avi`). This list replaces the default list, so include each extension that you want |
| `--no-recursive` | Do not search subfolders |
| `--skip-symlinks` | Skip the files that are symlinks (links to another file). By default, the script fixes the file that the link points to, and the link continues to work |
| `--follow-symlinks` | Also search the subfolders that are symlinks. By default, the script skips them. It always searches the folders that you name on the command line |
| `--avi-reorder` | For `.avi` files, move the stereo track to the front (see [AVI files](how-it-works.md#-changing-the-file)) |
| `--force` | Also remux (copy the tracks into a new file) the files that are already correct, for example to restore their thumbnails (see [Troubleshooting](troubleshooting.md)) |
| `--delete-leftovers` | Delete the temporary files and folders that an interrupted run left behind, when nothing in them changed in the last hour. The summary shows how many. With `--dry-run`, only list them (see [Leftover temp files](how-it-works.md#-leftover-temp-files)) |
| `--keep-dates` | Give each changed file the modification date of the original, so that the file does not look newly changed (see [File dates](how-it-works.md#-file-dates)) |
| `--no-progress` | Hide the progress bars. The script already hides them when the output is not a terminal (cron, `docker run` without `-t`, or a pipe) |

> [!TIP]
> The speed of your disks limits the work, not the CPU. So choose a value for `--jobs` that your disks can support. Do not use the number of CPU cores. With a value above `1`, you see only the overall progress bar. The lines from different files appear as they happen. When a line of one file follows a line of another file, the script prints the `[i/N]` header of the file again. The header shows which file the line is for.

For the full help, run `python3 set_stereo_default.py --help`.

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

The top bar shows the progress of the file that the script remuxes now. The bottom bar shows the progress of the full batch. The count of the batch increases during each file, not only when the file is done. For this reason, it shows fractions such as `11.45/32`.

</details>

<details>
<summary>Several files at the same time (--jobs 5)</summary>

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

With several jobs, the files can finish in any order, so the `[i/N]` numbers are not in sequence.

</details>

<details>
<summary>Dry run (--dry-run)</summary>

```text
$ python3 set_stereo_default.py "/path/to/videos/TV Shows/Awesome Show (2026)/Season 04" --dry-run
Found 8 file(s) (dry run).

[1/8] /path/to/videos/TV Shows/Awesome Show (2026)/Season 04/Awesome Show (2026) - S04E01 - Episode 25.mkv
  Awesome Show (2026) - S04E01 - Episode 25.mkv: setting stream#1 (eng, aac) as default audio
    [dry-run] mkvmerge --gui-mode --output-charset UTF-8 -o '.../set_stereo_default.tmp.XXXXXXXX/S04E01 - Episode 25.mkv' --track-order 0:0,0:1,0:2,0:3,0:4 --default-track 1:yes --default-track 4:no '.../S04E01 - Episode 25.mkv'

...

----- Summary (Dry run, nothing was changed) -----
Would change: 8
Unchanged: 0
Skipped: 0
Error: 0
```

Each `[dry-run]` line shows the command that the script runs without `--dry-run`. The real run writes the new file in a temporary folder with a random name. The command shows this name as `set_stereo_default.tmp.XXXXXXXX`. To run the command yourself, change the path after `-o` to a new file. Do not make a folder with the name `set_stereo_default.tmp.XXXXXXXX` for it, because the next run shows a warning for that folder. The command has quotes, so you can paste it into a shell. On Windows, the quotes work in Command Prompt and in PowerShell. Two types of file names are exceptions. In PowerShell, a name with `$` or a backtick (`` ` ``) does not paste correctly. In Command Prompt, a name with a word between two `%` signs, for example `%NAME%`, does not paste correctly.

</details>

## 🚦 Exit codes

| Code | Meaning |
| --- | --- |
| `0` | The script finished with no errors, or you chose `q` at the [backup question](how-it-works.md#-backups) |
| `1` | The script found no matching files, a required tool is missing, or at least one file ended as an `Error` |
| `2` | The command-line options are not valid |
| `75` | Another run of the script already works, so this run did not change files (see [One run at a time](how-it-works.md#-one-run-at-a-time)) |
| `130` | Ctrl+C stopped the script |
| `143` | SIGTERM stopped the script |

If you run the script from cron or from another script, the exit code tells you whether a problem occurred. The summary that the script prints gives the details.
