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
"""The script under test, run as a separate program like a user would."""

LAYOUTS = {2: "stereo", 6: "5.1"}
"""ffmpeg's channel layout name for each channel count make_video() supports."""


def _unavailable(message):
    """Skip the test, or with REQUIRE_MEDIA_TOOLS set, fail it."""
    if os.environ.get("REQUIRE_MEDIA_TOOLS"):
        pytest.fail(message)
    pytest.skip(message)


def need(*tools):
    """Skip (or, with REQUIRE_MEDIA_TOOLS set, fail) unless every tool is on PATH."""
    missing = [t for t in tools if shutil.which(t) is None]
    if missing:
        _unavailable("not installed: " + ", ".join(missing))


def need_encoders(*encoders):
    """Skip (or, with REQUIRE_MEDIA_TOOLS set, fail) unless this ffmpeg can
    encode with every one of encoders, named as ffmpeg -encoders lists them."""
    res = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"], check=True,
                         capture_output=True, text=True)
    have = {line.split()[1] for line in res.stdout.splitlines() if len(line.split()) > 1}
    missing = [e for e in encoders if e not in have]
    if missing:
        _unavailable("ffmpeg can't encode with: " + ", ".join(missing))


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


def make_video(path, tracks, seconds=1, audio_codec=("ac3",)):
    """Write a video of the given length at path with one audio stream per
    Track. Video and audio use encoders built into every ffmpeg (mpeg4,
    ac3), except that WebM needs VP8 video, and the audio is silence, so
    each file is a few KB per second. audio_codec is the ffmpeg arguments
    after -c:a, for another audio codec (see AUDIO_CODECS). A
    track's title is set both as "title" (what MKV uses for a track name)
    and "handler_name" (what MP4 uses). MP4 files get their index at the
    front, so a truncated copy can still be read."""
    cmd = ["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i",
           f"testsrc=size=64x48:rate=5:duration={seconds}"]
    for t in tracks:
        cmd += ["-t", str(seconds), "-f", "lavfi", "-i",
                f"anullsrc=channel_layout={LAYOUTS[t.channels]}:sample_rate=48000"]
    cmd += ["-map", "0:v"]
    for i in range(len(tracks)):
        cmd += ["-map", f"{i + 1}:a"]
    cmd += ["-c:v", "libvpx" if path.suffix == ".webm" else "mpeg4", "-c:a", *audio_codec,
            "-disposition:v:0", "default"]
    for i, t in enumerate(tracks):
        flags = [name for name, on in (("default", t.default), ("comment", t.comment),
                                       ("visual_impaired", t.visual_impaired)) if on]
        cmd += [f"-disposition:a:{i}", "+".join(flags) or "0"]
        if t.language:
            cmd += [f"-metadata:s:a:{i}", f"language={t.language}"]
        if t.title:
            cmd += [f"-metadata:s:a:{i}", f"title={t.title}",
                    f"-metadata:s:a:{i}", f"handler_name={t.title}"]
    if path.suffix == ".mp4":
        cmd += ["-movflags", "+faststart"]
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
    """SHA-256 of path's contents, to tell whether a file changed at all."""
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run_script(*args):
    """Run the script as a user would. Returns (exit code, combined output)."""
    res = subprocess.run([sys.executable, str(SCRIPT), "--no-progress", *map(str, args)],
                         capture_output=True, text=True, check=False)
    return res.returncode, res.stdout + res.stderr


