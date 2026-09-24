"""Tests for set_stereo_default.py. None of them need ffmpeg, ffprobe or
mkvmerge: anything that would call those tools is replaced with a stand-in,
and subprocess behavior is exercised with small Python child processes."""

import json
import os
import shlex
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

import set_stereo_default as ssd


def audio(index, channels, codec="aac", language="eng", default=False):
    """An audio stream the way probe_audio_streams() describes it."""
    return {"index": index, "channels": channels, "codec": codec,
            "language": language, "title": "", "default": default}


def stream(index, codec_type, default=0, codec="aac", channels=None, language=None):
    """A stream the way ffprobe reports it to probe_layout()."""
    s = {"index": index, "codec_type": codec_type, "codec_name": codec,
         "disposition": {"default": default}}
    if channels:
        s["channels"] = channels
    if language:
        s["tags"] = {"language": language}
    return s


def python_cmd(code):
    """A command that runs a small Python program as a child process."""
    return [sys.executable, "-u", "-c", code]


def parse_pct_line(line):
    """parse_pct for the test child processes, which print "pct=N"."""
    return int(line.split("=", 1)[1]) if line.startswith("pct=") else None



def test_choose_target_picks_the_only_stereo_track():
    target, note = ssd.choose_target([audio(1, 6), audio(2, 2)], None)
    assert target["index"] == 2 and note is None


def test_choose_target_skips_files_without_a_stereo_track():
    target, note = ssd.choose_target([audio(1, 6)], None)
    assert target is None and "no 2-channel" in note


def test_choose_target_skips_files_with_several_stereo_tracks():
    streams = [audio(1, 2, language="eng"), audio(2, 2, language="spa")]
    target, note = ssd.choose_target(streams, None)
    assert target is None and "--prefer-lang" in note


def test_prefer_lang_breaks_the_tie_case_insensitively():
    streams = [audio(1, 2, language="spa"), audio(2, 2, language="eng")]
    target, _ = ssd.choose_target(streams, "ENG")
    assert target["index"] == 2


def test_prefer_lang_that_matches_nothing_still_skips():
    streams = [audio(1, 2, language="spa"), audio(2, 2, language="fre")]
    target, _ = ssd.choose_target(streams, "eng")
    assert target is None


@pytest.mark.parametrize("defaults, expected", [
    ((True, False), True),
    ((False, True), False),
    ((True, True), True),
    ((False, False), True),
], ids=["5.1 is default", "already correct", "both default", "neither default"])
def test_needs_change(defaults, expected):
    streams = [audio(1, 6, default=defaults[0]), audio(2, 2, default=defaults[1])]
    assert ssd.needs_change(streams, 2) is expected



@pytest.fixture
def library(tmp_path):
    """A small media folder with videos, non-video files, a backup, a
    leftover temp file, a subfolder, and a directory named like a video."""
    for name in ["a.mkv", "b.MP4", "notes.nfo", "a.mkv.bak", "a.mkv.tmp_remux.mkv",
                 "Season 01/c.mkv", "Season 01/c.srt"]:
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("x")
    (tmp_path / "folder.mkv").mkdir()
    return tmp_path


def names(paths, root):
    return sorted(p.relative_to(root).as_posix() for p in paths)


def test_iter_files_recursive(library):
    found = ssd.iter_files([library], ssd.DEFAULT_EXTS, recursive=True)
    assert names(found, library) == ["Season 01/c.mkv", "a.mkv", "b.MP4"]


def test_iter_files_non_recursive(library):
    found = ssd.iter_files([library], ssd.DEFAULT_EXTS, recursive=False)
    assert names(found, library) == ["a.mkv", "b.MP4"]


def test_iter_files_respects_ext(library):
    found = ssd.iter_files([library], {".mp4"}, recursive=True)
    assert names(found, library) == ["b.MP4"]


def test_iter_files_accepts_files_passed_directly(library):
    paths = [library / "a.mkv", library / "a.mkv.tmp_remux.mkv", library / "notes.nfo"]
    assert names(ssd.iter_files(paths, ssd.DEFAULT_EXTS, recursive=True), library) == ["a.mkv"]


