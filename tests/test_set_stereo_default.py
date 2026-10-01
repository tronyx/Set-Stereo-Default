"""Tests for set_stereo_default.py. None of them need ffmpeg, ffprobe or
mkvmerge: anything that would call those tools is replaced with a stand-in,
and subprocess behavior is exercised with small Python child processes."""

import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
import types
from pathlib import Path

import pytest

import set_stereo_default as ssd


def audio(index, channels, codec="aac", language="eng", default=False, name="",
          comment=False, visual_impaired=False):
    """An audio stream the way probe_audio_streams() describes it."""
    return {"index": index, "channels": channels, "codec": codec,
            "language": language, "names": [name] if name else [], "default": default,
            "comment": comment, "visual_impaired": visual_impaired}


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
    streams = [audio(1, 2, language="eng"), audio(2, 2, language="eng")]
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


@pytest.mark.parametrize("commentary", [
    audio(2, 2, comment=True),
    audio(2, 2, visual_impaired=True),
    audio(2, 2, name="Director's Commentary"),
    audio(2, 2, name="English Descriptive Audio"),
    audio(2, 2, name="Audio Description"),
], ids=["comment flag", "visual impaired flag", "commentary name", "descriptive name",
        "description name"])
def test_choose_target_never_picks_commentary_or_audio_description(commentary):
    target, note = ssd.choose_target([audio(1, 6, default=True), commentary], None)
    assert target is None and "commentary/audio description" in note


def test_choose_target_picks_the_stereo_track_next_to_a_commentary():
    streams = [audio(1, 6, default=True), audio(2, 2, name="Commentary"), audio(3, 2)]
    target, _ = ssd.choose_target(streams, None)
    assert target["index"] == 3


def test_choose_target_skips_a_stereo_dub_in_another_language():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="spa")]
    target, note = ssd.choose_target(streams, None)
    assert target is None and "'eng'" in note and "spa" in note


def test_choose_target_uses_the_first_track_language_when_none_is_default():
    streams = [audio(1, 6, language="jpn"), audio(2, 2, language="eng"), audio(3, 2, language="jpn")]
    target, _ = ssd.choose_target(streams, None)
    assert target["index"] == 3


def test_choose_target_follows_the_default_track_language_between_stereo_tracks():
    streams = [audio(1, 6, language="spa", default=True), audio(2, 2, language="eng"),
               audio(3, 2, language="spa")]
    target, _ = ssd.choose_target(streams, None)
    assert target["index"] == 3


def test_prefer_lang_overrides_the_default_track_language():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="spa")]
    target, _ = ssd.choose_target(streams, "spa")
    assert target["index"] == 2


@pytest.mark.parametrize("lang", ["", "und"])
def test_untagged_stereo_track_matches_any_language(lang):
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language=lang)]
    target, _ = ssd.choose_target(streams, None)
    assert target["index"] == 2


def test_exact_language_match_wins_over_an_untagged_track():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="und"),
               audio(3, 2, language="eng")]
    target, _ = ssd.choose_target(streams, None)
    assert target["index"] == 3


def test_probe_audio_streams_reads_commentary_flags(monkeypatch):
    probe = {"streams": [
        {"index": 1, "channels": 2, "codec_name": "aac", "tags": {"language": "eng"},
         "disposition": {"default": 0, "comment": 1, "visual_impaired": 0}},
        {"index": 2, "channels": 2, "codec_name": "aac",
         "disposition": {"default": 1, "comment": 0, "visual_impaired": 1}},
    ], "format": {"duration": "60.0"}}
    monkeypatch.setattr(ssd, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=0, stdout=json.dumps(probe), stderr=""))

    streams, duration = ssd.probe_audio_streams(Path("v.mkv"))

    assert [(s["comment"], s["visual_impaired"], s["default"]) for s in streams] == \
        [(True, False, False), (False, True, True)]
    assert duration == 60.0