def summary(output):
    """The summary's counts, e.g. {"changed": 1, "unchanged": 0, ...}, or
    {"would change": 1, ...} after a dry run. The summary capitalizes each
    label ("Changed: 1"); they're lowercased here."""
    counts = {}
    for line in output.splitlines():
        key, _, value = line.partition(": ")
        key = key.lower()
        if key in ("changed", "would change", "unchanged", "skipped", "error", "cancelled") \
                and value.isdigit():
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
    "mkv --prefer-lang with a two-letter code": Case(
        ".mkv", [Track(6, "eng", default=True), Track(2, "ger")], "changed", 1,
        ["--prefer-lang", "de"]),
    "mp4 --prefer-lang with no track in it falls back": Case(
        ".mp4", [Track(6, "fre", default=True), Track(2, "spa"), Track(2, "fre")], "changed", 2,
        ["--prefer-lang", "en"]),
    "mp4 one language tagged two ways": Case(
        ".mp4", [Track(6, "ger", default=True), Track(2, "deu")], "changed", 1),
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


CHAPTERS = """;FFMETADATA1
title=Rich Test Movie
[CHAPTER]
TIMEBASE=1/1000
START=0
END=1000
title=Opening
[CHAPTER]
TIMEBASE=1/1000
START=1000
END=2000
title=Ending
"""
"""Chapters and a file title in ffmpeg's metadata format, for make_rich_video()."""


def make_rich_video(folder, ext, font_type="application/x-truetype-font"):
    """Write a two-second video with everything a real library file tends to
    have besides audio, so a remux that drops or changes any of it shows up:
    a file title and two chapters; 5.1 and stereo audio tracks with names;
    two subtitle tracks with languages, names and the forced and
    hearing-impaired flags; and, for MKV, a font attachment of font_type, or
    for MP4, cover art. Subtitles are SubRip in MKV and mov_text in MP4."""
    srt = folder / "subs.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:01,000\nHello\n", encoding="utf-8")
    chapters = folder / "chapters.txt"
    chapters.write_text(CHAPTERS, encoding="utf-8")
    cmd = ["ffmpeg", "-v", "error", "-y",
           "-f", "lavfi", "-i", "testsrc=size=64x48:rate=5:duration=2",
           "-f", "lavfi", "-t", "2", "-i", "anullsrc=channel_layout=5.1:sample_rate=48000",
           "-f", "lavfi", "-t", "2", "-i", "anullsrc=channel_layout=stereo:sample_rate=48000",
           "-i", str(srt), "-i", str(srt), "-i", str(chapters)]
    maps = ["-map", "0:v", "-map", "1:a", "-map", "2:a", "-map", "3:s", "-map", "4:s"]
    if ext == ".mp4":
        cover = folder / "cover.png"
        subprocess.run(["ffmpeg", "-v", "error", "-y", "-f", "lavfi", "-i", "color=c=red:s=32x32",
                        "-frames:v", "1", str(cover)], check=True, capture_output=True)
        cmd += ["-i", str(cover)]
        maps += ["-map", "6:v"]
    else:
        font = folder / "font.ttf"
        font.write_bytes(b"\x00\x01\x00\x00" + bytes(64))
        cmd += ["-attach", str(font), "-metadata:s:t", f"mimetype={font_type}"]
    cmd += maps + ["-map_metadata", "5", "-map_chapters", "5",
                   "-c:v:0", "mpeg4", "-c:a", "ac3",
                   "-c:s", "mov_text" if ext == ".mp4" else "srt",
                   "-disposition:v:0", "default",
                   "-disposition:a:0", "default", "-disposition:a:1", "0",
                   "-disposition:s:0", "forced", "-disposition:s:1", "hearing_impaired"]
    for spec, language, title in (("a:0", "eng", "Surround 5.1"), ("a:1", "eng", "Stereo"),
                                  ("s:0", "eng", "English (Forced)"), ("s:1", "spa", "Español SDH")):
        cmd += [f"-metadata:s:{spec}", f"language={language}", f"-metadata:s:{spec}", f"title={title}",
                f"-metadata:s:{spec}", f"handler_name={title}"]
    if ext == ".mp4":
        cmd += ["-c:v:1", "png", "-disposition:v:1", "attached_pic", "-movflags", "+faststart"]
    path = folder / f"rich{ext}"
    subprocess.run(cmd + [str(path)], check=True, capture_output=True, text=True)
    return path


