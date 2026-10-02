# 🤝 Contributing

Thanks for wanting to help! Bug reports, ideas and pull requests are all welcome.

## 🐛 Reporting a bug

[Open an issue](https://github.com/tronyx/Set-Stereo-Default/issues) and include:

- the command you ran and what it printed (`--dry-run` output is ideal)
- your ffmpeg and mkvmerge versions (`ffmpeg -version`, `mkvmerge --version`)
- if a file was handled wrongly, its track list from `ffprobe -v error -show_streams -select_streams a -of json "<file>"`

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

4. Install the test requirements and run the tests:

   ```bash
   pip install -r requirements-dev.txt
   python -m pytest
   ```

   The real-file tests are skipped unless ffmpeg, ffprobe and mkvmerge are installed. GitHub runs them for you on every pull request, but it's quicker to catch problems locally.

5. Commit, push, and open a pull request against `develop`:

   ```bash
   git commit -am "Add some feature"
   git push origin my-new-feature
   ```

## 📏 Code style

- **Docstrings, not comments.** Explain code in the docstring of the function it belongs to. Module-level settings get a docstring on the line after them. Don't add `#` comments.
- **Keep docstrings short and plain.** Say what something does and, where it isn't obvious, why.
- **Add or update tests** for any change in behavior. Logic tests go in `tests/test_set_stereo_default.py`. Anything that depends on how ffmpeg or mkvmerge really behave goes in `tests/test_real_files.py`.
- **Update the README** if you add an option or change what the script does.
