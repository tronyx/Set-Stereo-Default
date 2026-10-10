# 🐳 Docker

[← All documentation](README.md)

The image contains the script, Python, ffmpeg, MKVToolNix, and tqdm. So you do not have to install other software. The image runs on `amd64` and on `arm64`, for example a Raspberry Pi 4 or 5, or a NAS with an ARM processor. You can get the image from two registries (servers that store container images):

| Registry | Image |
| --- | --- |
| Docker Hub | `tronyx/set-stereo-default` |
| GitHub | `ghcr.io/tronyx/set-stereo-default` |

The `latest` image comes from the `master` branch, and the `develop` image comes from the `develop` branch. Each week, a new build of both images gets the new security fixes for Alpine, the Linux system in the image. The weekly build makes `develop` first. It makes `latest` only after `develop` passes all the tests.

Before the build publishes an image, [Trivy](https://trivy.dev) scans it for known vulnerabilities that have a fix. If the image has a critical vulnerability, the build does not publish it. Trivy also scans both images again in the middle of each week. So if a vulnerability becomes known after a build, this scan finds it before the next weekly build.

## ▶️ Running it

```bash
# 1. See what would change, without touching anything
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --dry-run

# 2. Try it for real on one folder, keeping backups
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default "/videos/Some Show" --backup

# 3. Happy? Run it on everything, 3 files at a time
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --jobs 3
```

Docker gives all the text after the image name to the script. So each [option](usage.md#%EF%B8%8F-all-options) works the same way as without Docker. If you do not name other paths, such as `"/videos/Some Show"` in the second command, the script processes `/videos`.

If you write nothing after the image name, the image shows the built-in help and does not change files. So a `docker run` command with only the image name cannot change your files by accident. To fix all files without other options, name the folder: `docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default /videos`.

## 🔧 The `docker run` options

- `-v "/path/to/videos:/videos"` lets the container see your videos. The part before the `:` is the folder on your computer, and `/videos` is its path inside the container. The container can see only the folders that you mount (make available with `-v`). The script uses the paths inside the container, so the output shows `/videos/...`, not your real paths. Do not mount the folder read-only (`:ro`), because the script must replace the files.
- `-it` gives the script a terminal. With a terminal, the script shows the progress bars and can ask the [backup question](how-it-works.md#-backups). For cron and other scheduled runs, do not use `-it`. Then the script hides the bars and gives a number to each new backup.
- `--rm` removes the container when the run is done.

## 📂 Several folders

Mount each folder under `/videos`. The script then processes all of them:

```bash
docker run --rm -it -v "/mnt/media/Movies:/videos/Movies" -v "/mnt/media/TV Shows:/videos/TV Shows" tronyx/set-stereo-default --dry-run
```

## 📃 A list of folders

Put the [list](usage.md#-a-list-of-folders) in the folder that you mount, with paths relative to that folder. Then give the path of the list inside the container:

```bash
docker run --rm -it -v "/path/to/videos:/videos" tronyx/set-stereo-default --input-file /videos/shows.txt --dry-run
```

If the list has paths of your computer, like `/mnt/data/media/...`, mount that folder at the same path in the container: `-v "/mnt/data/media:/mnt/data/media"`. To send the list through a pipe (the output of one command sent to another), use `-i` instead of `-it`.

## 👤 Running it as the owner of the files

By default, the container runs as root (the administrator user). On a local disk, root can give each changed file the owner of the original file. On an NFS share (a network folder), root usually becomes the user `nobody` and cannot change owners (see [Permissions and ownership](how-it-works.md#-permissions-and-ownership)).

In this case, run the container as the user that owns your videos. `id -u` and `id -g` show your user ID and group ID. `stat -c '%u:%g' "/path/to/a/video.mkv"` shows the user ID and group ID of a file. Give these IDs with `--user`:

```bash
docker run --rm -it --user 1000:100 -v "/path/to/videos:/videos" tronyx/set-stereo-default /videos
```

## 📝 Keeping the log

The files that the script writes inside the container are gone when the container stops. So write the log to the mounted folder with `--log-file`. If the script cannot give some changed files their owner, it also saves the list of these files next to the log:

```bash
docker run --rm -v "/path/to/videos:/videos" tronyx/set-stereo-default --log-file /videos/set_stereo_default.log
```

## ⏰ Running it on a schedule

This cron line runs the script every night at 3 AM, as the owner of the files and without a terminal:

```text
0 3 * * * docker run --rm --user 1000:100 -v "/path/to/videos:/videos" tronyx/set-stereo-default --log-file /videos/set_stereo_default.log
```

## 💡 Good to know

- To get the newest image, run `docker pull tronyx/set-stereo-default`.
- Both `docker stop` and Ctrl+C stop the script safely, as they do without Docker.
- If you forget the `-v` option, the script shows `Nothing is mounted at /videos` and the `-v` option to add. Then it stops with exit code `1` and does not change files.
- Inside the container, the script follows each symlink (a link to another file) to the path that it contains. That path must also exist in the container. A relative link to a file in the mounted folder works. A link to a location outside the mounted folder works only if you also mount that location, at the same path. The same is true for a link with an absolute path from your computer, such as `/mnt/media/...`.
- With Docker Desktop on Windows and macOS, the path before the `:` is a path on your computer, for example `-v "D:\Videos:/videos"`.
- To build the image yourself, run `docker build -t set-stereo-default .` in this repository. Then use `set-stereo-default` as the image name.