def test_iter_files_warns_about_leftover_temp_files(library, caplog):
    list(ssd.iter_files([library], ssd.DEFAULT_EXTS, recursive=True))
    assert "a.mkv.tmp_remux.mkv" in caplog.text
    assert "safe to delete" in caplog.text



def test_make_backup_hard_links_and_replaces_a_stale_backup(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    bak = tmp_path / "v.mkv.bak"
    bak.write_bytes(b"stale")

    ssd.make_backup(video)

    assert bak.read_bytes() == b"original"
    assert os.stat(bak).st_ino == os.stat(video).st_ino


def test_make_backup_copies_where_hard_links_are_unsupported(tmp_path, monkeypatch):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")

    def no_hard_links(*args):
        raise OSError("hard links not supported")
    monkeypatch.setattr(ssd.os, "link", no_hard_links)
    ssd.make_backup(video)

    bak = tmp_path / "v.mkv.bak"
    assert bak.read_bytes() == b"original"
    assert os.stat(bak).st_ino != os.stat(video).st_ino



@pytest.fixture
def fake_ffprobe(monkeypatch):
    """Replace run() with a stand-in ffprobe. Set layouts[path] to the
    streams it should report for that file, or None to make it fail."""
    layouts = {}

    def fake_run(cmd, **kw):
        streams = layouts[cmd[-1]]
        if streams is None:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="unreadable")
        return types.SimpleNamespace(returncode=0, stdout=json.dumps({"streams": streams}), stderr="")
    monkeypatch.setattr(ssd, "run", fake_run)
    return layouts


ORIGINAL_LAYOUT = [stream(0, "video", 1, "h264"), stream(1, "audio", 1, "eac3", 6, "eng"),
                   stream(2, "audio", 0, "aac", 2, "eng"), stream(3, "subtitle")]
ORIGINAL_AUDIO = [audio(1, 6, "eac3", default=True), audio(2, 2, "aac")]


@pytest.mark.parametrize("remuxed, expected", [
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 0, "eac3", 6),
      stream(2, "audio", 1, "aac", 2), stream(3, "subtitle")], None),
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 0, "eac3", 6),
      stream(2, "audio", 1, "aac", 2)], "stream count changed from 4 to 3"),
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 1, "eac3", 6),
      stream(2, "audio", 0, "aac", 2), stream(3, "subtitle")], "default flag is on stream#1,"),
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 1, "eac3", 6),
      stream(2, "audio", 1, "aac", 2), stream(3, "subtitle")], "stream#1, stream#2"),
    (None, "couldn't read"),
], ids=["good", "stream dropped", "flag not moved", "both default", "unreadable"])
def test_verify_remux(fake_ffprobe, remuxed, expected):
    fake_ffprobe["orig"] = ORIGINAL_LAYOUT
    fake_ffprobe["tmp"] = remuxed
    problem = ssd.verify_remux("orig", "tmp", ORIGINAL_AUDIO, 2, reordered=False)
    if expected is None:
        assert problem is None
    else:
        assert expected in problem


def test_verify_remux_avi_reorder(fake_ffprobe):
    fake_ffprobe["orig"] = ORIGINAL_LAYOUT
    fake_ffprobe["tmp"] = [stream(0, "video", 0, "xvid"), stream(1, "audio", 0, "aac", 2, "eng"),
                           stream(2, "audio", 0, "eac3", 6, "eng"), stream(3, "subtitle")]
    assert ssd.verify_remux("orig", "tmp", ORIGINAL_AUDIO, 2, reordered=True) is None

    fake_ffprobe["tmp"] = ORIGINAL_LAYOUT
    assert "didn't end up first" in ssd.verify_remux("orig", "tmp", ORIGINAL_AUDIO, 2, reordered=True)



def remux_with_mkvmerge(path):
    return ssd.apply_mkv(path, ORIGINAL_AUDIO, 2, dry_run=False, backup=True)


def remux_with_ffmpeg(path):
    return ssd.apply_remux(path, ORIGINAL_AUDIO, 2, dry_run=False, backup=True,
                           reorder_for_avi=False, duration=100.0)


@pytest.fixture(params=[(remux_with_mkvmerge, "v.mkv"), (remux_with_ffmpeg, "v.mp4")],
                ids=["mkvmerge", "ffmpeg"])
