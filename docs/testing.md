# 🧪 How it is tested

[← All documentation](README.md)

To run the tests yourself, see [Making a change](../.github/CONTRIBUTING.md#%EF%B8%8F-making-a-change) in the contributing guide.

## 🧾 The tests

There are four sets of tests. Each set has its own file in the `tests` folder.

### 🧠 Logic tests

The logic tests are in [tests/test_set_stereo_default.py](../tests/test_set_stereo_default.py). They test the decisions of the script itself: how it picks the track, finds files, makes backups, tests a remux, reports progress, and stops safely. A remux is a copy of the tracks into a new file. Instead of ffmpeg and mkvmerge, they use stand-ins (small pieces of Python code that act like the tools). So the logic tests run on any computer.

### 🎲 Property tests

The property tests are in [tests/test_properties.py](../tests/test_properties.py). A property is a rule that must be true for all inputs. These tests use [Hypothesis](https://hypothesis.readthedocs.io) to give random inputs to the decisions that do not need real files:

- Picking the track: never a commentary track, never another language, and always the same track, in any order of the tracks.
- The test of a remux: a remux that kept everything passes, and a remux that lost something fails. The test accepts the additions that a tool can make.
- Language tags.
- `--input-file` lines, with all types of quotes.
- The search for files.

Each property tries 150 examples in each run, and 1,500 examples in the weekly run. When an example fails, Hypothesis shrinks it to the smallest input that still fails. If the `hypothesis` package is not installed, pytest skips these tests.

### 🎬 Real-file tests

The real-file tests are in [tests/test_real_files.py](../tests/test_real_files.py). They use ffmpeg to make small MKV, MP4, WebM, and AVI files for each case that the script handles. Then they run the script on these files and make sure that the results are correct. Some files also have subtitles, chapters, track names, a font attachment, or cover art. Other files use each common audio codec: AAC, AC3, E-AC3, DTS, TrueHD, FLAC, and Opus. All of this must stay unchanged after the remux.

Each run also tries 30 random layouts, and GitHub tries 300 layouts every week. A layout has up to four audio tracks with random channels, codecs, languages, names, and flags, in random order. Some layouts also have subtitles and a font. The tests compare the result of the script with their own version of the [Picking the track](how-it-works.md#-picking-the-track) rules. This version does not use code from the script.

The tests also stop the script at 10 random moments of a run. The weekly run uses 100 moments. On Linux and macOS, the tests send Ctrl+C or SIGTERM. On Windows, they press Ctrl+C in a hidden console of the script's own, as the next paragraph tells. The tests use a random `--jobs` value, and sometimes `--backup`. They make sure that the script stops safely, and that each file is unchanged or correctly fixed. They also make sure that a second run completes the work.

On Windows, Ctrl+C goes to every program in the console. So a test gives the script a hidden console of its own, starts it with `--jobs 3`, and sends Ctrl+C there while three remuxes run. Each file must count as `Cancelled` and stay unchanged, and no temporary folder must stay. On Windows, Python 3.13 and older notice the Ctrl+C later than newer versions. Only with these versions can the tests find a stop that the script records too late. So GitHub runs the script in these tests on Windows with Python 3.10. To do the same, set `SSD_CTRL_C_PYTHON` to the path of an older Python.

The layouts and the moments come from a seed (a start value for the random numbers). The ID of each test shows its seed. To run a failure again, set `SSD_FUZZ_SEED=<seed>`. To set the number of tries, set `SSD_FUZZ_CASES=<count>`.

When the tests do not run as root, they also try a folder without write permission and a file without read permission. In the Docker image, GitHub runs the tests as `nobody`. There, the tests also try a disk of 2 MB that is too small for the remux. To try the full disk on a different system, set `SSD_FULL_DISK` to a folder on a small file system. In each of these cases, the script must report the problem, leave the file as it was, and leave no files behind.

The tests also stop a run with `kill -9` during a remux. This acts like a reboot or the OOM killer (the Linux feature that stops programs when the memory is full). Sometimes the tests stop only the script, and sometimes they also stop the remux. The next run must report the leftover temporary folder, remux the original again instead of using the leftover file, and leave no new files behind.

These tests need ffmpeg, ffprobe, and mkvmerge on your `PATH`. A few tests also need an ffmpeg that can write Opus audio and VP8 video, and most builds of ffmpeg can. If these tools are not installed, pytest skips the tests. To make the tests fail when a tool is missing, set `REQUIRE_MEDIA_TOOLS=1`. GitHub does this.

### 📝 Docs tests

The docs tests are in [tests/test_docs.py](../tests/test_docs.py). They test the links in the README, in these pages, in the changelog, and in the files in `.github`. Each link must go to a file that exists. If a link goes to a heading, the heading must exist. The tests make the anchor of each heading (the part of the link after `#`) in the same way as GitHub.

The tests also compare the links to the files of this repository on GitHub with the files here. Examples are the links in the issue forms and in the `--help` of the script. So if you move or rename a page, or change a heading, the tests find each link that stops working.

## 🤖 What GitHub runs

GitHub runs the tests on each push and each pull request, and also once a week. So if a new ffmpeg release causes a problem, the weekly run finds it (see [.github/workflows/tests.yml](../.github/workflows/tests.yml)). The logic tests run on the oldest and the newest Python versions that the script supports. The real-file tests run on Linux with each ffmpeg and mkvmerge version that [Requirements](installation.md#-requirements) lists. Both sets of tests also run on Windows and macOS, with the newest ffmpeg and MKVToolNix.

Another job lints the code (examines it for errors and style problems) with `python -m ruff check .`. It also makes sure that the type hints are correct with `python -m mypy`, for Linux and for Windows. Then it lints the workflow, the `Dockerfile`, and the Markdown files.

The Docker job builds the Docker image on `amd64` and on `arm64`. It runs the logic tests and the real-file tests in the image, with the tools of the image. Then it runs the image in the ways that people will use it: as a normal user, and as root. As root, it makes sure that each file keeps its owner and permissions. It also stops the image with `docker stop` during a remux.

## 📈 Coverage

Coverage shows which lines of the script the logic tests run. To see the coverage, run the tests with the `coverage` tool:

```bash
python -m coverage run -m pytest
python -m coverage report
```

GitHub does the same on Linux, macOS, and Windows on each run. Then it combines the three results, because some code of the script runs on only one system, for example the handler for Ctrl+C on Windows. GitHub shows the combined result on the summary page of the run. The result is for information only, and it never makes a build fail. The coverage badge in the [README](../README.md) shows the total for the newest push to `master`.

Coverage counts only the code that runs in the test process. The logic tests reach almost every line. They run the code of the script that starts ffmpeg and mkvmerge, but with the small Python stand-ins instead of the tools. The real-file tests run the script as a separate program, so they do not add to the number.
