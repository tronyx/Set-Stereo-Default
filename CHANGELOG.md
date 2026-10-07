# 📜 Changelog

The notable changes to set_stereo_default, newest first.

The project doesn't use version numbers. Each entry is one merge into `master`, which always has the latest release, and links to its pull request for the full details. Changes waiting on `develop` are listed under **Unreleased**.

## Unreleased

### Fixed

- The Docker image only got Alpine's security fixes for the packages it adds (ffmpeg, MKVToolNix, Python), not for the ones the `alpine` base image itself comes with, such as zlib, musl and OpenSSL: those waited for Alpine to publish a new base image, however often the image was rebuilt. Each build now upgrades them too. Trivy's first scan found one such fix waiting, in zlib.
- With `--backup --existing-backups replace`, a run stopped just after making a file's backup, then run again, left a stray `<name>.bak.tmp_remux.<ext>` next to the file, which later runs reported as a leftover temp file. The stopped run's `.bak` was a hard link to the untouched file, and renaming the new backup over another link to the same file does nothing, but succeeds. The new backup's temp name is now always removed. The backup itself was always a true copy of the original. Found by the random-stop tests on macOS.

### Project

- ruff also checks pycodestyle's rules now, including its whitespace checks, which need ruff's preview mode, and a line length of 120. A missing space around `=` had slipped into the property tests unnoticed. Preview mode's other rules found a few small things, now fixed: docstrings that show a backslash are raw strings, and `file_context()` is typed as the generator it is.
- The weekly run is ready for `develop` as the default branch, where GitHub runs it: it tests and rebuilds `develop`, and once every job has passed, starts the same run on `master`, which rebuilds `latest`. So both Docker images get Alpine's security fixes every week, `develop` first, and nothing is ever pushed to `master`.
- Each Docker image is now scanned with Trivy before it's published, on each architecture. Every known vulnerability with a fix is listed in the run's log and, except for pull requests, in the repository's Security tab; a critical one fails the run, so that image isn't published. Trivy is pinned by digest, like actionlint, and a Trivy outage only skips the scan.

## 2026-10-07 · [#14](https://github.com/tronyx/Set-Stereo-Default/pull/14)

### Fixed

- MP4, M4V and MOV files with chapters often couldn't be fixed. ffmpeg writes a new chapter track from the file's chapters on every remux, and the script copied the original's too. In an MP4 whose video has B-frames and whose audio is AAC, as most web releases' do, the copy came through as an extra data stream, so the remux was rejected with `post-remux check failed (stream count changed from 5 to 6)`; in any M4V or MOV, ffmpeg refused it (`ffmpeg remux failed: ... Tag text incompatible with output codec id`). The original's chapter track is now left out, and the post-remux check takes the new one in its place, as long as there's still exactly one. The files were left untouched before, so nothing was lost; run the script again to fix them. A new real-file test remuxes each of the three with chapters, B-frames and AAC.

### Project

- The random tests now cover chapters too. The property tests give MP4, M4V and MOV files a chapter track anywhere after the video, check that a remux with ffmpeg's new one in its place passes and that one gone, copied twice or changed into another stream is rejected, and check that ffmpeg's command leaves out exactly the chapter tracks. The random real-file layouts are now made in M4V and MOV as well as MKV and MP4, are 2 seconds long, and sometimes have chapters, B-frames, or only AAC audio; run against the script from before the fix, they find both ways it failed.

## 2026-10-06 · [#13](https://github.com/tronyx/Set-Stereo-Default/pull/13)

### Fixed