@pytest.mark.parametrize("tags, is_commentary", [
    ({"title": "Director's Commentary"}, True),
    ({"name": "Director's Commentary", "handler_name": "SoundHandler"}, True),
    ({"handler_name": "Audio Description"}, True),
    ({"title": "Stereo", "handler_name": "SoundHandler"}, False),
], ids=["mkv title", "mp4 name", "mp4 handler_name", "ordinary names"])
def test_commentary_is_found_by_any_track_name(monkeypatch, tags, is_commentary):
    probe = {"streams": [{"index": 1, "channels": 2, "codec_name": "aac",
                          "tags": dict(tags, language="eng"), "disposition": {}}]}
    monkeypatch.setattr(ssd, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=0, stdout=json.dumps(probe), stderr=""))

    streams, _ = ssd.probe_audio_streams(Path("v.mp4"))

    assert ssd.is_commentary(streams[0]) is is_commentary


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



@pytest.mark.skipif(sys.platform == "win32", reason="Windows only has a read-only flag, not Unix permissions")
def test_swap_in_keeps_the_original_permissions(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    video.chmod(0o764)
    tmp = tmp_path / "v.mkv.tmp_remux.mkv"
    tmp.write_bytes(b"remuxed")
    tmp.chmod(0o600)

    ssd.swap_in(video, tmp, backup=False)

    assert video.read_bytes() == b"remuxed"
    assert video.stat().st_mode & 0o777 == 0o764


def test_swap_in_gives_the_new_file_the_original_owner(tmp_path, monkeypatch):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    tmp = tmp_path / "v.mkv.tmp_remux.mkv"
    tmp.write_bytes(b"remuxed")
    calls = []
    monkeypatch.setattr(ssd.os, "chown", lambda *args: calls.append(args), raising=False)

    ssd.swap_in(video, tmp, backup=False)

    st = video.stat()
    assert calls == [(tmp, st.st_uid, st.st_gid)]


def test_swap_in_warns_once_when_the_owner_cant_be_changed(tmp_path, monkeypatch, caplog):
    def not_permitted(*args):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(ssd.os, "chown", not_permitted, raising=False)

    for name in ("a.mkv", "b.mkv"):
        video = tmp_path / name
        video.write_bytes(b"original")
        tmp = tmp_path / (name + ".tmp_remux.mkv")
        tmp.write_bytes(b"remuxed")
        ssd.swap_in(video, tmp, backup=False)
        assert video.read_bytes() == b"remuxed"

    assert caplog.text.count("Couldn't give remuxed files their original owner") == 1
    assert "Operation not permitted" in caplog.text


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
    set_outcome("warn"), set_outcome("interrupt") or set_outcome(<reason>)
    changes what happens."""
    apply, filename = request.param
    video = tmp_path / filename
    video.write_bytes(b"original")
    outcome = {"run": "ok", "verify": None}
    results = {"ok": (0, ""), "fail": (2, "remux error"),
               "warn": (1, "#GUI#warning Warning: odd timestamps\n")}

    def fake_run_with_progress(cmd, *args, **kwargs):
        tmp = next(Path(c) for c in cmd if ssd.TMP_MARKER in c)
        tmp.write_bytes(b"remuxed")
        if outcome["run"] == "interrupt":
            raise KeyboardInterrupt
        return results[outcome["run"]]

    monkeypatch.setattr(ssd, "run_with_progress", fake_run_with_progress)
    monkeypatch.setattr(ssd, "verify_remux", lambda *a, **k: outcome["verify"])

    def set_outcome(value):
        if value in ("fail", "warn", "interrupt"):
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


def test_cancelled_remux_names_the_file(remux, caplog):
    apply, video, set_outcome = remux
    caplog.set_level("INFO")
    set_outcome("fail")
    ssd._cancelled.set()
    assert apply() is False
    assert f"{video.name}: cancelled" in caplog.text
    assert "remux failed" not in caplog.text


def test_interrupted_remux_keeps_the_original(remux):
    apply, video, set_outcome = remux
    set_outcome("interrupt")
    with pytest.raises(KeyboardInterrupt):
        apply()
    assert video.read_bytes() == b"original"
    assert not leftover_temp_files(video)


def test_exit_code_1_is_warnings_for_mkvmerge_but_failure_for_ffmpeg(remux, caplog):
    apply, video, set_outcome = remux
    set_outcome("warn")
    if video.suffix == ".mkv":
        assert apply() is True
        assert video.read_bytes() == b"remuxed"
        assert "finished with warnings" in caplog.text
        assert "odd timestamps" in caplog.text
    else:
        assert apply() is False
        assert video.read_bytes() == b"original"
        assert "ffmpeg remux failed" in caplog.text
    assert not leftover_temp_files(video)


def test_warning_exit_code_is_still_rejected_if_the_check_fails(remux, caplog):
    apply, video, set_outcome = remux
    set_outcome("warn")
    set_outcome("stream count changed from 4 to 3")
    assert apply() is False
    assert video.read_bytes() == b"original"
    assert not leftover_temp_files(video)


def test_exit_code_1_after_ctrl_c_counts_as_cancelled(remux, caplog):
    apply, video, set_outcome = remux
    caplog.set_level("INFO")
    set_outcome("warn")
    ssd._cancelled.set()
    assert apply() is False
    assert video.read_bytes() == b"original"
    assert f"{video.name}: cancelled" in caplog.text
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


def file_args(**overrides):
    """process_file()'s args, as a --dry-run with no progress bar."""
    values = dict(prefer_lang=None, avi_reorder=False, force=False, dry_run=True,
                  backup=False, no_progress=True, jobs=1)
    values.update(overrides)
    return types.SimpleNamespace(**values)


@pytest.fixture
def probed(monkeypatch):
    """Replace probe_audio_streams() so every file reports ORIGINAL_AUDIO;
    the returned list records which files were probed."""
    calls = []

    def fake_probe(path):
        calls.append(path.name)
        return [dict(s) for s in ORIGINAL_AUDIO], 100.0
    monkeypatch.setattr(ssd, "probe_audio_streams", fake_probe)
    return calls


def test_avi_without_reorder_is_skipped_before_anything_is_announced(tmp_path, probed, caplog):
    caplog.set_level("INFO")
    result = ssd.process_file(tmp_path / "v.avi", file_args(), header="[1/1] v.avi")

    assert result == "skipped"
    assert probed == []
    assert "setting stream#" not in caplog.text
    assert len(caplog.records) == 1
    assert caplog.records[0].getMessage().startswith("\n[1/1] v.avi\n  v.avi: SKIP (AVI")
    assert "--avi-reorder" in caplog.text


def test_avi_reorder_moves_the_stereo_track_first(tmp_path, probed, caplog):
    caplog.set_level("INFO")
    result = ssd.process_file(tmp_path / "v.avi", file_args(avi_reorder=True))

    assert result == "changed"
    assert "moving stream#2 (eng, aac) to the first audio track" in caplog.text
    assert "as default audio" not in caplog.text
    cmd = caplog.text.split("[dry-run] ", 1)[1]
    assert cmd.index("-map 0:2") < cmd.index("-map 0:1")


def test_avi_reorder_leaves_a_file_whose_stereo_track_is_already_first(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    streams = [audio(1, 2, "aac", default=True), audio(2, 6, "eac3")]
    monkeypatch.setattr(ssd, "probe_audio_streams", lambda path: (streams, 100.0))

    result = ssd.process_file(tmp_path / "v.avi", file_args(avi_reorder=True))

    assert result == "unchanged"
    assert "already correct (stream#1 is first audio stream)" in caplog.text


@pytest.mark.parametrize("name", ["v.mkv", "v.mp4"])
def test_other_containers_set_the_default_flag(tmp_path, probed, caplog, name):
    caplog.set_level("INFO")
    result = ssd.process_file(tmp_path / name, file_args(avi_reorder=True))

    assert result == "changed"
    assert "setting stream#2 (eng, aac) as default audio" in caplog.text


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


def test_run_with_progress_leaves_progress_lines_out_of_the_output():
    code = ("print('Warning: early')\n"
            "for p in range(1, 101): print(f'pct={p}')")
    _, output = ssd.run_with_progress(python_cmd(code), "x", False, parse_pct_line)
    assert output.splitlines() == ["Warning: early"]


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


def test_run_with_progress_kills_a_subprocess_started_after_ctrl_c():
    ssd._cancelled.set()
    t0 = time.monotonic()
    returncode, _ = ssd.run_with_progress(SLOW_CMD, "x", False, parse_pct_line)
    assert returncode != 0
    assert time.monotonic() - t0 < 5
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


def make_videos(folder, count):
    for i in range(count):
        (folder / f"e{i:02}.mkv").write_text("x")


def test_ctrl_c_with_jobs_skips_files_that_havent_started(tmp_path, monkeypatch):
    """Leaving the thread pool waits for its workers, which kept taking
    queued files after Ctrl+C until the whole queue had been remuxed."""
    make_videos(tmp_path, 8)
    started = []

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        started.append(path.name)
        ssd._cancelled.set()
        time.sleep(0.1)
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--jobs", "2", "--no-progress"])

    ssd.main()

    assert 1 <= len(started) <= 2


def test_partial_summary_counts_unfinished_files_as_cancelled(tmp_path, monkeypatch, capsys):
    make_videos(tmp_path, 5)
    calls = []

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        calls.append(path.name)
        if len(calls) == 3:
            raise KeyboardInterrupt
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--no-progress"])

    with pytest.raises(SystemExit) as exit_info:
        ssd.main()

    out = capsys.readouterr().out
    assert exit_info.value.code == 130
    assert "Summary (partial -- interrupted)" in out
    assert "changed: 2" in out
    assert "cancelled: 3" in out


def test_stop_handler_cancels_and_stops_subprocesses(monkeypatch):
    stopped = []
    monkeypatch.setattr(ssd, "_terminate_active_procs", lambda: stopped.append(True))

    with pytest.raises(KeyboardInterrupt) as exc_info:
        ssd._stop_handler(signal.SIGTERM, None)

    assert exc_info.value.signum == signal.SIGTERM
    assert ssd._cancelled.is_set()
    assert stopped == [True]


@pytest.mark.parametrize("signum, code, message", [
    (signal.SIGINT, 130, "Interrupted by user (Ctrl+C)"),
    (signal.SIGTERM, 143, "Stopped by SIGTERM"),
], ids=["SIGINT", "SIGTERM"])
def test_signal_mid_run_prints_a_partial_summary(tmp_path, monkeypatch, capsys,
                                                 signum, code, message):
    make_videos(tmp_path, 5)
    calls = []

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        calls.append(path.name)
        if len(calls) == 3:
            signal.raise_signal(signum)
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--no-progress"])

    with pytest.raises(SystemExit) as exit_info:
        ssd.main()

    out = capsys.readouterr().out
    assert exit_info.value.code == code
    assert message in out
    assert "changed: 2" in out
    assert "cancelled: 3" in out
    assert len(calls) == 3


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



def test_log_file_gets_everything_but_the_console_only_warnings_and_errors(tmp_path, capsys):
    log_file = tmp_path / "run.log"
    ssd.setup_logging(str(log_file))

    ssd.log.info("detail")
    ssd.log.warning("careful")
    ssd.log.error("broken")

    out = capsys.readouterr().out
    assert "detail" not in out
    assert "careful" in out and "broken" in out
    logged = log_file.read_text()
    assert "detail" in logged and "careful" in logged and "broken" in logged


@pytest.mark.parametrize("make_files, expected", [
    (lambda folder: None, "No matching files found."),
    (lambda folder: (folder / "a.mp4").write_text("x"), "Missing required tool(s): ffmpeg, ffprobe"),
], ids=["no files", "missing tools"])
def test_fatal_errors_reach_the_console_with_a_log_file(tmp_path, monkeypatch, capsys,
                                                         make_files, expected):
    videos = tmp_path / "videos"
    videos.mkdir()
    make_files(videos)
    monkeypatch.setattr(ssd.shutil, "which", lambda tool: None)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(videos),
                                      "--log-file", str(tmp_path / "run.log")])

    with pytest.raises(SystemExit) as exit_info:
        ssd.main()

    assert exit_info.value.code == 1
    assert expected in capsys.readouterr().out
    assert expected in (tmp_path / "run.log").read_text()
