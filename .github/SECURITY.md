# 🔒 Security policy

## ✅ Supported versions

The project doesn't use version numbers. Only the latest release is supported: the `master` branch, and the `latest` Docker image on Docker Hub (`tronyx/set-stereo-default`) and GitHub (`ghcr.io/tronyx/set-stereo-default`). Fixes aren't made to older copies, so update to the latest before reporting (`git pull`, or `docker pull`).

The `develop` branch and image are the next release in progress. A vulnerability there is welcome too, since fixing it early keeps it out of the next release.

## 📮 Reporting a vulnerability

**Please don't open a public issue.** Report it privately instead, through GitHub: on the [Security tab](https://github.com/tronyx/Set-Stereo-Default/security), choose **Report a vulnerability**, or go straight to the [form](https://github.com/tronyx/Set-Stereo-Default/security/advisories/new). Only you and the maintainer can see the report.

It helps to include:

- what an attacker could do, and what they'd need first (for example, write access to a shared media folder);
- how to reproduce it: the command, the files or folder layout involved, and whether you ran the script directly or in Docker;
- your operating system, and your Python, ffmpeg and mkvmerge versions, or the image's digest (`docker image inspect --format '{{.Id}}' tronyx/set-stereo-default`).

If you can't use the form, [open an issue](https://github.com/tronyx/Set-Stereo-Default/issues) asking for a private way to get in touch, with no details of the problem.

## ⏱️ What to expect

This project is maintained by one volunteer in their spare time, so please allow for that:

- You'll get a reply within **7 days** saying whether the report is accepted.
- An accepted report is fixed privately, then released, with an advisory on the [Security tab](https://github.com/tronyx/Set-Stereo-Default/security) that credits you, unless you'd rather not be named. The aim is a release within **90 days** of the report, sooner for something serious, and you'll be kept up to date along the way.
- Please keep it private until the fix is released and the advisory published.

## 🎯 Scope

The script changes files in place, often across a whole media library and sometimes as root, so these are in scope:

- **The script:** a file name, symlink, folder layout or file content that makes it write, replace or delete a file it shouldn't, change a file's owner or permissions, run a command, or reveal something it shouldn't. That includes tricks with its temp files (`<name>.tmp_remux.<ext>`) and backups by someone who can write to the media folder but not to the files themselves.
- **The Docker image:** a known vulnerability, with a fix available, in a package in the current `latest` image. The images are rebuilt every week and scanned with [Trivy](https://trivy.dev) before they're published, and again midweek, so check that the fix has been out for more than a few days.
- **The repository's workflows:** anything that could expose its secrets, or let someone else's code run with its permissions.

Out of scope:

- vulnerabilities in ffmpeg, MKVToolNix, Python or Alpine themselves; please report those to their own projects;
- anything that needs an attacker who can already run commands as the user running the script;
- the script leaving a file unfixed, or reporting an error: that's a bug, for a [bug report](https://github.com/tronyx/Set-Stereo-Default/issues/new?template=bug_report.yml).