def contents(path):
    """Everything about path a remux must keep, as plain data: the file's
    title, its chapters, and for each stream its type, codec, shape,
    language, name and every disposition flag, except the audio default
    flag, which is the one thing the script changes. Timings are rounded to
    the millisecond, since containers store them with different
    precision."""
    res = subprocess.run(["ffprobe", "-v", "error", "-of", "json", "-show_streams",
                          "-show_chapters", "-show_format", str(path)],
                         check=True, capture_output=True, text=True)
    data = json.loads(res.stdout)
    streams = []
    for s in data["streams"]:
        tags = {k.lower(): v for k, v in s.get("tags", {}).items()}
        disposition = dict(s.get("disposition", {}))
        if s["codec_type"] == "audio":
            disposition.pop("default", None)
        streams.append({
            "type": s["codec_type"], "codec": s.get("codec_name"),
            "channels": s.get("channels"), "size": (s.get("width"), s.get("height")),
            "tags": {k: tags[k] for k in ("language", "title", "handler_name", "filename", "mimetype")
                     if k in tags},
            "disposition": disposition})
    chapters = [(round(float(c["start_time"]), 3), round(float(c["end_time"]), 3),
                 c.get("tags", {}).get("title")) for c in data.get("chapters", [])]
    title = {k.lower(): v for k, v in data["format"].get("tags", {}).items()}.get("title")
    return {"title": title, "chapters": chapters, "streams": streams}


@pytest.mark.parametrize("ext, font_type", [
    (".mkv", "application/x-truetype-font"),
    (".mkv", "font/ttf"),
    (".mp4", None),
], ids=["mkv, older font type", "mkv, newer font type", "mp4"])
def test_a_remux_keeps_everything_but_the_audio_default(tmp_path, ext, font_type):
    """The test file is checked first, so a missing feature in the test's
    own ffmpeg can't make the comparison pass by having nothing to compare.
    Both font types must come through as they were: newer mkvmerge versions
    rewrite the older one unless told not to."""
    need("ffmpeg", "ffprobe", *(["mkvmerge"] if ext == ".mkv" else []))
    video = make_rich_video(tmp_path, ext, *([font_type] if font_type else []))
    before = contents(video)
    assert before["title"] == "Rich Test Movie" and len(before["chapters"]) == 2, before
    kinds = [s["type"] for s in before["streams"]]
    assert kinds.count("audio") == 2 and kinds.count("subtitle") == 2, before
    assert kinds.count("attachment" if ext == ".mkv" else "video") == (1 if ext == ".mkv" else 2), before
    assert audio_defaults(video) == [(6, True), (2, False)]

    code, output = run_script(video)

    assert code == 0, output
    assert summary(output)["changed"] == 1, output
    assert audio_defaults(video) == [(6, False), (2, True)]
    assert contents(video) == before


AUDIO_CODECS = {
    "aac": (("aac",), "aac"),
    "e-ac3": (("eac3",), "eac3"),
    "dts": (("dca", "-strict", "-2"), "dts"),
    "truehd": (("truehd", "-strict", "-2"), "truehd"),
    "flac": (("flac",), "flac"),
    "opus": (("libopus",), "opus"),
}
"""The common audio codecs besides AC3, each as (ffmpeg arguments after
-c:a, the name ffprobe reports). ffmpeg calls its DTS and TrueHD encoders
experimental, hence -strict -2."""

CODEC_CASES = [(".mkv", codec) for codec in AUDIO_CODECS] + [
    (".mp4", "aac"), (".mp4", "e-ac3"), (".webm", "opus")]
"""Each codec in MKV, and the usual ones in MP4 and WebM."""


