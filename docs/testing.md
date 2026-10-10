# 🧪 How it's tested

[← All documentation](README.md)

To run the tests yourself, see [Making a change](../.github/CONTRIBUTING.md#%EF%B8%8F-making-a-change) in the contributing guide.

## 🧾 The tests

There are three sets of tests:

- **Logic tests** ([tests/test_set_stereo_default.py](../tests/test_set_stereo_default.py)) cover the script's own decisions: picking the track, finding files, backups, checking a remux, progress reporting and clean stopping. They stand in for ffmpeg and mkvmerge, so they run anywhere.
- **Property tests** ([tests/test_properties.py](../tests/test_properties.py)) use [Hypothesis](https://hypothesis.readthedocs.io) to try random inputs on the decisions that don't need real files: picking the track (never commentary, never another language, and the same track whatever order the tracks come in), the post-remux check (a remux that kept everything passes, one that lost anything is rejected, and what a tool may add is tolerated), language tags, `--input-file` lines however they're quoted, and the search for files. Each property tries 150 examples per run (1,500 on the weekly run), and a failure is shrunk to the smallest input that still fails. They're skipped without the `hypothesis` package.
- **Real-file tests** ([tests/test_real_files.py](../tests/test_real_files.py)) use ffmpeg to create small MKV, MP4, WebM and AVI files for each case the script handles, run the script on them, and check the results. Some files also have subtitles, chapters, track names, a font attachment or cover art, and others use each common audio codec (AAC, AC3, E-AC3, DTS, TrueHD, FLAC and Opus); all of it must come through the remux unchanged. They also try 30 random layouts each run (up to four audio tracks with random channels, codecs, languages, names and flags, in random order, with or without subtitles and a font), checking what the script does against a separate restatement of the [Picking the track](how-it-works.md#-picking-the-track) rules; GitHub tries 300 every week. On Linux and macOS they also stop the script with Ctrl+C or SIGTERM at 10 random moments of a run (100 weekly), with random `--jobs` and sometimes `--backup`, and check that it exits cleanly, that every file is left either as it was or properly fixed, and that a second run finishes the job. The layouts and moments come from a seed that each test's ID shows, so `SSD_FUZZ_SEED=<seed>` replays a failure and `SSD_FUZZ_CASES=<count>` sets how many to try. Run as a user who isn't root, they also try a folder that user can't write to and a file they can't read, and in the Docker image, where GitHub runs them as `nobody`, a 2 MB disk the remux doesn't fit on (set `SSD_FULL_DISK` to a folder on a small file system to try that elsewhere): each is reported, the file left as it was, and nothing left behind. And they kill a run outright partway through a remux (`kill -9`, as a reboot or the OOM killer would, on its own or with the remux too): the next run must report the leftover temp file, remux the original afresh rather than trust it, and leave nothing behind. They need ffmpeg, ffprobe and mkvmerge on your `PATH` (and, for a few of them, an ffmpeg that can encode Opus and VP8, as most builds can), and are skipped if those aren't installed. Set `REQUIRE_MEDIA_TOOLS=1` to make a missing tool fail them instead, as GitHub does.

## 🤖 What GitHub runs

GitHub runs them on every push and pull request, plus once a week, so a new ffmpeg release that breaks something gets noticed (see [.github/workflows/tests.yml](../.github/workflows/tests.yml)). The logic tests run on the oldest and newest supported Python versions. The real-file tests run on Linux against every ffmpeg and mkvmerge version listed under [Requirements](installation.md#-requirements). Both also run on Windows and macOS, with the newest ffmpeg and MKVToolNix. A third job lints the code (`python -m ruff check .`) and checks its type hints (`python -m mypy`), for Linux and for Windows, then lints the workflow, the `Dockerfile` and the Markdown files. A fourth builds the Docker image on both `amd64` and `arm64`, runs both sets of tests inside it with the image's own tools, then runs the image the ways people will: as a regular user, as root (checking each file keeps its owner and permissions), and stopped with `docker stop` partway through a remux.

## 📈 Coverage

To see which lines of the script the logic tests reach, run them under coverage. GitHub does the same on every run and shows the result on the run's summary page; it's for information only and never fails a build. The coverage badge in the [README](../README.md) shows the total for the latest push to `master`:

```bash
python -m coverage run -m pytest
python -m coverage report
```

Coverage only counts code that runs inside the test process. The logic tests reach almost every line, because they drive the script's own code for running ffmpeg and mkvmerge with small Python stand-ins. The real-file tests run the script as a separate program, so they don't add to the number.