- Ctrl+C or SIGTERM arriving once every file was done, while the summary was being printed, or in the moment between the search and the first file, ended the run with a Python traceback instead of the summary. A stop is now ignored once the files are done, since only the summary is left, and one that lands anywhere else in the run is reported like any other stop. The script also puts back the signal handlers it found when it returns, for programs that call it from Python.
- A stop that arrived while Python was inside a finalizer (a finished tool's process object being collected, which can happen between any two steps) was lost: Python drops an exception raised there, so the run carried on, quietly cancelled the files that hadn't started, and ended with exit code 0 and a summary that didn't mention them, plus an `Exception ignored ... Stopped` traceback. The stop request is now checked after each phase of the run, so a lost one is still acted on, with the partial summary and exit code of any other stop, and the dropped exception is kept quiet. Found by the random-stop tests on macOS.
- On Windows, a file another program had open (a media player, or Plex, Jellyfin or Emby scanning it) couldn't be replaced by its remux, which was right, but it was reported as an `unexpected error` with a raw `[WinError 5]`, and with `--backup` a `.bak` of the untouched file was left behind. It's now reported as `couldn't swap the new file in (Access is denied), so it's left as it was -- is it read-only, or open in another program?`, and a backup made for a swap that didn't happen is removed again.
- A file whose name is at the file system's limit (255 characters), so the temp name doesn't fit, was reported as an `unexpected error` (`Filename too long`, or on Windows the puzzling `The filename, directory name, or volume label syntax is incorrect`). It's now reported as `can't use the temp name ...; the name may be too long for this file system`.
- A language tag with a space before its region separator (`en -US`) compared as `en` plus a trailing space rather than as English. The part before the separator is now trimmed too.
- After an `--avi-reorder`, a target whose language tag was `und` and whose remux reported a real language, or none, was rejected with `target audio track didn't end up first`. Languages are now compared there the way they are everywhere else: only when both are known.

### Project

- The real-file tests also try random track layouts, 30 per job on every push and 300 on the weekly run: an MKV or MP4 with up to four audio tracks of random channels, codecs, languages, names and flags, in random order, with or without subtitles and a font attachment. What the script does with each is checked against a separate restatement of the README's track-picking rules, and the file must come through with only the default flags changed, or byte for byte as it was. Each test's ID shows the seed, so `SSD_FUZZ_SEED=<seed>` replays a failure and `SSD_FUZZ_CASES=<count>` tries more.
- On Linux and macOS, the real-file tests also stop the script with Ctrl+C or SIGTERM at random moments of a run over six files, 10 times per job on every push and 100 on the weekly run: sent the way a terminal does (to the script and its remux alike) or the way `docker stop` and `kill` do (to the script alone), with a random `--jobs` and sometimes `--backup`. The script must exit with the signal's code and a partial summary, or with 0 if it had finished, never with a traceback; leave no temp file; leave every file either as it was or properly fixed, and every backup a copy of the original; and a second run must finish the job. This is what found the stop windows fixed above.
- The real-file tests also cover Windows' edges: a path longer than its 260-character limit (fixed, with long paths enabled), and a file another program holds open for reading or exclusively, with and without `--backup` (reported, left alone, the rest of the folder still fixed); and on every platform a name at the 255-character limit. These found the two messages fixed above.
- The real-file tests also try a folder the user can't write to and a file they can't read (run as a user who isn't root), and a 2 MB disk the remux doesn't fit on (in the Docker image, where the tests now run as `nobody`, like `docker run --user`, with a tmpfs standing in for the full disk): each is reported with the tool's own error, the file left as it was, no temp file left behind, and the full disk's space given back.
- Property-based tests with [Hypothesis](https://hypothesis.readthedocs.io) (`tests/test_properties.py`; a new entry in `requirements-dev.txt`, and skipped without it) try random inputs on the decisions that don't need real files: picking the track, the post-remux check, language tags, `--input-file` lines and the search for files, 150 examples per property on every push and 1,500 on the weekly run. They found the two language fixes above.
- The real-file tests also kill a run outright partway through a remux, with and without `--backup`: the whole process tree, as a reboot or the OOM killer would (Linux and macOS), or the script alone, as `kill -9` or Task Manager's End task does, leaving the remux to finish or die on its own. The original must be untouched, and the next run must report the leftover temp file, remux the original afresh rather than trust it, and leave nothing behind.
- The logic tests also run on the next Python's pre-release (3.15 for now), as a heads-up: that job may fail without failing the run.

## 2026-10-05 · [#12](https://github.com/tronyx/Set-Stereo-Default/pull/12)

### Changed

- A missing ffmpeg or ffprobe is reported before the search for files rather than after it, which on a large library or a network share could take minutes. mkvmerge is still checked only once the search shows there are MKV files to process.
- With `--backup`, the check for existing `.bak` files before the run lists each folder once instead of checking every file on its own, which on a network share was one round trip per file.

### Fixed

- A symlink that someone had put at a file's temp name (`<name>.tmp_remux.<ext>`), or at a backup's staging name, was written through by mkvmerge, ffmpeg or the backup copy, and the link was then renamed over the original or into `.bak`, so the video itself became a symlink to wherever the link pointed. Whatever has the temp name is now removed before the remux, a remux that isn't a regular file is never swapped in, and a backup copy only ever writes a new file.
- A file that another program replaced, edited or removed while it was being remuxed (e.g. Sonarr or Radarr importing an upgrade) was overwritten by the remux of the old version, silently losing the change. The script now checks that the file is unchanged before making its backup and again right before swapping the remux in (without hard links the backup is a full copy, which can take minutes on a share, so a change during it has to be caught too); if it changed, the file is left as it is now, any backup just made of the superseded version is removed, the file is reported as `changed by another program during the remux` and counted as an `Error`, and the next run fixes the new version.
- Run on a relative path such as `.`, files whose names start with `-` or `@`, or contain a colon (`Movie: Part 2.mp4`), failed with errors like `Missing argument for option` or `Protocol not found`, because ffprobe, ffmpeg and mkvmerge read the bare name as an option or a web address. Every path is now made absolute before it's used, so these files are fixed like any other. File headers show the full path as a result.
- With `--backup` on storage without hard links (exFAT, some network shares), where the backup is a full copy, a copy that failed partway (a full disk, a dropped share) left a partial `.bak` that looked complete, and with `--existing-backups replace` had already deleted the old one. Backups are now made under a temporary name and only renamed to `.bak` once complete, so a failed copy leaves the old backup, and the original, as they were.
- The list of files that couldn't keep their owner could overwrite another list made in the same second, and, when written to a shared temp folder by a run as root, follow a symlink someone had planted at its name and overwrite the file it pointed to. The list is now always created as a new file, never over or through anything already at that name, and its name includes the process ID (`set_stereo_default-owners-<date>-<time>-<process ID>.log`).
- On Linux, a file whose name isn't valid UTF-8 (old libraries can hold Latin-1 names) was fixed, but on most desktop locales each of its lines became a `--- Logging error ---` traceback on the console and was missing from `--log-file`. Such names are now written with the odd bytes as escapes, e.g. `caf\xe9.mkv`.
- `--log-file` naming a folder that doesn't exist ended in a Python traceback instead of an error message. It's now reported like any other invalid option, with exit code 2, as is a log file that can't be written for another reason, such as permissions.
- An MP4, MOV or AVI remux that lost a flag ffmpeg can't write (e.g. "commentary") and then failed a later check got both the `lost its ... flag` warning, which means the file was fixed, and the `post-remux check failed` error saying it was left untouched. The warning now only appears when the file really was fixed.

### Project

- The check before a remux replaces the original is split into one small function per rule, with no change in behavior, so it's easier to read and to change safely. Its complexity score went from E (36) to B (9).
- That check returns its result as one `Verification` value, the problem (if any) and the warnings to log if the remux is used, instead of filling in a list passed to it, so a caller can't lose the warnings by mistake. No change in behavior.
- Whether a file is an AVI reorder, and which stream is the target, are decided once and kept on the file's `Plan`, instead of being worked out again in each function that needs them. No change in behavior.
- The summary's counts are kept in a `Counter`, which reads as zero for an outcome that never happened, instead of a dictionary seeded with every outcome by hand. No change in behavior.
- mypy checks the script in strict mode, now that its last loose type hints (an untyped `dict`, and two lookups that returned a value of unknown type) are fixed. No change in behavior.
- Every third-party GitHub Action is pinned to a commit instead of a version tag, which its owner could move to other code, and the actionlint image to its digest. The Docker actions get the Docker Hub token and push the published image, so this keeps a compromised or moved tag from reaching either. Dependabot still proposes updates, moving each pin and its version comment together.
- The command-line options are passed around as one typed, read-only `Options` object instead of argparse's untyped one, with no change in behavior. mypy now checks every option the script reads, so a misspelled option name or a wrong type fails the checks instead of slipping through, and the backup setting no longer changes from yes/no to a mode partway through a run.
- `main()` can be called more than once in the same Python process. A second call used to print every line twice, report the first run's ownership problems again, and, after a stopped run, cancel every file. Each run now starts clean. It must be called from the main thread, which Python requires for installing the Ctrl+C and SIGTERM handlers, and one run at a time; its docstring says so.
- Removed an unused `position` setting from `process_file()` and `Progress`: nothing ever set it, so the file's progress bar was always on the top row, where it's now drawn directly.
- What happened to each file (`changed`, `unchanged`, `skipped`, `error`, `cancelled`) has its own `Outcome` type instead of being any string, so mypy rejects a misspelled one, which would otherwise have been counted somewhere the summary never shows.
- The README lists mkvmerge 45, the oldest version the tests run against, as the minimum instead of "any"; shows the lint and type-check commands next to the test command; and no longer says the coverage report misses the lines that run ffmpeg and mkvmerge, which the logic tests now reach.
- The README's dry-run sample shows the `--track-order` option mkvmerge is now given, and its Troubleshooting section explains `changed by another program during the remux`; `--help` lists that check under "Safe by default".

## 2026-10-04 · [#11](https://github.com/tronyx/Set-Stereo-Default/pull/11)

### Added

- **`--input-file FILE`** processes the paths listed in a file, one per line, in the list's order; `--input-file -` reads them from a pipe. Each line can be a plain path, a path quoted the way a shell or `ls` shows it (including `'\''` for an apostrophe and backslash-escaped spaces), a path relative to the list's folder, or one starting with `~`. Blank lines and `#` comments are skipped. In the Docker image, a list replaces the `/videos` default.

### Changed

- Paths are processed in the order you give them, each folder's files in alphabetical order, instead of every file from every path being sorted together. A file reached through more than one path is still processed once.

### Fixed

- MKV files with a subtitle between two audio tracks (common in some releases) were rejected with `post-remux check failed (stream#2 changed from subtitle subrip to audio ...)` and left unfixed. mkvmerge writes video, then audio, then subtitles unless told otherwise, so it moved the subtitle to the end. The script now tells it to keep every track where it was.

### Project

- The static-build and Windows CI jobs find the newest ffmpeg build however BtbN, which publishes those builds, names and releases them. A brief change there had stopped both jobs from finding one.

## 2026-10-04 · [#10](https://github.com/tronyx/Set-Stereo-Default/pull/10)

### Changed

- **In the Docker image, the path defaults to `/videos`**, where the image expects your videos to be mounted, so `docker run -v "/path/to/videos:/videos" tronyx/set-stereo-default --dry-run` is enough. Naming paths still works as before. With nothing at all after the image name, the image still shows the help, so a bare `docker run` never changes files. Outside Docker, a path is still required.
- In the Docker image, a forgotten `-v` now says `Nothing is mounted at /videos` and shows the `-v` to add.

## 2026-10-04 · [#9](https://github.com/tronyx/Set-Stereo-Default/pull/9)

### Added

- **A Docker image** with the script, ffmpeg, MKVToolNix and tqdm, for amd64 and arm64, built on Alpine (about 310 MB). It's published as `tronyx/set-stereo-default` on Docker Hub and as `ghcr.io/tronyx/set-stereo-default`: `latest` from `master`, rebuilt weekly for Alpine's security fixes, and `develop` from `develop`. The README's new 🐳 Docker section covers mounting folders, running as the files' owner, keeping the log and running on a schedule.

### Changed

- Progress bars are hidden automatically when the output isn't a terminal (cron, `docker run` without `-t`, `docker logs`, a pipe), instead of filling it with cursor codes. The same goes for the `Processing i/N...` counter shown with `--log-file`.
- `--help` shows how to run the script with the Docker image, which shows the help when it's given no options.
- In the Docker image, the ownership warning suggests `docker run --user` instead of `sudo`.
- A file with no audio says `SKIP (no audio streams found)`, like every other skipped file, so searching a log for `SKIP` finds them all.

### Fixed

- **The check before a remux replaces the original now compares every stream**, not just how many there are. A changed codec or channel count, a changed language or a lost track name rejects the remux, and the original is kept.
- **mkvmerge 52 and older drop four track flags** (commentary, audio description, hearing impaired and original language) when remuxing an MKV file. A remux that lost one is now rejected; update MKVToolNix to 54 or newer to fix those files.
- ffmpeg can't write those flags to MP4, MOV or AVI files at all, so a flag lost there is now reported as a warning. It used to go unnoticed.
- Recent mkvmerge versions (99 does; 82 and older don't) changed the MIME type of attached fonts from the older `application/x-truetype-font` to `font/ttf`, which ffmpeg, and players built on it, don't recognize as a font, so styled subtitles could lose their fonts. Font types now come through a remux unchanged.

### Project

- The real-file tests check that subtitles, chapters, track names and languages, font attachments and cover art all come through a remux unchanged.
- The real-file tests cover each common audio codec (AAC, E-AC3, DTS, TrueHD, FLAC and Opus, besides AC3) in MKV, and AAC, E-AC3 and Opus in MP4 and WebM.
- The logic tests cover the last untested paths, including running without tqdm, an unreadable ffprobe result, an unexpected error in one file, and `--ext` written with spaces, dots or capitals. Coverage is up from 96% to 99%.
- CI runs the logic and real-file tests on macOS too, with Homebrew's ffmpeg and MKVToolNix.
- CI tests mkvmerge 45, the oldest the README lists, from MKVToolNix's AppImage archive.
- CI builds the Docker image on every push, for amd64 and arm64 on their own runners, and runs all the tests inside it. It then runs the image as a regular user, as root (checking each file keeps its owner and permissions), and stopped with `docker stop` partway through a remux. The image that's published is that exact tested image, tagged once every test job has passed, never a separate rebuild.
- CI lints the workflow (actionlint, with shellcheck), the `Dockerfile` (hadolint) and the Markdown files (markdownlint).

## 2026-10-03 · [#8](https://github.com/tronyx/Set-Stereo-Default/pull/8)

### Project

- Issue forms for bug reports and feature requests. Blank issues are turned off, so every report asks for the details needed to look into it.
- Dependabot proposes updates to the GitHub Actions and to the pinned ruff and mypy once a month.
- This changelog.

## 2026-10-03 · [#7](https://github.com/tronyx/Set-Stereo-Default/pull/7)

### Changed

- **`--prefer-lang` is a preference, not a requirement.** A file with a stereo track in that language uses it. A file without one is handled as if the option wasn't given, instead of being skipped, and its line says so. Several stereo tracks in the preferred language still skip the file.

## 2026-10-02 · [#6](https://github.com/tronyx/Set-Stereo-Default/pull/6)

### Changed

- `--jobs` output prints as it happens. A file's `[i/N]` header is repeated when its lines follow another file's, and in a dry run each file's lines come out together.
- When remuxed files can't get their original owner (typically when running as root on an NFS share), there's one warning just before the summary. It gives the number of files, the owner they should have and the one they got, the `sudo -u <owner>` command to use instead, and a list file with every path.
- Summary labels are capitalized: `Changed: 5`, `Summary (Dry run, nothing was changed)`.
- One ffprobe run fewer per changed file, and about 9× fewer file-system calls when searching folders, which matters most on network shares.
- The script is marked executable, so it runs as `./set_stereo_default.py` on Linux and macOS.

### Fixed

- No ownership warning on storage that already gives files the right owner.
- Ctrl+C and SIGTERM also stop a running ffprobe or `mkvmerge -J`, so stopping with `--jobs` doesn't wait on a slow share.
- The `--avi-reorder` check matches languages however they're written, like everywhere else.

### Project

- The script was refactored with no change to its output, and has type hints throughout. CI runs ruff, and mypy for Linux and Windows.

## 2026-10-02 · [#5](https://github.com/tronyx/Set-Stereo-Default/pull/5)

### Fixed

- Unattended Windows runs (Task Scheduler, or input from `NUL`) no longer quit silently at the backup question. With no one to answer, new backups are numbered.
- A folder that can't be opened gets a warning, instead of its files being silently missing from the run.
- `--existing-backups` without `--backup` is an error instead of being ignored.
- The backup question says it only applies to files that get changed.

### Project

- The real-file tests also run on Windows in CI.

## 2026-10-02 · [#4](https://github.com/tronyx/Set-Stereo-Default/pull/4)

### Added

- `--existing-backups replace|number` answers the backup question up front.
- `--skip-symlinks` leaves symlinked files alone, and `--follow-symlinks` also searches symlinked subfolders.
- `--keep-dates` gives each changed file the original's dates.
- Language codes match however they're written (`en`/`eng`, `de`/`ger`/`deu`, `pt-BR`), in files and in `--prefer-lang`. A `--prefer-lang` value that isn't a language code is rejected.

### Changed

- **Python 3.10 or newer is required** (was 3.8).
- A dry run's summary says `would change`.
- `--help` has annotated examples, the exit codes and a link to the full guide.

### Fixed

- A remux that comes out more than 1% (and at least 1 second) shorter than the original is rejected, so truncated files are left alone instead of being "fixed".
- Symlinked files are fixed through their link, instead of the link being replaced by a separate copy.
- An existing `.bak` is never overwritten without asking.
- MKV files use mkvmerge's own track IDs, which can differ from ffprobe's. If the two tools disagree on the number of audio tracks, the file is left alone.
- Tool output is read as UTF-8 everywhere, so non-English track names on Windows are no longer garbled.
- Ctrl+C or SIGTERM while looking for files stops cleanly.
- A path that doesn't exist is reported instead of skipped silently.

### Project

- Coverage badges for `master` and `develop`, and the logic tests also run on Windows.
- New README sections for backups, symlinks, file dates and troubleshooting.

## 2026-10-02 · [#3](https://github.com/tronyx/Set-Stereo-Default/pull/3)

### Project

- A markdownlint config for the README's collapsible sections.

## 2026-10-01 · [#2](https://github.com/tronyx/Set-Stereo-Default/pull/2)

### Added

- Commentary and audio-description tracks are never picked, whether the file flags them or only their name says so.
- SIGTERM (`docker stop`, `kill`, systemd) stops cleanly like Ctrl+C, and exits with 143.

### Changed

- **The stereo track must be in the language that plays by default now**, so a stereo dub never replaces the original language.
- `--prefer-lang` became a language the stereo track must be in, not only a tie-breaker. (Relaxed again in [#7](https://github.com/tronyx/Set-Stereo-Default/pull/7).)
- **ffmpeg 4.4 or newer is the documented minimum.** Older versions can't read MKV commentary flags.
- With `--log-file`, warnings and errors still show on the console.

### Fixed

- Temp files are removed even if a stop arrives during the post-remux check, and a badly timed stop can no longer hang or leave a remux running.
- mkvmerge's "finished with warnings" exit code no longer counts as a failure.
- MKV files work with mkvmerge versions older than 65.
- ffmpeg error messages no longer fill up with progress output.

### Project

- End-to-end tests on real files generated with ffmpeg, run in CI against ffmpeg 4.4, 5.1, 6.1, 7.1 and the newest release, and weekly.
- The README was reworked, and the script documents itself in docstrings only.

## 2026-09-23 · [#1](https://github.com/tronyx/Set-Stereo-Default/pull/1)

### Added

- `--jobs N` remuxes several files at once.
- Ctrl+C stops cleanly: running remuxes are killed, temp files are removed, and the script exits with 130 and a partial summary.
- Each remuxed file is checked (no lost streams, the right track is default) before it replaces the original.
- Remuxed files keep the original's permissions and owner.
- Leftover temp files from a run that was killed outright are skipped with a warning.

### Changed

- `--backup` makes a hard link instead of copying the file.
- The overall progress bar moves while each file is remuxed.
- The `--dry-run` command is quoted, so it can be pasted into a shell.

### Project

- A pytest suite, run by GitHub Actions on every push and pull request.

## 2026-09-21 · First release

- Makes the stereo track play by default, without re-encoding: `.mkv`/`.webm` with mkvmerge, `.mp4`/`.m4v`/`.mov` with ffmpeg, and `.avi` by moving the stereo track first with `--avi-reorder`.
- Options: `--ext`, `--no-recursive`, `--dry-run`, `--backup`, `--prefer-lang` (to choose between several stereo tracks), `--avi-reorder`, `--force`, `--log-file` and `--no-progress`.