@pytest.mark.parametrize("ext, codec", CODEC_CASES, ids=[f"{e[1:]} {c}" for e, c in CODEC_CASES])
def test_every_common_audio_codec_is_remuxed_untouched(tmp_path, ext, codec):
    """Both audio tracks use the codec. The remux must flip the default
    flags and nothing else, so the audio is copied, never re-encoded."""
    encoder_args, probed_as = AUDIO_CODECS[codec]
    need("ffmpeg", "ffprobe", *(["mkvmerge"] if ext in (".mkv", ".webm") else []))
    need_encoders(encoder_args[0], *(["libvpx"] if ext == ".webm" else []))
    video = make_video(tmp_path / f"video{ext}", [Track(6, default=True), Track(2)],
                       audio_codec=encoder_args)
    before = contents(video)
    assert [s["codec"] for s in before["streams"] if s["type"] == "audio"] == [probed_as] * 2

    code, output = run_script(video)

    assert code == 0, output
    assert summary(output)["changed"] == 1, output
    assert audio_defaults(video) == [(6, False), (2, True)]
    assert contents(video) == before


def test_dry_run_changes_nothing(tmp_path):
    need("ffmpeg", "ffprobe", "mkvmerge")
    video = make_video(tmp_path / "video.mkv", [Track(6, default=True), Track(2)])
    before = digest(video)

    code, output = run_script(video, "--dry-run")

    assert code == 0, output
    assert "[dry-run] mkvmerge" in output
    assert "----- Summary (Dry run, nothing was changed) -----" in output
    assert summary(output) == {"would change": 1, "unchanged": 0, "skipped": 0, "error": 0}
    assert digest(video) == before
    assert sorted(p.name for p in tmp_path.iterdir()) == ["video.mkv"]


@pytest.mark.parametrize("ext", [".mkv", ".mp4"])
@pytest.mark.parametrize("keep", [True, False], ids=["--keep-dates", "default"])
def test_keep_dates_keeps_the_original_modification_time(tmp_path, ext, keep):
    need("ffmpeg", "ffprobe", *(["mkvmerge"] if ext == ".mkv" else []))
    video = make_video(tmp_path / f"video{ext}", [Track(6, default=True), Track(2)])
    old_mtime_ns = 1_577_890_000_000_000_000
    os.utime(video, ns=(old_mtime_ns, old_mtime_ns))

    code, output = run_script(video, *(["--keep-dates"] if keep else []))

    assert code == 0, output
    assert summary(output)["changed"] == 1, output
    if keep:
        assert video.stat().st_mtime_ns == old_mtime_ns
    else:
        assert video.stat().st_mtime_ns > old_mtime_ns


def test_backup_keeps_the_original(tmp_path):
    need("ffmpeg", "ffprobe")
    video = make_video(tmp_path / "video.mp4", [Track(6, default=True), Track(2)])
    before = digest(video)

    code, output = run_script(video, "--backup")

    assert code == 0, output
    assert digest(tmp_path / "video.mp4.bak") == before
    assert digest(video) != before
    assert audio_defaults(video) == [(6, False), (2, True)]


@pytest.mark.parametrize("option, kept", [
    ([], ["video.mp4.bak", "video.mp4.bak.1"]),
    (["--existing-backups", "replace"], ["video.mp4.bak"]),
], ids=["numbered without a terminal", "--existing-backups replace"])
def test_a_second_backup_never_loses_the_first_unless_asked(tmp_path, option, kept):
    """A second --backup --force run on the same file finds the first run's
    .bak. With no terminal to ask (as here, and in cron or Docker) the new
    backup is numbered; with --existing-backups replace it overwrites."""
    need("ffmpeg", "ffprobe")
    video = make_video(tmp_path / "video.mp4", [Track(6, default=True), Track(2)])
    original = digest(video)
    assert run_script(video, "--backup")[0] == 0
    first_result = digest(video)

    code, output = run_script(video, "--backup", "--force", *option)

    assert code == 0, output
    assert sorted(p.name for p in tmp_path.glob("video.mp4.bak*")) == kept
    if len(kept) == 2:
        assert digest(tmp_path / "video.mp4.bak") == original
        assert digest(tmp_path / "video.mp4.bak.1") == first_result
        assert "kept the original as video.mp4.bak.1" in output
    else:
        assert digest(tmp_path / "video.mp4.bak") == first_result


