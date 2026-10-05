# 📜 Changelog

The notable changes to set_stereo_default, newest first.

The project doesn't use version numbers. Each entry is one merge into `master`, which always has the latest release, and links to its pull request for the full details. Changes waiting on `develop` are listed under **Unreleased**.

## Unreleased

### Fixed

- Run on a relative path such as `.`, files whose names start with `-` or `@`, or contain a colon (`Movie: Part 2.mp4`), failed with errors like `Missing argument for option` or `Protocol not found`, because ffprobe, ffmpeg and mkvmerge read the bare name as an option or a web address. Every path is now made absolute before it's used, so these files are fixed like any other. File headers show the full path as a result.

### Project

- The check before a remux replaces the original is split into one small function per rule, with no change in behavior, so it's easier to read and to change safely. Its complexity score went from E (36) to B (9).

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
