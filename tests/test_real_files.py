"""End-to-end tests: generate small real videos with ffmpeg, run the script
on them as a user would, and check the results with ffprobe and mkvmerge.

Unlike test_set_stereo_default.py, these need ffmpeg/ffprobe on PATH (and
mkvmerge for the .mkv cases). Without them the tests are skipped, unless
REQUIRE_MEDIA_TOOLS is set -- as it is in CI -- in which case a missing tool
fails the run instead of quietly skipping everything."""

import hashlib
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "set_stereo_default.py"
LAYOUTS = {2: "stereo", 6: "5.1"}


def need(*tools):
    """Skip (or, with REQUIRE_MEDIA_TOOLS set, fail) unless every tool is on PATH."""
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        message = "not installed: " + ", ".join(missing)
        if os.environ.get("REQUIRE_MEDIA_TOOLS"):
            pytest.fail(message)
        pytest.skip(message)


@dataclass
class Track:
    """One audio track for make_video(): its channel count (2 or 6),
    language, name, and which flags it carries."""
    channels: int
    language: str = "eng"
    title: str = ""
    default: bool = False
    comment: bool = False
    visual_impaired: bool = False


def make_video(path, tracks):
    """Write a 1-second video at path with one audio stream per Track.
    Video and audio use encoders built into every ffmpeg (mpeg4, ac3), and
    the audio is silence, so each file is a few KB. A track's title is set
    both as "title" (what MKV uses for a track name) and "handler_name"
    (what MP4 uses)."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=1"]
    for t in tracks:
        cmd += ["-t", "1", "-f", "lavfi", "-i",
                f"anullsrc=channel_layout={LAYOUTS[t.channels]}:sample_rate=48000"]
    cmd += ["-map", "0:v"]
    for i in range(len(tracks)):
        cmd += ["-map", f"{i + 1}:a"]
    cmd += ["-c:v", "mpeg4", "-c:a", "ac3", "-disposition:v:0", "default"]
    for i, t in enumerate(tracks):
        flags = [name for name, on in (("default", t.default), ("comment", t.comment),
                                       ("visual_impaired", t.visual_impaired)) if on]
        cmd += [f"-disposition:a:{i}", "+".join(flags) or "0"]
        if t.language:
            cmd += [f"-metadata:s:a:{i}", f"language={t.language}"]
        if t.title:
            cmd += [f"-metadata:s:a:{i}", f"title={t.title}",
                    f"-metadata:s:a:{i}", f"handler_name={t.title}"]
    subprocess.run(cmd + [str(path)], check=True, capture_output=True, text=True)
    return path


def probe(path):
    """Every stream in path as ffprobe reports it."""
    res = subprocess.run(["ffprobe", "-v", "error", "-of", "json", "-show_entries",
                          "stream=index,codec_type,channels:stream_disposition=default", str(path)],
                         check=True, capture_output=True, text=True)
    return json.loads(res.stdout)["streams"]


def audio_defaults(path):
    """(channels, default flag) for each audio stream, in file order."""
    return [(s["channels"], bool(s["disposition"]["default"]))
            for s in probe(path) if s["codec_type"] == "audio"]


def mkvmerge_defaults(path):
    """Each audio track's default flag as mkvmerge sees it, in file order."""
    res = subprocess.run(["mkvmerge", "-J", str(path)], check=True, capture_output=True, text=True)
    return [t["properties"].get("default_track", False)
            for t in json.loads(res.stdout)["tracks"] if t["type"] == "audio"]


def top_level_boxes(path):
    """The order of an MP4's top-level boxes, e.g. ["ftyp", "moov", "mdat"]."""
    kinds, data = [], path.read_bytes()
    pos = 0
    while pos + 8 <= len(data):
        size = int.from_bytes(data[pos:pos + 4], "big")
        kinds.append(data[pos + 4:pos + 8].decode("latin-1"))
        if size == 1:
            size = int.from_bytes(data[pos + 8:pos + 16], "big")
        if size < 8:
            break
        pos += size
    return kinds


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_script(*args):
    """Run the script as a user would. Returns (exit code, combined output)."""
    res = subprocess.run([sys.executable, str(SCRIPT), "--no-progress", *map(str, args)],
                         capture_output=True, text=True)
    return res.returncode, res.stdout + res.stderr


def summary(output):
    """The summary's counts, e.g. {"changed": 1, "unchanged": 0, ...}."""
    counts = {}
    for line in output.splitlines():
        key, _, value = line.partition(": ")
        if key in ("changed", "unchanged", "skipped", "error", "cancelled") and value.isdigit():
            counts[key] = int(value)
    return counts


@dataclass
class Case:
    """One file to generate and what the script should do with it.

    expect is the summary bucket the file should land in ("changed",
    "unchanged", ...). For "changed", target is the audio stream (counting
    from 0) that must end up the only default; with --avi-reorder, it's the
    original audio stream that must end up first."""
    ext: str
    tracks: list
    expect: str
    target: int = None
    args: list = field(default_factory=list)