def test_unreadable_file_is_an_error_and_left_alone(tmp_path):
    """The file starts with the MKV signature, so ffprobe reads it as MKV
    and rejects the junk that follows. Random bytes aren't used: ffprobe
    now and then takes them for some other format (e.g. lyrics), and then
    the file is merely skipped for having no audio."""
    need("ffmpeg", "ffprobe", "mkvmerge")
    video = tmp_path / "broken.mkv"
    video.write_bytes(bytes.fromhex("1a45dfa3") + b"\xff" * 4092)
    before = digest(video)

    code, output = run_script(video)

    assert code == 1, output
    assert summary(output)["error"] == 1, output
    assert digest(video) == before


@pytest.mark.parametrize("ext", [".mkv", ".mp4"])
def test_a_truncated_file_is_an_error_and_left_alone(tmp_path, ext):
    """An incomplete download still claims its full length in its header,
    but a remux only contains what's really there, so it comes out much
    shorter. The script must reject it rather than hide the problem."""
    need("ffmpeg", "ffprobe", *(["mkvmerge"] if ext == ".mkv" else []))
    full = make_video(tmp_path / f"full{ext}", [Track(6, default=True), Track(2)], seconds=20)
    video = tmp_path / f"video{ext}"
    video.write_bytes(full.read_bytes()[:full.stat().st_size // 2])
    before = digest(video)

    code, output = run_script(video)

    assert code == 1, output
    assert summary(output)["error"] == 1, output
    assert "duration dropped from" in output, output
    assert digest(video) == before
    assert not list(tmp_path.glob("*.tmp_remux*")), "temp file left behind"


def test_a_jobs_dry_run_shows_each_files_header_once(tmp_path):
    """With --jobs, each file's "setting ..." line and dry-run command come
    out together, so its [i/N] header is never printed a second time."""
    need("ffmpeg", "ffprobe", "mkvmerge")
    for i in range(4):
        make_video(tmp_path / f"e{i}.mkv", [Track(6, default=True), Track(2)])

    code, output = run_script(tmp_path, "--jobs", "2", "--dry-run")

    assert code == 0, output
    headers = [line for line in output.splitlines() if line.startswith("[")]
    assert sorted(headers) == sorted(f"[{i}/4] {tmp_path / f'e{i - 1}.mkv'}" for i in range(1, 5)), \
        output
    assert output.count("[dry-run] mkvmerge") == 4


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


@pytest.mark.parametrize("skip", [False, True], ids=["default", "--skip-symlinks"])
def test_a_symlinked_file_is_fixed_through_its_link(tmp_path, skip):
    """By default the file a link points to is fixed and the link survives.
    With --skip-symlinks, neither is touched."""
    need("ffmpeg", "ffprobe", "mkvmerge")
    real = make_video(tmp_path / "real.mkv", [Track(6, default=True), Track(2)])
    library = tmp_path / "library"
    library.mkdir()
    link = library / "movie.mkv"
    try:
        link.symlink_to(real)
    except OSError as exc:
        pytest.skip(f"can't create symlinks here: {exc}")
    before = digest(real)

    code, output = run_script(library, *(["--skip-symlinks"] if skip else []))

    assert link.is_symlink(), "the link was replaced"
    assert link.resolve() == real.resolve()
    assert not list(tmp_path.rglob("*.tmp_remux*")), "temp file left behind"
    if skip:
        assert code == 1 and "No matching files found" in output, output
        assert digest(real) == before
    else:
        assert code == 0, output
        assert summary(output)["changed"] == 1, output
        assert audio_defaults(real) == [(6, False), (2, True)]