def remux(request, tmp_path, monkeypatch):
    """Returns (apply, video, set_outcome). Instead of running a real remux,
    the stand-in writes "remuxed" to the temp file; set_outcome("fail"),
    set_outcome("interrupt") or set_outcome(<reason>) changes what happens."""
    apply, filename = request.param
    video = tmp_path / filename
    video.write_bytes(b"original")
    outcome = {"run": "ok", "verify": None}

    def fake_run_with_progress(cmd, *args, **kwargs):
        tmp = next(Path(c) for c in cmd if ssd.TMP_MARKER in c)
        tmp.write_bytes(b"remuxed")
        if outcome["run"] == "interrupt":
            raise KeyboardInterrupt
        return (1, "remux error") if outcome["run"] == "fail" else (0, "")

    monkeypatch.setattr(ssd, "run_with_progress", fake_run_with_progress)
    monkeypatch.setattr(ssd, "verify_remux", lambda *a, **k: outcome["verify"])

    def set_outcome(value):
        if value in ("fail", "interrupt"):
            outcome["run"] = value
        else:
            outcome["verify"] = value
    return lambda: apply(video), video, set_outcome


def leftover_temp_files(video):
    return list(video.parent.glob("*" + ssd.TMP_MARKER + "*"))


def test_successful_remux_replaces_the_original_and_backs_it_up(remux):
    apply, video, _ = remux
    assert apply() is True
    assert video.read_bytes() == b"remuxed"
    assert video.with_name(video.name + ".bak").read_bytes() == b"original"
    assert not leftover_temp_files(video)


@pytest.mark.parametrize("outcome", ["fail", "stream count changed from 4 to 3"])
def test_failed_or_rejected_remux_keeps_the_original(remux, outcome, caplog):
    apply, video, set_outcome = remux
    set_outcome(outcome)
    assert apply() is False
    assert video.read_bytes() == b"original"
    assert not leftover_temp_files(video)
    if outcome != "fail":
        assert outcome in caplog.text


def test_interrupted_remux_keeps_the_original(remux):
    apply, video, set_outcome = remux
    set_outcome("interrupt")
    with pytest.raises(KeyboardInterrupt):
        apply()
    assert video.read_bytes() == b"original"
    assert not leftover_temp_files(video)


@pytest.mark.parametrize("apply, tool", [
    (lambda p: ssd.apply_mkv(p, ORIGINAL_AUDIO, 2, dry_run=True, backup=False), "mkvmerge"),
    (lambda p: ssd.apply_remux(p, ORIGINAL_AUDIO, 2, dry_run=True, backup=False,
                               reorder_for_avi=False), "ffmpeg"),
], ids=["mkvmerge", "ffmpeg"])
def test_dry_run_prints_a_command_that_can_be_pasted_into_a_shell(tmp_path, caplog, apply, tool):
    caplog.set_level("INFO")
    video = tmp_path / "Show (2026) - S01E01 [WEBDL-1080p][AAC 2.0] Joey's.mkv"
    video.write_bytes(b"original")

    assert apply(video) is True

    printed = caplog.text.split("[dry-run] ", 1)[1].strip()
    args = shlex.split(printed)
    assert args[0] == tool
    assert str(video) in args
    assert str(video) + ssd.TMP_MARKER + ".mkv" in args
    assert video.read_bytes() == b"original"


def test_ffmpeg_progress_is_reported_without_a_per_file_bar(tmp_path, monkeypatch):
    def fake_run_with_progress(cmd, label, show_progress, parse_pct, position=0, on_progress=None):
        for us in (25_000_000, 50_000_000, 100_000_000):
            on_progress(parse_pct(f"out_time_us={us}\n"))
        return 1, ""
    monkeypatch.setattr(ssd, "run_with_progress", fake_run_with_progress)

    seen = []
    ssd.apply_remux(tmp_path / "v.mp4", ORIGINAL_AUDIO, 2, dry_run=False, backup=False,
                    reorder_for_avi=False, duration=100.0, show_progress=False,
                    on_progress=seen.append)
    assert seen == [25, 50, 100]



