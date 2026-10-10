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

Folders are searched recursively. You can also pass individual files, or a mix of files and folders. They're processed in the order you give them, each folder's files in alphabetical order, so you can put the shows you care about most first. A path that doesn't exist, such as a mistyped one, is reported (`Skipping /path/to/vidoes: no such file or directory`) and the rest still run.

On Windows, type `py` instead of `python3`.

## 📃 A list of folders

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

To pipe a list in instead, use `--input-file -`, e.g. `ls -d "/mnt/data/media/Videos/TV Shows/B"* | python3 set_stereo_default.py --input-file - --dry-run`. The [backup question](how-it-works.md#-backups) can't be asked then, so new backups are numbered.

## ⚙️ All options

| Option | What it does |
| --- | --- |
| `--dry-run` | Show what would change without touching any files |
| `--backup` | Keep each original as `<name>.bak`. Each backup takes as much space as the original (see [Disk space](installation.md#-disk-space)) |
| `--existing-backups MODE` | With `--backup`, what to do when `<name>.bak` already exists: `replace` it, or `number` the new one (`.bak.1`, `.bak.2`, ...). Without this you're asked once (see [Backups](how-it-works.md#-backups)) |
| `--prefer-lang LANG` | Language to use whenever a file has a stereo track in it, as a 2- or 3-letter code (`en`, `eng`, `de`, `ger` and `deu` all work). Also picks between several stereo tracks. Files without one are handled as usual, in the language of the track that plays by default now |
| `--input-file FILE` | Also process the paths listed in `FILE`, one per line, in order; `-` reads them from a pipe (see [A list of folders](#-a-list-of-folders)) |
| `--jobs N` | Work on up to `N` files at once (default: `1`) |
| `--log-file PATH` | Write the details to a file. Warnings, errors, the progress bar and the summary still show on the console |
| `--ext EXT1,EXT2` | Extensions to process (default: `mkv,webm,mp4,m4v,mov,avi`). This replaces the default list, so list every extension you want |
| `--no-recursive` | Don't look inside subfolders |
| `--skip-symlinks` | Skip symlinked files. By default, the file a link points to is fixed and the link keeps working |
| `--follow-symlinks` | Also look inside symlinked subfolders. By default they're skipped (folders you name on the command line are always searched) |
| `--avi-reorder` | For `.avi` files, move the stereo track to the front (see [AVI files](how-it-works.md#-changing-the-file)) |
| `--force` | Remux even files that are already correct, e.g. to restore thumbnails (see [Troubleshooting](troubleshooting.md)) |
| `--keep-dates` | Give each changed file the original's modification date, so it doesn't look newly changed (see [File dates](how-it-works.md#-file-dates)) |
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

## 🚦 Exit codes

| Code | Meaning |
| --- | --- |
| `0` | Finished with no errors, or you chose **q** at the [backup question](how-it-works.md#-backups) |
| `1` | No matching files found, a required tool is missing, or at least one file ended up as an `Error` |
| `2` | Invalid command-line options |
| `130` | Stopped with Ctrl+C |
| `143` | Stopped with SIGTERM |

If you're running this from cron or another script, the exit code tells you whether anything went wrong. The printed summary has the details.
