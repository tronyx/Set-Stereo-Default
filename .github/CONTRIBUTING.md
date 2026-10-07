# 🤝 Contributing

Thanks for wanting to help! Bug reports, ideas and pull requests are all welcome.

## 🐛 Reporting a bug

[Open a bug report](https://github.com/tronyx/Set-Stereo-Default/issues/new?template=bug_report.yml). The form asks for:

- The command you ran and what it printed (`--dry-run` output is ideal).
- Your operating system, and your Python, ffmpeg and mkvmerge versions (`python3 --version`, `ffmpeg -version`, `mkvmerge --version`).
- If a file was handled wrongly, its track list from `ffprobe -v error -show_streams -select_streams a -of json "<file>"`. The form shows how to get it through the Docker image if you don't have ffprobe installed.

## 💡 Suggesting a feature

[Open a feature request](https://github.com/tronyx/Set-Stereo-Default/issues/new?template=feature_request.yml) describing what you'd like to do and why. For anything bigger than a small fix, please do this before writing code, so we can agree on the approach and your time isn't wasted.

## 🛠️ Making a change

1. Fork the repository, then clone your fork:

   ```bash
   git clone https://github.com/<your_name>/Set-Stereo-Default.git
   cd Set-Stereo-Default
   ```

2. Create a branch from `develop`:

   ```bash
   git checkout -b my-new-feature develop
   ```

3. Make your change. Keep each pull request to one change.

4. Install the dev requirements, then run the tests and the checks:

   ```bash
   pip install -r requirements-dev.txt
   python -m pytest
   python -m ruff check .
   python -m mypy
   ```

   The real-file tests are skipped unless ffmpeg, ffprobe and mkvmerge are installed. GitHub runs them for you on every pull request, but it's quicker to catch problems locally. Some of them try random track layouts, or stop the script at random moments: a failing one's test ID shows the seed, and running pytest with `SSD_FUZZ_SEED=<seed>` tries the same cases again (`SSD_FUZZ_CASES=<count>` sets how many). To see which lines your tests reach, run `python -m coverage run -m pytest` and then `python -m coverage report`.

   `ruff` is a linter and `mypy` checks the type hints; both are set up in `pyproject.toml`, and GitHub runs them on every push too.

   GitHub also lints the workflow ([actionlint](https://github.com/rhysd/actionlint)), the `Dockerfile` ([hadolint](https://github.com/hadolint/hadolint), set up in `.hadolint.yaml`) and the Markdown files ([markdownlint](https://github.com/DavidAnson/markdownlint-cli2), set up in `.markdownlint.json`). If you change any of those and have Docker, you can run the same checks locally:

   ```bash
   docker run --rm -v "$PWD:/repo" -w /repo rhysd/actionlint:1.7.12
   docker run --rm -v "$PWD:/repo" -w /repo hadolint/hadolint hadolint --config .hadolint.yaml Dockerfile
   docker run --rm -v "$PWD:/workdir" davidanson/markdownlint-cli2 "**/*.md"
   ```

   If you change the `Dockerfile`, build the image and try it on a copy of a few videos. GitHub also runs every test inside it, on amd64 and arm64:

   ```bash
   docker build -t set-stereo-default .
   docker run --rm -it -v "/path/to/test/videos:/videos" set-stereo-default --dry-run
   ```

5. Commit, push, and open a pull request against `develop`:

   ```bash
   git add -A
   git commit -m "Add some feature"
   git push origin my-new-feature
   ```

## 📏 Code style

- **One file, standard library only.** People install this by copying a single script, so keep it that way: no new modules, and no new required packages. tqdm stays optional.
- **Python 3.10 and newer.** GitHub tests on 3.10, the newest release, and the next release's pre-release, which may fail without failing the run (a heads-up, not a blocker). Avoid syntax newer than 3.10, such as reusing the same quote inside an f-string (3.12+).
- **Linux, macOS and Windows.** Use `pathlib` for paths, pass commands as lists rather than through a shell, and don't assume a file system feature (hard links, symlinks, owners) is always available.
- **Docstrings, not comments.** Explain code in the docstring of the function it belongs to. Module-level settings get a docstring on the line after them. Don't add `#` comments.
- **Keep docstrings short and plain.** Say what something does and, where it isn't obvious, why.
- **Lines up to 120 characters, spaced as PEP 8 asks** (spaces around operators, and the like). `ruff` checks both.
- **Type hints on every function.** `mypy` checks them in strict mode, so they stay accurate and complete. If it objects to something, fix the code rather than silencing it: a `# type: ignore` is a comment, which the rule above rules out.
- **Add or update tests** for any change in behavior. Logic tests go in `tests/test_set_stereo_default.py`. A rule that must hold for every input, not just a few chosen examples, can be a property in `tests/test_properties.py` (Hypothesis). Anything that depends on how ffmpeg or mkvmerge really behave goes in `tests/test_real_files.py`.
- **Update the README** if you add an option or change what the script does.
- **Add a line to the changelog** under **Unreleased** in `CHANGELOG.md`, saying what changes for someone using the script.
