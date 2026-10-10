# 🐳 Docker

[← All documentation](README.md)

The image has the script, Python, ffmpeg, MKVToolNix and tqdm, so there's nothing else to install. It runs on `amd64` and `arm64` (e.g. a Raspberry Pi 4 or 5, or an ARM-based NAS), and is published in two places:

| Registry | Image |
| --- | --- |
| Docker Hub | `tronyx/set-stereo-default` |
| GitHub | `ghcr.io/tronyx/set-stereo-default` |

`latest` is built from `master`, and `develop` from the `develop` branch. Both are rebuilt every week for Alpine's security fixes: `develop` first, then `latest` once `develop` has passed every test. Before an image is published, it's scanned with [Trivy](https://trivy.dev) for known vulnerabilities that have a fix, and one with a critical vulnerability isn't published. Both images are scanned again midweek, so a vulnerability found after they were built is caught before the next rebuild.

```bash
# 1. See what would change, without touching anything
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --dry-run

# 2. Try it for real on one folder, keeping backups
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default "/videos/Some Show" --backup

# 3. Happy? Run it on everything, 3 files at a time
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --jobs 3
```

Everything after the image name is passed to the script, so every [option](usage.md#%EF%B8%8F-all-options) works the same way. The script works on `/videos` unless you name other paths, such as `"/videos/Some Show"` above.

With nothing at all after the image name, the image shows the built-in help instead of changing anything, so a bare `docker run` can never touch your files by accident. To fix everything with no other options, name the folder: `docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default /videos`.

**What the `docker run` options do:**

- **`-v "/path/to/videos:/videos"`** lets the container see your videos: the folder on your computer before the `:`, at `/videos` inside the container. The container can only see folders you mount, and the script works with paths inside it, so the output shows `/videos/...` rather than your real paths. Don't mount it read-only (`:ro`), since the script has to replace the files.
- **`-it`** gives the script a terminal, which shows the progress bars and lets it ask the [backup question](how-it-works.md#-backups). Leave it out for cron and other scheduled runs: the bars are hidden and new backups are numbered.
- **`--rm`** removes the container once the run finishes.

**Several folders.** Mount each one under `/videos`, and the script works on all of them:

```bash
docker run --rm -it -v "/mnt/media/Movies:/videos/Movies" -v "/mnt/media/TV Shows:/videos/TV Shows" tronyx/set-stereo-default --dry-run
```

**A list of folders.** Keep the [list](usage.md#-a-list-of-folders) in the folder you mount, with paths relative to it, and give its path inside the container:

```bash
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --input-file /videos/shows.txt --dry-run
```

A list of your computer's own paths, like `/mnt/data/media/...`, works too if you mount that folder at the same path: `-v "/mnt/data/media:/mnt/data/media"`. To pipe a list in, use `-i` instead of `-it`.

**Running as the files' owner.** The container runs as root by default, which lets it give every remuxed file its original owner on a local disk. On an NFS share, root usually becomes `nobody` and can't change owners (see [Permissions and ownership](how-it-works.md#-permissions-and-ownership)), so run the container as the user that owns your videos instead. `id -u` and `id -g` show your own IDs, and `stat -c '%u:%g' "/path/to/a/video.mkv"` shows a file's:

```bash
docker run --rm -it --user 1000:100 -v "/path/to/videos:/videos" tronyx/set-stereo-default /videos
```

**Keeping the log.** Anything written inside the container is gone when it exits, so point `--log-file` at the mounted folder. If remuxed files can't be given their owner, the list of them is saved next to the log too:

```bash
docker run --rm -v "/path/to/videos:/videos" tronyx/set-stereo-default --log-file /videos/set_stereo_default.log
```

**Running it on a schedule,** e.g. every night at 3 AM from cron, as the files' owner and with no terminal:

```text
0 3 * * * docker run --rm --user 1000:100 -v "/path/to/videos:/videos" tronyx/set-stereo-default --log-file /videos/set_stereo_default.log
```

**Good to know:**

- **Updating:** `docker pull tronyx/set-stereo-default` gets the newest image.
- **Stopping:** `docker stop` and Ctrl+C both stop the script cleanly, the same as without Docker.
- **Forgot the `-v`?** The script says `Nothing is mounted at /videos` and shows the `-v` to add, then exits with code `1` without changing anything.
- **Symlinks:** inside the container, a symlink is followed to the path it holds, and that path has to exist there too. A relative link within the mounted folder works. One that points elsewhere, or uses an absolute path from your computer (`/mnt/media/...`), only works if that path is also mounted, at the same place.
- **Docker Desktop (Windows and macOS):** the path before the `:` is one on your computer, e.g. `-v "D:\Videos:/videos"`.
- **Building it yourself:** `docker build -t set-stereo-default .` in this repository, then use `set-stereo-default` as the image name.