def test_run_with_progress_reports_increases_and_finishes_at_100():
    seen = []
    code = "for p in (10, 40, 40, 30, 70): print(f'pct={p}')"
    returncode, _ = ssd.run_with_progress(python_cmd(code), "x", False, parse_pct_line,
                                          on_progress=seen.append)
    assert returncode == 0
    assert seen == [10, 40, 70, 100]


def test_run_with_progress_keeps_only_the_last_50_lines():
    code = ("import sys\n"
            "for i in range(500): print(i)\n"
            "print('Error: boom', file=sys.stderr)\n"
            "sys.exit(3)")
    returncode, output = ssd.run_with_progress(python_cmd(code), "x", False, parse_pct_line)
    lines = output.splitlines()
    assert returncode == 3
    assert len(lines) == 50
    assert lines[-1] == "Error: boom"


SLOW_CMD = python_cmd("import time\n"
                      "for i in range(1, 10000):\n"
                      "    print(f'pct={i % 100}')\n"
                      "    time.sleep(0.05)")


def test_run_with_progress_kills_its_subprocess_on_an_exception(monkeypatch):
    started = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        started.append(real_popen(*args, **kwargs))
        return started[-1]
    monkeypatch.setattr(ssd.subprocess, "Popen", recording_popen)

    def fail(pct):
        raise RuntimeError("stop")

    with pytest.raises(RuntimeError):
        ssd.run_with_progress(SLOW_CMD, "x", False, parse_pct_line, on_progress=fail)
    assert started[0].poll() is not None
    assert not ssd._active_procs


def test_terminate_active_procs_unblocks_a_worker_thread():
    result = {}

    def worker():
        result["returncode"] = ssd.run_with_progress(SLOW_CMD, "x", False, parse_pct_line)[0]

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 10
    while not ssd._active_procs and time.monotonic() < deadline:
        time.sleep(0.05)

    ssd._terminate_active_procs()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert result["returncode"] != 0
    assert not ssd._active_procs


def test_terminate_active_procs_waits_5s_in_total_not_per_process(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(ssd, "time", types.SimpleNamespace(monotonic=lambda: clock[0]))

    class IgnoresTerminate:
        def __init__(self):
            self.waited = None
            self.killed = False

        def terminate(self):
            pass

        def wait(self, timeout=None):
            self.waited = timeout
            clock[0] += timeout
            raise subprocess.TimeoutExpired("stub", timeout)

        def kill(self):
            self.killed = True

    procs = [IgnoresTerminate() for _ in range(4)]
    ssd._active_procs.update(procs)
    ssd._terminate_active_procs()

    assert sorted(p.waited for p in procs) == [0, 0, 0, 5]
    assert all(p.killed for p in procs)



@pytest.mark.parametrize("jobs", ["1", "3"])
def test_overall_bar_moves_during_each_file(tmp_path, monkeypatch, jobs):
    pytest.importorskip("tqdm")
    for name in ("a.mp4", "b.mp4", "c.mp4"):
        (tmp_path / name).write_text("x")

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        for pct in (25, 50, 75):
            on_progress(pct)
        return "changed"

    shown = []

    class RecordingTqdm(ssd.tqdm):
        def refresh(self, *args, **kwargs):
            if self.desc == "Processing":
                shown.append(round(self.n, 2))
            return super().refresh(*args, **kwargs)

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(ssd, "tqdm", RecordingTqdm)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--jobs", jobs])

    ssd.main()

    assert {round(0.25 * i, 2) for i in range(1, 13)} <= set(shown)
    assert max(shown) == 3.0


@pytest.mark.filterwarnings("error")
def test_overall_bar_never_drifts_past_the_total(tmp_path, monkeypatch):
    """Two files reporting 1% at a time used to add up to
    2.0000000000000004, which made tqdm warn and show a negative ETA."""
    pytest.importorskip("tqdm")
    for name in ("a.mkv", "b.mkv"):
        (tmp_path / name).write_text("x")

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        for pct in range(1, 99):
            on_progress(pct)
        return "changed"

    finals = []

    class RecordingTqdm(ssd.tqdm):
        def close(self):
            if self.desc == "Processing":
                finals.append(self.n)
            return super().close()

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(ssd, "tqdm", RecordingTqdm)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path)])

    ssd.main()

    assert finals and set(finals) == {2}