CASES = {
    "mkv 5.1 default, stereo second": Case(".mkv", [Track(6, default=True), Track(2)], "changed", 1),
    "mp4 5.1 default, stereo second": Case(".mp4", [Track(6, default=True), Track(2)], "changed", 1),
    "mkv stereo already default": Case(".mkv", [Track(6), Track(2, default=True)], "unchanged"),
    "mp4 stereo already default": Case(".mp4", [Track(6), Track(2, default=True)], "unchanged"),
    "mkv commentary flag": Case(".mkv", [Track(6, default=True), Track(2, comment=True)], "skipped"),
    "mkv audio description flag": Case(
        ".mkv", [Track(6, default=True), Track(2, visual_impaired=True)], "skipped"),
    "mkv commentary by name": Case(
        ".mkv", [Track(6, default=True), Track(2, title="Director's Commentary")], "skipped"),
    "mp4 commentary by name": Case(
        ".mp4", [Track(6, default=True), Track(2, title="Director's Commentary")], "skipped"),
    "mkv real stereo next to a commentary": Case(
        ".mkv", [Track(6, default=True), Track(2, title="Commentary"), Track(2)], "changed", 2),
    "mkv stereo dub in another language": Case(
        ".mkv", [Track(6, "eng", default=True), Track(2, "spa")], "skipped"),
    "mkv two stereo tracks": Case(".mkv", [Track(6, default=True), Track(2), Track(2)], "skipped"),
    "mkv --prefer-lang picks between stereo tracks": Case(
        ".mkv", [Track(6, "eng", default=True), Track(2, "eng"), Track(2, "spa")], "changed", 2,
        ["--prefer-lang", "spa"]),
    "mkv no stereo track": Case(".mkv", [Track(6, default=True)], "skipped"),
    "avi without --avi-reorder": Case(".avi", [Track(6), Track(2)], "skipped"),
    "avi --avi-reorder": Case(".avi", [Track(6), Track(2)], "changed", 1, ["--avi-reorder"]),
}


@pytest.mark.parametrize("case", CASES.values(), ids=CASES.keys())
def test_script_on_a_real_file(tmp_path, case):
    """Generate the case's file, run the script on it, and check the result.
    Files that shouldn't change must be byte-for-byte identical afterwards.
    Changed files must keep every stream and their audio order, except that
    --avi-reorder moves the target to the front and keeps the rest in order."""
    need("ffmpeg", "ffprobe", *(["mkvmerge"] if case.ext == ".mkv" else []))
    video = make_video(tmp_path / f"video{case.ext}", case.tracks)
    before_streams, before_audio, before_digest = probe(video), audio_defaults(video), digest(video)

    code, output = run_script(video, *case.args)

    assert code == 0, output
    assert summary(output)[case.expect] == 1, output
    if case.expect != "changed":
        assert digest(video) == before_digest, "file was modified"
        return

    after_audio = audio_defaults(video)
    assert len(probe(video)) == len(before_streams), "a stream was lost"
    assert not list(tmp_path.glob("*.tmp_remux*")), "temp file left behind"
    if "--avi-reorder" in case.args:
        moved = before_audio[case.target][0]
        rest = [ch for i, (ch, _) in enumerate(before_audio) if i != case.target]
        assert [ch for ch, _ in after_audio] == [moved] + rest
        return

    assert [ch for ch, _ in after_audio] == [ch for ch, _ in before_audio], "audio order changed"
    assert [d for _, d in after_audio] == [i == case.target for i in range(len(after_audio))]
    if case.ext == ".mkv":
        assert mkvmerge_defaults(video) == [i == case.target for i in range(len(after_audio))]
    if case.ext == ".mp4":
        boxes = top_level_boxes(video)
        assert boxes.index("moov") < boxes.index("mdat"), f"not faststart: {boxes}"


def test_dry_run_changes_nothing(tmp_path):
    need("ffmpeg", "ffprobe", "mkvmerge")
    video = make_video(tmp_path / "video.mkv", [Track(6, default=True), Track(2)])
    before = digest(video)

    code, output = run_script(video, "--dry-run")

    assert code == 0, output
    assert "[dry-run] mkvmerge" in output
    assert digest(video) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["video.mkv"]


def test_backup_keeps_the_original(tmp_path):
    need("ffmpeg", "ffprobe")
    video = make_video(tmp_path / "video.mp4", [Track(6, default=True), Track(2)])
    before = digest(video)

    code, output = run_script(video, "--backup")

    assert code == 0, output
    assert digest(tmp_path / "video.mp4.bak") == before
    assert digest(video) != before
    assert audio_defaults(video) == [(6, False), (2, True)]


def test_unreadable_file_is_an_error_and_left_alone(tmp_path):
    need("ffmpeg", "ffprobe", "mkvmerge")
    video = tmp_path / "broken.mkv"
    video.write_bytes(os.urandom(4096))
    before = digest(video)

    code, output = run_script(video)

    assert code == 1, output
    assert summary(output)["error"] == 1, output
    assert digest(video) == before


def test_a_mixed_folder_with_jobs_and_a_second_run_changes_nothing_more(tmp_path):
    need("ffmpeg", "ffprobe", "mkvmerge")
    make_video(tmp_path / "fix.mkv", [Track(6, default=True), Track(2)])
    make_video(tmp_path / "fix.mp4", [Track(6, default=True), Track(2)])
    (tmp_path / "Season 01").mkdir()
    make_video(tmp_path / "Season 01" / "ok.mkv", [Track(6), Track(2, default=True)])
    make_video(tmp_path / "dub.mkv", [Track(6, "eng", default=True), Track(2, "spa")])

    code, output = run_script(tmp_path, "--jobs", "2")
    assert code == 0, output
    assert summary(output) == {"changed": 2, "unchanged": 1, "skipped": 1, "error": 0}, output

    digests = {p: digest(p) for p in tmp_path.rglob("*.m*")}
    code, output = run_script(tmp_path, "--jobs", "2")
    assert code == 0, output
    assert summary(output) == {"changed": 0, "unchanged": 3, "skipped": 1, "error": 0}, output
    assert {p: digest(p) for p in tmp_path.rglob("*.m*")} == digests
