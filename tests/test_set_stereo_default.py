"""Tests for set_stereo_default.py. None of them need ffmpeg, ffprobe or
mkvmerge: anything that would call those tools is replaced with a stand-in,
and subprocess behavior is exercised with small Python child processes."""

import io
import json
import logging
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

real_mkvmerge_tracks = ssd.mkvmerge_tracks


@pytest.fixture(autouse=True)
def mkvmerge_ids(monkeypatch):
    """mkvmerge never runs in these tests, so mkvmerge_tracks() reports a
    video track (ID 0) and audio tracks with IDs 1 and 2, the indexes the
    ffprobe stand-ins use. Set mkvmerge_ids.value to other audio track IDs,
    or to None to make mkvmerge fail to read the file."""
    stand_in = types.SimpleNamespace(value=[1, 2])

    def tracks(path):
        if stand_in.value is None:
            return None
        return [(0, "video"), *((i, "audio") for i in stand_in.value)]
    monkeypatch.setattr(ssd, "mkvmerge_tracks", tracks)
    return stand_in


def audio(index, channels, codec="aac", language="eng", default=False, name="",
          comment=False, visual_impaired=False):
    """An audio stream the way probe_streams() describes it."""
    return ssd.Stream(index=index, type="audio", channels=channels, codec=codec,
                      language=language, names=(name,) if name else (), default=default,
                      comment=comment, visual_impaired=visual_impaired)


def stream(index, codec_type, default=0, codec="aac", channels=None, language=None):
    """A stream the way ffprobe's JSON reports it."""
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


def file_args(**overrides):
    """The parsed options process_file() and the apply functions take, as a
    --dry-run with no progress bar."""
    values = {"prefer_lang": None, "avi_reorder": False, "force": False, "dry_run": True,
              "backup": False, "keep_dates": False, "no_progress": True, "jobs": 1}
    values.update(overrides)
    return types.SimpleNamespace(**values)


def make_videos(folder, count):
    """Create count placeholder videos in folder: e00.mkv, e01.mkv, ... Only
    their names matter, since the tests that use them replace
    process_file()."""
    for i in range(count):
        (folder / f"e{i:02}.mkv").write_text("x")


def not_permitted(*args):
    """A stand-in for os.chown() on an NFS share that squashes root."""
    raise PermissionError(1, "Operation not permitted")


def test_choose_target_picks_the_only_stereo_track():
    target, note = ssd.choose_target([audio(1, 6), audio(2, 2)], None)
    assert target.index == 2 and note is None


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
    assert target.index == 2


def test_prefer_lang_that_matches_nothing_falls_back_to_the_current_language():
    streams = [audio(1, 6, language="fre", default=True), audio(2, 2, language="spa"),
               audio(3, 2, language="fre")]
    target, note = ssd.choose_target(streams, "eng")
    assert target.index == 3
    assert note == "no 2-channel track in 'eng', so picked as if --prefer-lang wasn't given"


def test_prefer_lang_fallback_picks_what_a_run_without_it_would():
    streams = [audio(1, 6, language="", default=True), audio(2, 2, language="spa")]
    target, note = ssd.choose_target(streams, "eng")
    assert target.index == 2 and "--prefer-lang wasn't given" in note
    assert ssd.choose_target(streams, None) == (target, None)


def test_prefer_lang_fallback_that_also_matches_nothing_names_both_languages():
    streams = [audio(1, 6, language="fre", default=True), audio(2, 2, language="spa")]
    target, note = ssd.choose_target(streams, "eng")
    assert target is None
    assert note.startswith("no 2-channel track in 'eng' or 'fre' [found stream#2 (spa/aac)]")


def test_prefer_lang_fallback_with_several_tracks_still_skips():
    streams = [audio(1, 6, language="", default=True), audio(2, 2, language="spa"),
               audio(3, 2, language="fre")]
    target, note = ssd.choose_target(streams, "eng")
    assert target is None
    assert note.startswith("no 2-channel track in 'eng', multiple 2-channel tracks found")


def test_prefer_lang_in_the_current_language_doesnt_fall_back():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="spa")]
    target, note = ssd.choose_target(streams, "en")
    assert target is None and note.startswith("no 2-channel track in 'en' [")


def test_several_tracks_in_the_prefer_lang_language_skip_instead_of_falling_back():
    streams = [audio(1, 6, language="fre", default=True), audio(2, 2, language="eng"),
               audio(3, 2, language="eng"), audio(4, 2, language="fre")]
    target, note = ssd.choose_target(streams, "eng")
    assert target is None
    assert note.startswith("multiple 2-channel tracks found [stream#2 (eng/aac), stream#3 (eng/aac)]")


@pytest.mark.parametrize("commentary", [
    audio(2, 2, comment=True),
    audio(2, 2, visual_impaired=True),
    audio(2, 2, name="Director's Commentary"),
    audio(2, 2, name="English Descriptive Audio"),
    audio(2, 2, name="Audio Description"),
    audio(2, 2, name="English (Audio-Description)"),
    audio(2, 2, name="Descriptive Video Service"),
    audio(2, 2, name="Described Video"),
    audio(2, 2, name="English [DVS]"),
], ids=["comment flag", "visual impaired flag", "commentary name", "descriptive name",
        "description name", "hyphenated description name", "dvs full name", "described name",
        "dvs abbreviation"])
def test_choose_target_never_picks_commentary_or_audio_description(commentary):
    target, note = ssd.choose_target([audio(1, 6, default=True), commentary], None)
    assert target is None and "commentary/audio description" in note


@pytest.mark.parametrize("name", ["Stereo (see description)", "No description", "Advsound Mix"])
def test_choose_target_picks_a_stereo_track_whose_name_only_looks_like_audio_description(name):
    target, _ = ssd.choose_target([audio(1, 6, default=True), audio(2, 2, name=name)], None)
    assert target.index == 2


def test_choose_target_picks_the_stereo_track_next_to_a_commentary():
    streams = [audio(1, 6, default=True), audio(2, 2, name="Commentary"), audio(3, 2)]
    target, _ = ssd.choose_target(streams, None)
    assert target.index == 3


def test_choose_target_skips_a_stereo_dub_in_another_language():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="spa")]
    target, note = ssd.choose_target(streams, None)
    assert target is None and "'eng'" in note and "spa" in note


def test_choose_target_uses_the_first_track_language_when_none_is_default():
    streams = [audio(1, 6, language="jpn"), audio(2, 2, language="eng"), audio(3, 2, language="jpn")]
    target, _ = ssd.choose_target(streams, None)
    assert target.index == 3


def test_choose_target_follows_the_default_track_language_between_stereo_tracks():
    streams = [audio(1, 6, language="spa", default=True), audio(2, 2, language="eng"),
               audio(3, 2, language="spa")]
    target, _ = ssd.choose_target(streams, None)
    assert target.index == 3


def test_prefer_lang_overrides_the_default_track_language():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="spa")]
    target, _ = ssd.choose_target(streams, "spa")
    assert target.index == 2


@pytest.mark.parametrize("lang", ["", "und"])
def test_untagged_stereo_track_matches_any_language(lang):
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language=lang)]
    target, _ = ssd.choose_target(streams, None)
    assert target.index == 2


def test_exact_language_match_wins_over_an_untagged_track():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="und"),
               audio(3, 2, language="eng")]
    target, _ = ssd.choose_target(streams, None)
    assert target.index == 3


@pytest.mark.parametrize("lang", ["", "und"])
def test_untagged_default_track_lets_a_stereo_track_in_any_language_through(lang):
    streams = [audio(1, 6, language=lang, default=True), audio(2, 2, language="spa")]
    target, note = ssd.choose_target(streams, None)
    assert target.index == 2 and note is None


def test_untagged_default_track_still_needs_a_single_stereo_track():
    streams = [audio(1, 6, language="", default=True), audio(2, 2, language="eng"),
               audio(3, 2, language="spa")]
    target, note = ssd.choose_target(streams, None)
    assert target is None and note.startswith("multiple 2-channel tracks found")


@pytest.mark.parametrize("code, expected", [
    ("de", "deu"), ("ger", "deu"), ("deu", "deu"), ("GER", "deu"), ("de-DE", "deu"),
    ("pt-BR", "por"), ("zh_Hant", "zho"), ("chi", "zho"), ("en", "eng"), ("eng", "eng"),
    ("und", "und"), ("", ""), (None, ""), ("haw", "haw"), ("english", "english"),
])
def test_normalize_language(code, expected):
    assert ssd.normalize_language(code) == expected


def test_language_aliases_cover_every_two_letter_code_and_bibliographic_code():
    """ISO 639-1 has 183 two-letter codes, and 20 languages have an ISO
    639-2/B code that differs from the /T one."""
    two_letter = [k for k in ssd.LANGUAGE_ALIASES if len(k) == 2]
    bibliographic = [k for k in ssd.LANGUAGE_ALIASES if len(k) == 3]
    assert len(two_letter) == 183
    assert len(bibliographic) == 20
    assert all(len(v) == 3 for v in ssd.LANGUAGE_ALIASES.values())
    assert not set(bibliographic) & set(ssd.LANGUAGE_ALIASES.values())


@pytest.mark.parametrize("prefer_lang", ["de", "ger", "deu", "DE", "de-AT"])
def test_prefer_lang_matches_however_the_language_is_written(prefer_lang):
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="ger"),
               audio(3, 2, language="eng")]
    target, _ = ssd.choose_target(streams, prefer_lang)
    assert target.index == 2


def test_tracks_tagged_differently_in_one_language_arent_treated_as_a_dub():
    streams = [audio(1, 6, language="ger", default=True), audio(2, 2, language="deu")]
    target, note = ssd.choose_target(streams, None)
    assert target.index == 2 and note is None


def test_skip_note_shows_the_language_as_given():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="spa")]
    _, note = ssd.choose_target(streams, "de")
    assert "no 2-channel track in 'de'" in note


@pytest.mark.parametrize("argv, paths", [
    (["--dry-run"], ["/videos"]),
    (["/videos/Some Show", "--dry-run"], ["/videos/Some Show"]),
], ids=["no path", "a path given"])
def test_paths_default_to_videos_in_the_docker_image(monkeypatch, argv, paths):
    monkeypatch.setenv(ssd.IN_DOCKER_VAR, "1")
    assert ssd.parse_args(argv).paths == paths


def test_a_path_is_required_outside_the_docker_image(capsys):
    """Outside the image there's no folder to assume, so a forgotten path
    is an error rather than a search of somewhere unexpected."""
    with pytest.raises(SystemExit) as exit_info:
        ssd.parse_args(["--dry-run"])

    assert exit_info.value.code == 2
    assert "give at least one path to process, or --input-file" in capsys.readouterr().err


SHOWS = ["Big Sky (2020)", "Billions", "Blue's Clues (1996)", "Black Bird", "Black Sails",
         "Blue Planet II", "Bloodline"]
"""Show folders for the --input-file tests, with the spaces, brackets and
apostrophes real names have."""


@pytest.fixture
def shows(tmp_path):
    """tmp_path/TV Shows/<each of SHOWS>, created. Returns the TV Shows folder."""
    folder = tmp_path / "TV Shows"
    for show in SHOWS:
        (folder / show).mkdir(parents=True)
    return folder


def test_input_file_reads_a_path_however_its_written(shows, tmp_path, monkeypatch):
    """Every way a list line can come: quoted as ls shows names on a
    terminal (with '\\'' for an apostrophe), double quoted, plain with
    spaces, plain with an apostrophe (which a shell would reject), with
    backslash-escaped spaces, relative to the list's folder, from ~, and
    with a missing path, which comes back unquoted so its warning is
    recognizable. Blank lines and comments are skipped; the list has a byte
    order mark and Windows line endings."""
    root = shows.as_posix()
    home = tmp_path / "home"
    (home / "Bloodline").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    lines = [
        "# Shows to fix",
        f"'{root}/Big Sky (2020)'",
        f'"{root}/Billions"',
        f"'{root}/Blue'\\''s Clues (1996)'",
        "",
        f"   {shows / 'Black Bird'}   ",
        "TV\\ Shows/Black\\ Sails",
        "TV Shows/Blue Planet II",
        str(shows / "Blue's Clues (1996)"),
        "~/Bloodline",
        f"'{root}/Missing Show'",
    ]
    listing = tmp_path / "shows.txt"
    listing.write_bytes("﻿".encode() + "\r\n".join(lines).encode("utf-8") + b"\r\n")

    found = [Path(p) for p in ssd.read_input_file(str(listing))]

    assert found == [shows / "Big Sky (2020)", shows / "Billions", shows / "Blue's Clues (1996)",
                     shows / "Black Bird", shows / "Black Sails", shows / "Blue Planet II",
                     shows / "Blue's Clues (1996)", home / "Bloodline", shows / "Missing Show"]


def test_input_file_from_standard_input_is_relative_to_the_current_folder(shows, monkeypatch):
    monkeypatch.chdir(shows)
    monkeypatch.setattr(ssd.sys, "stdin", types.SimpleNamespace(buffer=io.BytesIO(b"Billions\n'Black Bird'\n")))

    assert [Path(p) for p in ssd.read_input_file("-")] == [shows / "Billions", shows / "Black Bird"]


def test_input_file_paths_come_after_the_command_lines(shows):
    (shows / "shows.txt").write_text("Black Bird\nBillions\n", encoding="utf-8")

    args = ssd.parse_args([str(shows / "Bloodline"), "--input-file", str(shows / "shows.txt")])

    assert [Path(p) for p in args.paths] == [shows / "Bloodline", shows / "Black Bird",
                                             shows / "Billions"]


def test_an_input_file_that_cant_be_read_is_an_error(tmp_path, capsys):
    with pytest.raises(SystemExit) as exit_info:
        ssd.parse_args(["--input-file", str(tmp_path / "missing.txt")])

    assert exit_info.value.code == 2
    assert f"couldn't read --input-file {tmp_path / 'missing.txt'}" in capsys.readouterr().err


def test_an_input_file_replaces_the_docker_default(shows, monkeypatch):
    """With a list, only the list is processed, never the whole of /videos."""
    monkeypatch.setenv(ssd.IN_DOCKER_VAR, "1")
    (shows / "shows.txt").write_text("Billions\n", encoding="utf-8")

    args = ssd.parse_args(["--input-file", str(shows / "shows.txt"), "--dry-run"])

    assert [Path(p) for p in args.paths] == [shows / "Billions"]


@pytest.mark.parametrize("value", ["english", "xx", "", "e"])
def test_prefer_lang_rejects_something_that_isnt_a_language_code(tmp_path, monkeypatch, capsys,
                                                                 value):
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--prefer-lang", value])

    with pytest.raises(SystemExit) as exit_info:
        ssd.main()

    assert exit_info.value.code == 2
    assert "isn't a language code; use a 2- or 3-letter code such as en or eng" in capsys.readouterr().err


def test_jobs_must_be_at_least_1(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--jobs", "0"])

    with pytest.raises(SystemExit) as exit_info:
        ssd.main()

    assert exit_info.value.code == 2
    assert "--jobs must be >= 1" in capsys.readouterr().err


def test_existing_backups_needs_backup(tmp_path, monkeypatch, capsys):
    """Without --backup no backup is made, so --existing-backups would be
    silently ignored; most likely --backup was forgotten."""
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path),
                                      "--existing-backups", "replace"])

    with pytest.raises(SystemExit) as exit_info:
        ssd.main()

    assert exit_info.value.code == 2
    assert "--existing-backups only applies with --backup" in capsys.readouterr().err


@pytest.mark.parametrize("missing, need_mkvmerge, reported", [
    ([], True, None),
    (["mkvmerge"], False, None),
    (["mkvmerge"], True, "mkvmerge (install MKVToolNix)"),
    (["ffprobe"], False, "ffprobe"),
    (["ffmpeg", "ffprobe", "mkvmerge"], True, "ffmpeg, ffprobe, mkvmerge (install MKVToolNix)"),
], ids=["all there", "mkvmerge not needed", "mkvmerge needed", "ffprobe", "everything"])
def test_check_tools(monkeypatch, caplog, missing, need_mkvmerge, reported):
    monkeypatch.setattr(ssd.shutil, "which",
                        lambda tool: None if tool in missing else f"/usr/bin/{tool}")

    if reported is None:
        assert ssd.check_tools(need_mkvmerge) is True
        assert not caplog.records
    else:
        assert ssd.check_tools(need_mkvmerge) is False
        assert f"Missing required tool(s): {reported}" in caplog.text
        assert "MKVToolNix (https://mkvtoolnix.download)" in caplog.text


def test_probe_streams_reads_every_stream_and_the_commentary_flags(monkeypatch):
    probe = {"streams": [
        {"index": 0, "codec_type": "video", "codec_name": "h264", "disposition": {"default": 1}},
        {"index": 1, "codec_type": "audio", "channels": 2, "codec_name": "aac",
         "tags": {"language": "eng"},
         "disposition": {"default": 0, "comment": 1, "visual_impaired": 0}},
        {"index": 2, "codec_type": "audio", "channels": 2, "codec_name": "aac",
         "disposition": {"default": 1, "comment": 0, "visual_impaired": 1}},
    ], "format": {"duration": "60.0"}}
    monkeypatch.setattr(ssd, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=0, stdout=json.dumps(probe), stderr=""))

    streams, duration = ssd.probe_streams(Path("v.mkv"))

    assert [s.type for s in streams] == ["video", "audio", "audio"]
    assert [(s.comment, s.visual_impaired, s.default) for s in streams[1:]] == \
        [(True, False, False), (False, True, True)]
    assert duration == 60.0


@pytest.mark.parametrize("returncode, stdout, logged", [
    (1, "", "ffprobe failed on v.mkv: unreadable"),
    (0, "not json", "Could not parse ffprobe output for v.mkv"),
], ids=["ffprobe failed", "unreadable output"])
def test_probe_streams_logs_why_it_couldnt_read_a_file(monkeypatch, caplog, returncode, stdout,
                                                       logged):
    monkeypatch.setattr(ssd, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr="unreadable\n"))

    assert ssd.probe_streams(Path("v.mkv")) == (None, None)
    assert logged in caplog.text

    caplog.clear()
    assert ssd.probe_streams(Path("v.mkv"), report=False) == (None, None)
    assert caplog.text == ""


@pytest.mark.parametrize("tags, is_commentary", [
    ({"title": "Director's Commentary"}, True),
    ({"name": "Director's Commentary", "handler_name": "SoundHandler"}, True),
    ({"handler_name": "Audio Description"}, True),
    ({"title": "Stereo", "handler_name": "SoundHandler"}, False),
], ids=["mkv title", "mp4 name", "mp4 handler_name", "ordinary names"])
def test_commentary_is_found_by_any_track_name(monkeypatch, tags, is_commentary):
    probe = {"streams": [{"index": 1, "codec_type": "audio", "channels": 2, "codec_name": "aac",
                          "tags": dict(tags, language="eng"), "disposition": {}}]}
    monkeypatch.setattr(ssd, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=0, stdout=json.dumps(probe), stderr=""))

    streams, _ = ssd.probe_streams(Path("v.mp4"))

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
    """paths relative to root, sorted and with forward slashes, so results
    compare the same way on every system."""
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


@pytest.mark.parametrize("ext, found", [
    ("mkv", ["a.mkv"]),
    ("mkv, .MP4", ["a.mkv", "b.MP4"]),
    (".AVI,", ["c.avi"]),
], ids=["one", "spaces, dots and capitals", "trailing comma"])
def test_ext_takes_extensions_however_theyre_written(tmp_path, ext, found):
    for name in ("a.mkv", "b.MP4", "c.avi", "d.webm"):
        (tmp_path / name).write_bytes(b"x")
    args = types.SimpleNamespace(paths=[str(tmp_path)], ext=ext, no_recursive=False,
                                 skip_symlinks=False, follow_symlinks=False)
    assert [p.name for p in ssd.find_files(args)] == found


def test_files_come_path_by_path_in_the_order_given(tmp_path):
    """Listing one show before another processes it first; within each
    path, files are sorted. A file reached again later isn't repeated."""
    for name in ["Show B/e02.mkv", "Show B/e01.mkv", "Show A/e02.mkv", "Show A/e01.mkv"]:
        (tmp_path / name).parent.mkdir(exist_ok=True)
        (tmp_path / name).write_text("x")
    paths = [tmp_path / "Show B", tmp_path / "Show A", tmp_path / "Show B" / "e01.mkv"]
    args = types.SimpleNamespace(paths=paths, ext=None, no_recursive=False, skip_symlinks=False,
                                 follow_symlinks=False)

    found = [p.relative_to(tmp_path).as_posix() for p in ssd.find_files(args)]

    assert found == ["Show B/e01.mkv", "Show B/e02.mkv", "Show A/e01.mkv", "Show A/e02.mkv"]


def test_iter_files_accepts_files_passed_directly(library):
    paths = [library / "a.mkv", library / "a.mkv.tmp_remux.mkv", library / "notes.nfo"]
    assert names(ssd.iter_files(paths, ssd.DEFAULT_EXTS, recursive=True), library) == ["a.mkv"]


def test_iter_files_warns_about_leftover_temp_files(library, caplog):
    list(ssd.iter_files([library], ssd.DEFAULT_EXTS, recursive=True))
    assert "a.mkv.tmp_remux.mkv" in caplog.text
    assert "safe to delete" in caplog.text


@pytest.mark.parametrize("in_docker, mounted, hinted", [
    (True, False, True),
    (True, True, False),
    (False, False, False),
], ids=["docker, nothing mounted", "docker, mounted but no such subfolder", "not docker"])
def test_a_forgotten_docker_mount_gets_the_fix(tmp_path, monkeypatch, caplog, in_docker, mounted,
                                               hinted):
    """A missing path under /videos in the image usually means the -v was
    forgotten, so the warning says how to mount it. If something is
    mounted there, it's just a mistyped path."""
    videos = tmp_path / "videos"
    if mounted:
        videos.mkdir()
    monkeypatch.setattr(ssd, "DOCKER_VIDEOS", str(videos))
    if in_docker:
        monkeypatch.setenv(ssd.IN_DOCKER_VAR, "1")

    assert list(ssd.iter_files([videos / "Some Show"], ssd.DEFAULT_EXTS, recursive=True)) == []

    assert f"Skipping {videos / 'Some Show'}: no such file or directory" in caplog.text
    assert (f"Nothing is mounted at {videos}: add -v \"/path/to/videos:{videos}\" to docker run"
            in caplog.text) is hinted


def test_iter_files_warns_about_a_path_that_doesnt_exist(library, caplog):
    typo = library.parent / "vidoes"

    found = ssd.iter_files([typo, library], ssd.DEFAULT_EXTS, recursive=True)

    assert names(found, library) == ["Season 01/c.mkv", "a.mkv", "b.MP4"]
    assert f"Skipping {typo}: no such file or directory" in caplog.text


@pytest.mark.parametrize("name", [
    "-dash.mkv",
    "@at.mkv",
    pytest.param("Movie:Part2.mp4", marks=pytest.mark.skipif(
        sys.platform == "win32", reason="Windows file names can't contain a colon")),
])
def test_files_found_from_a_relative_path_are_absolute(tmp_path, monkeypatch, name):
    """Run on ".", a file named like an option (-, @) or a protocol (a colon)
    must still come out as a path no tool can misread."""
    (tmp_path / name).write_text("x")
    monkeypatch.chdir(tmp_path)

    found = list(ssd.iter_files(["."], ssd.DEFAULT_EXTS, recursive=True))

    assert found == [Path.cwd() / name]
    assert found[0].is_absolute()


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a named pipe (not on Windows)")
def test_iter_files_warns_about_a_path_that_isnt_a_file_or_folder(tmp_path, caplog):
    pipe = tmp_path / "pipe.mkv"
    os.mkfifo(pipe)

    assert list(ssd.iter_files([pipe], ssd.DEFAULT_EXTS, recursive=True)) == []
    assert f"Skipping {pipe}: not a file or directory" in caplog.text


def test_iter_files_warns_about_a_folder_it_cant_open(library, monkeypatch, caplog):
    """A folder that can't be opened (no permission, a network share that
    dropped) must be reported, or its files would quietly be missing from
    the run. The folder is made unreadable by failing os.scandir() for it,
    which works the same on every platform and when run as root."""
    real_scandir = os.scandir
    locked = library / "Season 01"

    def scandir(path="."):
        if os.path.abspath(path) == str(locked):
            raise PermissionError(13, "Permission denied", str(path))
        return real_scandir(path)
    monkeypatch.setattr(os, "scandir", scandir)

    found = names(ssd.iter_files([library], ssd.DEFAULT_EXTS, recursive=True), library)

    assert found == ["a.mkv", "b.MP4"]
    assert f"Couldn't search {locked}: Permission denied" in caplog.text
    assert [r.levelname for r in caplog.records if "Couldn't search" in r.message] == ["WARNING"]


def test_a_mistyped_path_is_reported_on_the_console_and_the_rest_still_run(tmp_path, monkeypatch,
                                                                         capsys):
    """The warning must show even with --log-file, where only warnings and
    errors reach the console."""
    videos = tmp_path / "videos"
    videos.mkdir()
    make_videos(videos, 2)
    typo = tmp_path / "vidoes"
    processed = []

    def fake_process_file(path, args, position=0, on_progress=None):
        processed.append(path.name)
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(typo), str(videos),
                                      "--no-progress", "--log-file", str(tmp_path / "run.log")])

    ssd.main()

    assert f"Skipping {typo}: no such file or directory" in capsys.readouterr().out
    assert sorted(processed) == ["e00.mkv", "e01.mkv"]


@pytest.fixture
def linked(tmp_path):
    """A library folder full of symlinks, next to the real files they point
    to. The path is fully resolved, since macOS keeps temp folders behind a
    symlink (/var -> /private/var).

        real/movie.mkv, real/notes.txt, real/show/ep1.mkv
        lib/own.mkv                       an ordinary file
        lib/movie.mkv, lib/again.mkv  ->  real/movie.mkv
        lib/notes.mkv                 ->  real/notes.txt
        lib/broken.mkv                ->  real/missing.mkv
        lib/show                      ->  real/show
        lib/loop                      ->  lib
    """
    root = tmp_path.resolve()
    for name in ["real/movie.mkv", "real/notes.txt", "real/show/ep1.mkv", "lib/own.mkv"]:
        (root / name).parent.mkdir(parents=True, exist_ok=True)
        (root / name).write_text("x")
    links = {"lib/movie.mkv": "real/movie.mkv", "lib/again.mkv": "real/movie.mkv",
             "lib/notes.mkv": "real/notes.txt", "lib/broken.mkv": "real/missing.mkv",
             "lib/show": "real/show", "lib/loop": "lib"}
    try:
        for link, target in links.items():
            (root / link).symlink_to(root / target, target_is_directory=(root / target).is_dir())
    except OSError as exc:
        pytest.skip(f"can't create symlinks here: {exc}")
    return root


def test_symlinked_files_are_found_as_the_file_they_point_to(linked, caplog):
    caplog.set_level("INFO")
    found = list(ssd.iter_files([linked / "lib"], ssd.DEFAULT_EXTS, recursive=True))

    assert names(found, linked) == ["lib/own.mkv", "real/movie.mkv"]
    assert "Not searching symlinked folder (use --follow-symlinks)" in caplog.text
    assert str(Path("lib") / "show") in caplog.text
    assert "symlink to a file without a video extension" in caplog.text


def test_skip_symlinks_leaves_linked_files_out(linked, caplog):
    caplog.set_level("INFO")
    found = ssd.iter_files([linked / "lib"], ssd.DEFAULT_EXTS, recursive=True, skip_symlinks=True)

    assert names(found, linked) == ["lib/own.mkv"]
    assert "Skipping symlink (--skip-symlinks)" in caplog.text


def test_follow_symlinks_searches_linked_folders_once_each(linked):
    found = ssd.iter_files([linked / "lib"], ssd.DEFAULT_EXTS, recursive=True,
                           follow_symlinks=True)

    assert names(found, linked) == ["lib/own.mkv", "lib/show/ep1.mkv", "real/movie.mkv"]


def test_a_symlinked_folder_named_on_the_command_line_is_searched(linked):
    (linked / "shortcut").symlink_to(linked / "real" / "show", target_is_directory=True)

    found = ssd.iter_files([linked / "shortcut"], ssd.DEFAULT_EXTS, recursive=True)

    assert names(found, linked) == ["shortcut/ep1.mkv"]


def test_a_file_given_more_than_once_is_found_once(linked, monkeypatch):
    monkeypatch.chdir(linked)
    paths = ["lib/own.mkv", linked / "lib" / "own.mkv", "lib/movie.mkv", "real/movie.mkv"]

    found = ssd.iter_files(paths, ssd.DEFAULT_EXTS, recursive=True)

    assert sorted(str(p.resolve()) for p in found) == [str(linked / "lib" / "own.mkv"),
                                                        str(linked / "real" / "movie.mkv")]


def test_make_backup_hard_links_and_replaces_a_stale_backup(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    bak = tmp_path / "v.mkv.bak"
    bak.write_bytes(b"stale")

    assert ssd.make_backup(video, replace=True) == bak

    assert bak.read_bytes() == b"original"
    assert os.stat(bak).st_ino == os.stat(video).st_ino


def test_make_backup_numbers_the_new_backup_instead_of_replacing(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    for name, data in [("v.mkv.bak", b"first"), ("v.mkv.bak.1", b"second")]:
        (tmp_path / name).write_bytes(data)

    assert ssd.make_backup(video) == tmp_path / "v.mkv.bak.2"

    assert (tmp_path / "v.mkv.bak").read_bytes() == b"first"
    assert (tmp_path / "v.mkv.bak.1").read_bytes() == b"second"
    assert (tmp_path / "v.mkv.bak.2").read_bytes() == b"original"


def test_make_backup_uses_plain_bak_when_its_free(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")

    assert ssd.make_backup(video) == tmp_path / "v.mkv.bak"


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


@pytest.mark.parametrize("answers, expected", [
    (["d"], "replace"),
    (["N"], "number"),
    (["q"], "quit"),
    (["", "maybe", " n "], "number"),
    ([EOFError], "number"),
], ids=["delete", "number", "quit", "asks again until it understands", "ctrl+d"])
def test_ask_about_existing_backups(monkeypatch, answers, expected):
    """End of input numbers new backups, as when no one can be asked, rather
    than quitting: on Windows a run started with input from NUL gets end of
    input straight away, and quitting would end every such run doing
    nothing while reporting success."""
    questions = []

    def fake_input(prompt):
        questions.append(prompt)
        answer = answers[len(questions) - 1]
        if answer is EOFError:
            raise EOFError
        return answer
    monkeypatch.setattr("builtins.input", fake_input)

    assert ssd.ask_about_existing_backups(3) == expected
    assert questions[0] == ("3 file(s) already have a backup. If they're changed: [d]elete and "
                            "replace the old backup, [n]umber the new one (.bak.1, .bak.2...), "
                            "or [q]uit? ")
    assert len(questions) == len(answers)


@pytest.fixture
def backed_up(tmp_path, monkeypatch):
    """Two videos, one of which already has a .bak, with process_file()
    replaced by a stand-in. Returns (folder, run), where run(*options,
    tty=..., answer=...) runs main() and returns (exit code or None, the
    backup setting each file was processed with, questions asked). An
    exception as the answer is raised at the question instead."""
    make_videos(tmp_path, 2)
    (tmp_path / "e00.mkv.bak").write_text("old")

    def run(*options, tty=True, answer="n"):
        seen, questions = [], []

        def fake_process_file(path, args, position=0, on_progress=None):
            seen.append(args.backup)
            return "changed"

        def fake_input(prompt):
            questions.append(prompt)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
        monkeypatch.setattr(ssd, "process_file", fake_process_file)
        monkeypatch.setattr(ssd, "_can_ask", lambda: tty)
        monkeypatch.setattr("builtins.input", fake_input)
        monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path),
                                          "--no-progress", *options])
        return ssd.main(), seen, questions
    return tmp_path, run


@pytest.mark.parametrize("answer, mode", [("d", "replace"), ("n", "number")])
def test_existing_backups_are_asked_about_once(backed_up, answer, mode):
    _, run = backed_up
    code, seen, questions = run("--backup", answer=answer)

    assert code == 0
    assert len(questions) == 1 and questions[0].startswith("1 file(s) already have a backup")
    assert seen == [mode, mode]


def test_quitting_at_the_backup_question_changes_nothing(backed_up, capsys):
    _, run = backed_up
    code, seen, _ = run("--backup", answer="q")

    assert code == 0
    assert seen == []
    assert "Quit before changing any files." in capsys.readouterr().out


@pytest.mark.parametrize("stop, code, message", [
    (KeyboardInterrupt(), 130, "Interrupted by user (Ctrl+C). No files were changed."),
    (ssd.Stopped(signal.SIGTERM), 143, "Stopped by SIGTERM. No files were changed."),
], ids=["Ctrl+C", "SIGTERM"])
def test_stopping_at_the_backup_question_changes_nothing(backed_up, capsys, stop, code, message):
    _, run = backed_up
    exit_code, seen, questions = run("--backup", answer=stop)

    assert exit_code == code
    assert len(questions) == 1
    assert seen == []
    assert message in capsys.readouterr().out


def test_existing_backups_are_numbered_when_no_one_can_answer(backed_up, capsys):
    _, run = backed_up
    code, seen, questions = run("--backup", tty=False)

    assert code == 0
    assert questions == []
    assert seen == ["number", "number"]
    assert "Use --existing-backups to choose" in capsys.readouterr().out


@pytest.mark.parametrize("stdin_tty, stdout_tty, expected", [
    (True, True, True),
    (True, False, False),
    (False, True, False),
], ids=["both terminals", "output piped", "input redirected"])
def test_can_ask_only_with_a_terminal_for_input_and_output(monkeypatch, stdin_tty, stdout_tty,
                                                          expected):
    """Input alone isn't enough: Windows reports NUL as a terminal, so a run
    started with input from NUL would otherwise be asked a question no one
    can answer."""
    monkeypatch.setattr(ssd.sys, "stdin", types.SimpleNamespace(isatty=lambda: stdin_tty))
    monkeypatch.setattr(ssd.sys, "stdout", types.SimpleNamespace(isatty=lambda: stdout_tty))
    assert ssd._can_ask() is expected


@pytest.mark.parametrize("mode", ["replace", "number"])
def test_existing_backups_option_skips_the_question(backed_up, mode):
    _, run = backed_up
    _, seen, questions = run("--backup", "--existing-backups", mode)

    assert questions == []
    assert seen == [mode, mode]


@pytest.mark.parametrize("options", [["--backup", "--dry-run"], []], ids=["dry run", "no --backup"])
def test_no_backup_question_when_no_backup_will_be_made(backed_up, options):
    _, run = backed_up
    _, seen, questions = run(*options)

    assert questions == []
    assert len(seen) == 2


def test_no_backup_question_without_existing_backups(backed_up):
    folder, run = backed_up
    (folder / "e00.mkv.bak").unlink()
    _, seen, questions = run("--backup")

    assert questions == []
    assert seen == ["number", "number"]


@pytest.fixture
def fake_ffprobe(monkeypatch):
    """Replace run() with a stand-in ffprobe. Set layouts[path] to the
    streams it should report for that file, to (streams, duration in
    seconds) to report a duration too, or to None to make it fail."""
    layouts = {}

    def fake_run(cmd, **kw):
        layout = layouts[cmd[-1]]
        if layout is None:
            return types.SimpleNamespace(returncode=1, stdout="", stderr="unreadable")
        streams, duration = layout if isinstance(layout, tuple) else (layout, None)
        data = {"streams": streams}
        if duration is not None:
            data["format"] = {"duration": f"{duration:.6f}"}
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")
    monkeypatch.setattr(ssd, "run", fake_run)
    return layouts


ORIGINAL_LAYOUT = [stream(0, "video", 1, "h264"), stream(1, "audio", 1, "eac3", 6, "eng"),
                   stream(2, "audio", 0, "aac", 2, "eng"), stream(3, "subtitle")]


ORIGINAL_AUDIO = [audio(1, 6, "eac3", default=True), audio(2, 2, "aac")]


REMUX = str(Path("v.mkv" + ssd.TMP_MARKER + ".mkv"))


def plan_to_check(duration=None):
    """The Plan _process_file() would make for v.mkv: ORIGINAL_LAYOUT's
    streams, of which ORIGINAL_AUDIO are the audio ones, making stream 2
    default. verify_remux() probes its remux, REMUX."""
    return ssd.Plan(Path("v.mkv"), ORIGINAL_AUDIO, 2, duration,
                    layout=[ssd._stream_info(s) for s in ORIGINAL_LAYOUT])


@pytest.mark.parametrize("remuxed, expected", [
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 0, "eac3", 6),
      stream(2, "audio", 1, "aac", 2), stream(3, "subtitle")], None),
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 0, "eac3", 6),
      stream(2, "audio", 1, "aac", 2)], "stream count changed from 4 to 3"),
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 1, "eac3", 6),
      stream(2, "audio", 0, "aac", 2), stream(3, "subtitle")], "default flag is on stream#1,"),
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 1, "eac3", 6),
      stream(2, "audio", 1, "aac", 2), stream(3, "subtitle")], "stream#1, stream#2"),
    ([stream(0, "video", 1, "h264"), stream(1, "audio", 0, "eac3", 6),
      stream(2, "data", 0, "bin_data"), stream(3, "subtitle")], "expected 2 audio tracks, found 1"),
    (None, "couldn't read"),
], ids=["good", "stream dropped", "flag not moved", "both default", "audio track lost",
        "unreadable"])
def test_verify_remux(fake_ffprobe, remuxed, expected):
    fake_ffprobe[REMUX] = remuxed
    problem = ssd.verify_remux(plan_to_check(), reordered=False)
    if expected is None:
        assert problem is None
    else:
        assert expected in problem


def test_verify_remux_avi_reorder(fake_ffprobe):
    fake_ffprobe[REMUX] = [stream(0, "video", 0, "h264"), stream(1, "audio", 0, "aac", 2, "eng"),
                           stream(2, "audio", 0, "eac3", 6, "eng"), stream(3, "subtitle")]
    assert ssd.verify_remux(plan_to_check(), reordered=True) is None

    fake_ffprobe[REMUX] = ORIGINAL_LAYOUT
    assert "didn't end up first" in ssd.verify_remux(plan_to_check(), reordered=True)


@pytest.mark.parametrize("tagged, accepted", [("en", True), ("ENG", True), ("spa", False)])
def test_verify_remux_avi_reorder_compares_languages_however_theyre_written(fake_ffprobe, tagged,
                                                                            accepted):
    """The target is tagged "eng"; the remux's first track may say the same
    language another way."""
    fake_ffprobe[REMUX] = [stream(0, "video", 0, "h264"), stream(1, "audio", 0, "aac", 2, tagged),
                           stream(2, "audio", 0, "eac3", 6, "eng"), stream(3, "subtitle")]
    problem = ssd.verify_remux(plan_to_check(), reordered=True)
    assert (problem is None) is accepted


def described(index, codec_type, codec, default=0, channels=None, language="eng", title=None,
              flags=()):
    """A stream the way ffprobe's JSON reports it, with a name and flags."""
    s = stream(index, codec_type, default, codec, channels, language)
    s["disposition"].update(dict.fromkeys(flags, 1))
    if title:
        s.setdefault("tags", {})["title"] = title
    return s


def rich_layout(**changes):
    """How ffprobe reports a correctly remuxed file: a video, a 5.1 track, a
    stereo track (now the default), a commentary track and a forced
    subtitle, with names and languages. rich_plan() describes the same file
    before the remux. changes replaces streams by index, e.g.
    rich_layout(stream3=...), to make the remux lose or change something."""
    streams = [described(0, "video", "h264", 1),
               described(1, "audio", "eac3", 0, 6, title="Surround"),
               described(2, "audio", "aac", 1, 2, title="Stereo"),
               described(3, "audio", "aac", 0, 2, title="Director", flags=("comment",)),
               described(4, "subtitle", "subrip", 0, title="Signs", flags=("forced",))]
    for key, value in changes.items():
        streams[int(key.removeprefix("stream"))] = value
    return streams


def rich_plan(name):
    """The Plan for name (e.g. "v.mkv") with rich_layout()'s original
    streams, making stream 2 default. Returns (plan, its remux's path)."""
    original = rich_layout(stream1=described(1, "audio", "eac3", 1, 6, title="Surround"),
                           stream2=described(2, "audio", "aac", 0, 2, title="Stereo"))
    layout = [ssd._stream_info(s) for s in original]
    plan = ssd.Plan(Path(name), [s for s in layout if s.type == "audio"], 2, layout=layout)
    return plan, str(plan.tmp_path)


@pytest.mark.parametrize("remuxed, problem", [
    (rich_layout(), None),
    (rich_layout(stream4=described(4, "subtitle", "subrip", title="Signs", flags=("forced", "dub"))),
     None),
    (rich_layout(stream3=described(3, "audio", "aac", 0, 2, title="Director", flags=("comment",),
                                   language="und")), None),
    (rich_layout(stream3=described(3, "audio", "aac", 0, 2, title="Director", flags=("comment",),
                                   language="en")), None),
    (rich_layout(stream1=described(1, "audio", "ac3", 0, 6, title="Surround")),
     "stream#1 changed from audio eac3 6ch to audio ac3 6ch"),
    (rich_layout(stream4=described(4, "subtitle", "subrip", language="spa", title="Signs",
                                   flags=("forced",))),
     "stream#4's language changed from eng to spa"),
    (rich_layout(stream2=described(2, "audio", "aac", 1, 2)), "stream#2 lost its name 'Stereo'"),
], ids=["identical", "a flag added", "language now unknown", "language written another way",
        "codec changed", "language changed", "name lost"])
def test_verify_remux_checks_every_stream_came_through(fake_ffprobe, remuxed, problem):
    plan, remux = rich_plan("v.mkv")
    fake_ffprobe[remux] = remuxed
    assert ssd.verify_remux(plan, reordered=False) == problem


def test_verify_remux_rejects_a_flag_lost_from_an_mkv_file(fake_ffprobe):
    """mkvmerge 52 and older drop the commentary flag, which mkvmerge 54 and
    newer keep, so the remux is rejected and the fix named."""
    plan, remux = rich_plan("v.mkv")
    fake_ffprobe[remux] = rich_layout(stream3=described(3, "audio", "aac", 0, 2, title="Director"))
    assert ssd.verify_remux(plan, reordered=False) == (
        "stream#3 lost its comment flag; mkvmerge 52 and older drop it, so update MKVToolNix "
        "to 54 or newer")


def test_verify_remux_warns_about_a_flag_lost_from_an_mp4_file(fake_ffprobe, caplog):
    """ffmpeg can't write the flag to MP4 files at all, so rejecting the
    remux would leave the file unfixable: it's used, with a warning."""
    plan, remux = rich_plan("v.mp4")
    fake_ffprobe[remux] = rich_layout(
        stream3=described(3, "audio", "aac", 0, 2, title="Director"),
        stream4=described(4, "subtitle", "subrip", title="Signs"))
    assert ssd.verify_remux(plan, reordered=False) is None
    assert [r.getMessage() for r in caplog.records if r.levelname == "WARNING"] == [
        "    v.mp4: stream#3 lost its comment flag, which ffmpeg can't write to .mp4 files",
        "    v.mp4: stream#4 lost its forced flag, which ffmpeg can't write to .mp4 files"]


def test_stream_info_reads_every_flag_but_default():
    raw = described(4, "subtitle", "subrip", 1, flags=("forced", "hearing_impaired"))
    assert ssd._stream_info(raw).flags == {"forced", "hearing_impaired"}


REMUXED_LAYOUT = [stream(0, "video", 1, "h264"), stream(1, "audio", 0, "eac3", 6),
                  stream(2, "audio", 1, "aac", 2), stream(3, "subtitle")]


@pytest.mark.parametrize("before, after, rejected", [
    (2400.0, 2400.02, False),
    (2400.0, 2377.0, False),
    (2400.0, 2375.0, True),
    (2400.0, 1200.0, True),
    (30.0, 29.1, False),
    (30.0, 28.9, True),
    (1.0, 0.5, False),
    (2400.0, 2500.0, False),
    (None, 1200.0, False),
    (2400.0, None, False),
], ids=["normal drift", "just under 1%", "just over 1%", "half missing",
        "short clip within 1s", "short clip over 1s", "1s clip", "longer",
        "original duration unknown", "remux duration unknown"])
def test_verify_remux_rejects_a_remux_much_shorter_than_the_original(fake_ffprobe, before, after,
                                                                     rejected):
    fake_ffprobe[REMUX] = (REMUXED_LAYOUT, after)

    problem = ssd.verify_remux(plan_to_check(before), reordered=False)

    if rejected:
        assert problem == (f"duration dropped from {before:.1f}s to {after:.1f}s; "
                           f"the original may be incomplete")
    else:
        assert problem is None


def test_a_changed_file_is_probed_once_and_its_remux_once(tmp_path, monkeypatch):
    """The decision and the check share one probe of the original."""
    video = tmp_path / "v.mp4"
    video.write_bytes(b"original")
    probed = []

    def fake_ffprobe(cmd, **kw):
        probed.append(Path(cmd[-1]).name)
        layout = ORIGINAL_LAYOUT if cmd[-1] == str(video) else REMUXED_LAYOUT
        data = {"streams": layout, "format": {"duration": "60.0"}}
        return types.SimpleNamespace(returncode=0, stdout=json.dumps(data), stderr="")

    def fake_remux(cmd, *args, **kwargs):
        Path(cmd[-1]).write_bytes(b"remuxed")
        return 0, ""
    monkeypatch.setattr(ssd, "run", fake_ffprobe)
    monkeypatch.setattr(ssd, "run_with_progress", fake_remux)

    assert ssd.process_file(video, file_args(dry_run=False)) == "changed"
    assert probed == ["v.mp4", "v.mp4" + ssd.TMP_MARKER + ".mp4"]
    assert video.read_bytes() == b"remuxed"


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


OLD_TIMES_NS = (1_577_880_000_000_000_000, 1_577_890_000_123_456_000)


@pytest.mark.parametrize("keep_dates", [True, False])
def test_swap_in_keeps_the_original_dates_only_when_asked(tmp_path, monkeypatch, keep_dates):
    """Only the modification time is compared. The access time is copied
    too, but the system updates it whenever anything (an indexer,
    antivirus) reads the file, which can happen even before swap_in()
    runs, so its exact value can't be relied on here."""
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    os.utime(video, ns=OLD_TIMES_NS)
    tmp = tmp_path / "v.mkv.tmp_remux.mkv"
    tmp.write_bytes(b"remuxed")
    set_mtimes = []
    real_utime = os.utime

    def recording_utime(path, *args, ns=None, **kwargs):
        set_mtimes.append(ns[1])
        return real_utime(path, *args, ns=ns, **kwargs)
    monkeypatch.setattr(ssd.os, "utime", recording_utime)

    ssd.swap_in(video, tmp, backup=False, keep_dates=keep_dates)

    assert video.read_bytes() == b"remuxed"
    if keep_dates:
        assert set_mtimes == [OLD_TIMES_NS[1]]
        assert video.stat().st_mtime_ns == OLD_TIMES_NS[1]
    else:
        assert set_mtimes == []
        assert video.stat().st_mtime_ns > OLD_TIMES_NS[1]


def test_keep_dates_leaves_the_backup_with_the_original_dates(tmp_path):
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    os.utime(video, ns=OLD_TIMES_NS)
    tmp = tmp_path / "v.mkv.tmp_remux.mkv"
    tmp.write_bytes(b"remuxed")

    ssd.swap_in(video, tmp, backup="number", keep_dates=True)

    bak = tmp_path / "v.mkv.bak"
    assert bak.read_bytes() == b"original"
    assert bak.stat().st_mtime_ns == video.stat().st_mtime_ns == OLD_TIMES_NS[1]


def test_a_numbered_backup_is_announced(tmp_path, caplog):
    """When <name>.bak is taken, the new backup's name is logged, so it can
    be found."""
    caplog.set_level("INFO")
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    (tmp_path / "v.mkv.bak").write_bytes(b"older backup")
    tmp = tmp_path / "v.mkv.tmp_remux.mkv"
    tmp.write_bytes(b"remuxed")

    ssd.swap_in(video, tmp, backup="number")

    assert (tmp_path / "v.mkv.bak.1").read_bytes() == b"original"
    assert "v.mkv: kept the original as v.mkv.bak.1" in caplog.text


@pytest.fixture
def nfs_owners(tmp_path, monkeypatch):
    """Makes every original look owned by tronyx:users (1000:100) and every
    new file by nobody:nogroup (65534:65534), as on an NFS share that turns
    root into nobody. Returns make(name) -> (video, temp file); chown calls
    are recorded in the list returned alongside."""
    def owner(path):
        return (65534, 65534) if ssd.TMP_MARKER in str(path) else (1000, 100)
    users = {1000: "tronyx", 65534: "nobody"}
    groups = {100: "users", 65534: "nogroup"}

    def lookup(names, attr):
        def get(i):
            if i not in names:
                raise KeyError(i)
            return types.SimpleNamespace(**{attr: names[i]})
        return get
    monkeypatch.setattr(ssd, "_owner", owner)
    monkeypatch.setattr(ssd, "pwd", types.SimpleNamespace(getpwuid=lookup(users, "pw_name")))
    monkeypatch.setattr(ssd, "grp", types.SimpleNamespace(getgrgid=lookup(groups, "gr_name")))
    calls = []
    monkeypatch.setattr(ssd.os, "chown", lambda *args: calls.append(args), raising=False)

    def make(name):
        video = tmp_path / name
        video.write_bytes(b"original")
        tmp = tmp_path / (name + ssd.TMP_MARKER + ".mkv")
        tmp.write_bytes(b"remuxed")
        return video, tmp
    return make, calls


def test_swap_in_gives_the_new_file_the_original_owner(nfs_owners):
    make, calls = nfs_owners
    video, tmp = make("v.mkv")

    ssd.swap_in(video, tmp, backup=False)

    assert calls == [(tmp, 1000, 100)]


def test_swap_in_leaves_the_owner_alone_when_it_already_matches(tmp_path, monkeypatch, caplog):
    """e.g. an NFS share that maps every user to the media owner, where
    changing the owner fails but isn't needed, so there's nothing to warn
    about."""
    monkeypatch.setattr(ssd.os, "chown", not_permitted, raising=False)
    monkeypatch.setattr(ssd, "_owner", lambda path: (1000, 100))
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    tmp = tmp_path / "v.mkv.tmp_remux.mkv"
    tmp.write_bytes(b"remuxed")

    ssd.swap_in(video, tmp, backup=False)

    assert video.read_bytes() == b"remuxed"
    assert "Couldn't give" not in caplog.text


@pytest.mark.skipif(sys.platform == "win32", reason="Windows only has a read-only flag, not Unix permissions")
def test_copy_ownership_without_chown_still_copies_permissions(tmp_path, monkeypatch):
    """Without os.chown (Windows), only the permissions can be copied."""
    monkeypatch.delattr(ssd.os, "chown", raising=False)
    src, dst = tmp_path / "src", tmp_path / "dst"
    src.write_bytes(b"x")
    dst.write_bytes(b"x")
    src.chmod(0o640)
    dst.chmod(0o600)

    ssd.copy_ownership(src, dst)

    assert dst.stat().st_mode & 0o777 == 0o640
    assert not ssd._ownership_failures


def test_owner_failures_are_reported_once_at_the_end_with_a_list(nfs_owners, monkeypatch,
                                                                  tmp_path, caplog):
    """Swapping a file in doesn't warn by itself: the cause affects the
    whole run, so report_ownership_failures() warns once, with a count and
    a file listing every affected file by its full path."""
    make, _ = nfs_owners
    monkeypatch.setattr(ssd.os, "chown", not_permitted, raising=False)
    videos = []
    for name in ("a.mkv", "b.mkv"):
        video, tmp = make(name)
        ssd.swap_in(video, tmp, backup=False)
        assert video.read_bytes() == b"remuxed"
        videos.append(video)
    assert "Couldn't give" not in caplog.text
    listing = tmp_path / "lists"
    listing.mkdir()

    ssd.report_ownership_failures(listing)

    [listed] = listing.glob("set_stereo_default-owners-*.log")
    assert listed.read_text(encoding="utf-8").splitlines() == [str(v) for v in videos]
    warnings = [r.getMessage() for r in caplog.records if r.levelname == "WARNING"]
    assert warnings == [(
        "\nCouldn't give 2 remuxed files their original owner (Operation not permitted). "
        f"You can view the full list of files here: {listed.resolve()}\n\n"
        "These files should belong to tronyx:users (1000:100) but belong to "
        "nobody:nogroup (65534:65534). Permissions were still copied. Changing a file's "
        "owner needs root, and NFS shares usually turn root into 'nobody'. Run the script "
        "as the files' owner instead (sudo -u tronyx python3 ...).")]


def test_files_with_different_owners_are_listed_with_each_files_owners(nfs_owners, tmp_path,
                                                                        caplog):
    for name, wanted in (("a.mkv", (1000, 100)), ("b.mkv", (99, 100))):
        ssd._ownership_failures.append((str(tmp_path / name), wanted, (65534, 65534),
                                        "Operation not permitted"))

    ssd.report_ownership_failures(tmp_path)

    [listed] = tmp_path.glob("set_stereo_default-owners-*.log")
    assert listed.read_text(encoding="utf-8").splitlines() == [
        (f"{tmp_path / 'a.mkv'}  (should belong to tronyx:users (1000:100), "
         f"belongs to nobody:nogroup (65534:65534))"),
        f"{tmp_path / 'b.mkv'}  (should belong to 99:100, belongs to nobody:nogroup (65534:65534))"]
    assert ("Their owners vary (the list shows each file's); for example, a.mkv should belong "
            "to tronyx:users (1000:100)") in caplog.text


def test_owner_list_falls_back_to_the_temp_folder(tmp_path, monkeypatch, caplog):
    """e.g. the script was run from a folder it can't write to."""
    temp = tmp_path / "temp"
    temp.mkdir()
    monkeypatch.setattr(ssd.tempfile, "gettempdir", lambda: str(temp))
    for name in ("a.mkv", "b.mkv"):
        ssd._ownership_failures.append((name, (1000, 100), (65534, 65534), "Operation not permitted"))

    ssd.report_ownership_failures(tmp_path / "missing")

    [listed] = temp.glob("set_stereo_default-owners-*.log")
    assert f"You can view the full list of files here: {listed.resolve()}" in caplog.text


def test_owner_list_goes_in_the_warning_if_it_cant_be_saved(tmp_path, monkeypatch, caplog):
    monkeypatch.setattr(ssd.tempfile, "gettempdir", lambda: str(tmp_path / "missing too"))
    for name in ("/videos/a.mkv", "/videos/b.mkv"):
        ssd._ownership_failures.append((name, (1000, 100), (65534, 65534), "Operation not permitted"))

    ssd.report_ownership_failures(tmp_path / "missing")

    assert "The list of files couldn't be saved:\n/videos/a.mkv\n/videos/b.mkv\n\n" in caplog.text


def test_nothing_is_reported_when_every_owner_was_kept(tmp_path, caplog):
    ssd.report_ownership_failures(tmp_path)
    assert caplog.records == []
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("uid, gid, described", [
    (1000, 100, "tronyx:users (1000:100)"),
    (1000, 5, "1000:5"),
    (7, 100, "7:100"),
], ids=["both named", "group has no name", "user has no name"])
def test_owner_name(nfs_owners, uid, gid, described):
    assert ssd._owner_name(uid, gid) == described


def test_optional_module_is_none_when_it_cant_be_imported():
    """e.g. pwd and grp on Windows."""
    assert ssd._optional_module("no_such_module_here") is None


def test_owner_names_without_pwd_and_grp_fall_back_to_ids(monkeypatch):
    """Windows has no user or group names to look up."""
    monkeypatch.setattr(ssd, "pwd", None)
    monkeypatch.setattr(ssd, "grp", None)
    assert ssd._user_name(1000) is None
    assert ssd._group_name(100) is None
    assert ssd._owner_name(1000, 100) == "1000:100"


def test_sudo_hint_uses_the_user_id_when_the_owner_has_no_name(nfs_owners, monkeypatch, caplog):
    """e.g. Unraid's 99:100 seen from a client with no user 99. sudo accepts
    a numeric user as '#99'."""
    make, _ = nfs_owners
    monkeypatch.setattr(ssd, "_owner",
                        lambda path: (65534, 65534) if ssd.TMP_MARKER in str(path) else (99, 100))

    monkeypatch.setattr(ssd.os, "chown", not_permitted, raising=False)
    video, tmp = make("v.mkv")

    ssd.swap_in(video, tmp, backup=False)
    ssd.report_ownership_failures(video.parent)

    assert (f"Couldn't give {video} its original owner (Operation not permitted).\n\n"
            f"It should belong to 99:100 but belongs to nobody:nogroup (65534:65534)."
            ) in caplog.text
    assert "sudo -u '#99' python3" in caplog.text
    assert not list(video.parent.glob("set_stereo_default-owners-*.log"))


def test_hint_in_the_docker_image_is_a_docker_run_user(nfs_owners, monkeypatch, tmp_path, caplog):
    """The image has no sudo, and usually no name for the owner's IDs."""
    monkeypatch.setenv(ssd.IN_DOCKER_VAR, "1")
    ssd._ownership_failures.append((str(tmp_path / "v.mkv"), (1000, 100), (65534, 65534),
                                    "Operation not permitted"))

    ssd.report_ownership_failures(tmp_path)

    assert ("Run the container as the files' owner instead (docker run --user 1000:100 ...)."
            in caplog.text)
    assert "sudo" not in caplog.text


def test_owner_warning_comes_after_every_file_just_before_the_summary(tmp_path, monkeypatch,
                                                                        capsys):
    """With --jobs, the warning must still come once, after every file's
    lines, not under whichever file happened to fail first. The list of
    files is saved in the folder the script was run from."""
    videos, run_from = tmp_path / "videos", tmp_path / "run from here"
    videos.mkdir()
    run_from.mkdir()
    make_videos(videos, 3)
    monkeypatch.chdir(run_from)

    def fake_process_file(path, args, position=0, on_progress=None):
        ssd.log.info(f"  {path.name}: setting stream#1 (eng, aac) as default audio")
        with ssd._ownership_lock:
            ssd._ownership_failures.append((str(path), (1000, 100), (65534, 65534),
                                            "Operation not permitted"))
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(videos),
                                      "--no-progress", "--jobs", "2"])
    ssd.main()

    lines = capsys.readouterr().out.splitlines()
    warning = next(i for i, line in enumerate(lines) if line.startswith("Couldn't give"))
    assert lines[warning].startswith("Couldn't give 3 remuxed files their original owner")
    assert max(i for i, line in enumerate(lines) if "setting stream#1" in line) < warning
    assert lines[warning + 1] == ""
    assert lines[warning + 2].startswith("These files should belong to")
    assert lines[warning + 3:warning + 5] == ["", "----- Summary -----"]
    [listed] = run_from.glob("set_stereo_default-owners-*.log")
    assert sorted(listed.read_text(encoding="utf-8").splitlines()) == \
        sorted(str(v) for v in videos.iterdir())


def remux_with_mkvmerge(path):
    """Fix path with apply_mkv(), as if it had ORIGINAL_AUDIO's tracks,
    keeping a backup."""
    return ssd.apply_mkv(ssd.Plan(path, ORIGINAL_AUDIO, 2), file_args(dry_run=False, backup=True))


def remux_with_ffmpeg(path):
    """Fix path with apply_remux(), as if it had ORIGINAL_AUDIO's tracks,
    keeping a backup."""
    return ssd.apply_remux(ssd.Plan(path, ORIGINAL_AUDIO, 2, duration=100.0),
                           file_args(dry_run=False, backup=True))


@pytest.fixture(params=[(remux_with_mkvmerge, "v.mkv"), (remux_with_ffmpeg, "v.mp4")],
                ids=["mkvmerge", "ffmpeg"])
def remux(request, tmp_path, monkeypatch):
    """Runs each test once with mkvmerge and once with ffmpeg. Returns
    (apply, video, set_outcome): apply() fixes video, which starts out
    containing "original". No real remux runs: the stand-in writes
    "remuxed" to the temp file and succeeds, unless set_outcome() says
    otherwise first. "fail" makes the tool exit with an error, "warn" exit
    with code 1 and a warning, and "interrupt" raise KeyboardInterrupt.
    Any other string makes the post-remux check fail with that reason."""
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
    """Any temp remux files left next to video."""
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


@pytest.mark.parametrize("error", [ssd.Stopped(signal.SIGTERM), OSError("disk gone")],
                         ids=["stopped", "unexpected error"])
def test_stop_during_the_post_remux_check_removes_the_temp_file(remux, monkeypatch, error):
    apply, video, _ = remux

    def interrupted_check(*args, **kwargs):
        raise error
    monkeypatch.setattr(ssd, "verify_remux", interrupted_check)

    with pytest.raises(type(error)):
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


@pytest.mark.parametrize("output", [
    "#GUI#begin_scanning\n#GUI#warning Warning: odd timestamps\n#GUI#warning gap in track 1\n",
    "Warning: odd timestamps\nWarning: gap in track 1\n",
], ids=["gui mode", "plain"])
def test_mkvmerge_warnings_are_logged_without_their_prefixes(tmp_path, monkeypatch, caplog, output):
    def fake_mkvmerge(cmd, *args, **kwargs):
        next(Path(c) for c in cmd if ssd.TMP_MARKER in c).write_bytes(b"remuxed")
        return 1, output
    monkeypatch.setattr(ssd, "run_with_progress", fake_mkvmerge)
    monkeypatch.setattr(ssd, "verify_remux", lambda *a, **k: None)
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")

    assert ssd.apply_mkv(ssd.Plan(video, ORIGINAL_AUDIO, 2), file_args(dry_run=False)) is True
    assert caplog.records[-1].getMessage() == \
        "    v.mkv: mkvmerge finished with warnings: odd timestamps; gap in track 1"


@pytest.mark.parametrize("apply, filename, tool", [
    (remux_with_mkvmerge, "v.mkv", "mkvmerge"),
    (remux_with_ffmpeg, "v.mp4", "ffmpeg"),
], ids=["mkvmerge", "ffmpeg"])
@pytest.mark.parametrize("returncode", [0, 2], ids=["exit 0", "exit 2"])
def test_remux_that_writes_no_file_is_a_failure(tmp_path, monkeypatch, caplog, apply, filename,
                                                tool, returncode):
    monkeypatch.setattr(ssd, "run_with_progress", lambda cmd, *a, **k: (returncode, "disk full\n"))
    video = tmp_path / filename
    video.write_bytes(b"original")

    assert apply(video) is False

    assert f"{filename}: {tool} remux failed: disk full" in caplog.text
    assert video.read_bytes() == b"original"
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
    (lambda p: ssd.apply_mkv(ssd.Plan(p, ORIGINAL_AUDIO, 2), file_args()), "mkvmerge"),
    (lambda p: ssd.apply_remux(ssd.Plan(p, ORIGINAL_AUDIO, 2), file_args()), "ffmpeg"),
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


@pytest.fixture
def probed(monkeypatch):
    """Replace probe_streams() so every file reports ORIGINAL_AUDIO;
    the returned list records which files were probed."""
    calls = []

    def fake_probe(path):
        calls.append(path.name)
        return list(ORIGINAL_AUDIO), 100.0
    monkeypatch.setattr(ssd, "probe_streams", fake_probe)
    return calls


def test_avi_without_reorder_is_skipped_before_anything_is_announced(tmp_path, probed, caplog):
    caplog.set_level("INFO")
    with ssd.file_context("[1/1] v.avi"):
        result = ssd.process_file(tmp_path / "v.avi", file_args())

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
    monkeypatch.setattr(ssd, "probe_streams", lambda path: (streams, 100.0))

    result = ssd.process_file(tmp_path / "v.avi", file_args(avi_reorder=True))

    assert result == "unchanged"
    assert "already correct (stream#1 is first audio stream)" in caplog.text


@pytest.mark.parametrize("name", ["v.mkv", "v.mp4"])
def test_other_containers_set_the_default_flag(tmp_path, probed, caplog, name):
    caplog.set_level("INFO")
    result = ssd.process_file(tmp_path / name, file_args(avi_reorder=True))

    assert result == "changed"
    assert "setting stream#2 (eng, aac) as default audio" in caplog.text


@pytest.mark.parametrize("default, line", [
    (1, "setting stream#2 (fre, aac) as default audio"),
    (2, "already correct (stream#2 is default), skipping"),
], ids=["changed", "already correct"])
def test_a_prefer_lang_fallback_is_noted_on_the_files_line(tmp_path, monkeypatch, caplog,
                                                           default, line):
    caplog.set_level("INFO")
    streams = [audio(1, 6, language="fre", default=default == 1),
               audio(2, 2, language="fre", default=default == 2)]
    monkeypatch.setattr(ssd, "probe_streams", lambda path: (streams, 100.0))

    ssd.process_file(tmp_path / "v.mkv", file_args(prefer_lang="en"))

    note = "(no 2-channel track in 'en', so picked as if --prefer-lang wasn't given)"
    assert f"  v.mkv: {line} {note}" in caplog.text


def test_file_without_audio_is_skipped(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(ssd, "probe_streams", lambda path: ([], 100.0))

    assert ssd.process_file(tmp_path / "v.mkv", file_args()) == "skipped"
    assert "v.mkv: SKIP (no audio streams found)" in caplog.text


def test_an_unexpected_error_fails_only_that_file(tmp_path, monkeypatch, caplog):
    def broken(*args, **kwargs):
        raise RuntimeError("something nobody expected")
    monkeypatch.setattr(ssd, "_process_file", broken)

    assert ssd.process_file(tmp_path / "v.mkv", file_args()) == "error"
    assert "v.mkv: unexpected error, skipping rest of file (something nobody expected)" in caplog.text


def test_a_file_ffprobe_cant_read_is_an_error(tmp_path, monkeypatch):
    monkeypatch.setattr(ssd, "probe_streams", lambda path: (None, None))
    assert ssd.process_file(tmp_path / "v.mkv", file_args()) == "error"


def test_a_file_with_no_track_to_pick_is_skipped_with_the_reason(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(ssd, "probe_streams", lambda path: ([audio(1, 6, default=True)], 100.0))

    assert ssd.process_file(tmp_path / "v.mkv", file_args()) == "skipped"
    assert "v.mkv: SKIP (no 2-channel audio track found)" in caplog.text


def test_ffmpeg_progress_is_reported_without_a_per_file_bar(tmp_path, monkeypatch):
    def fake_run_with_progress(cmd, label, parse_pct, progress=None):
        for us in (25_000_000, 50_000_000, 100_000_000):
            progress.on_progress(parse_pct(f"out_time_us={us}\n"))
        return 1, ""
    monkeypatch.setattr(ssd, "run_with_progress", fake_run_with_progress)

    seen = []
    ssd.apply_remux(ssd.Plan(tmp_path / "v.mp4", ORIGINAL_AUDIO, 2, duration=100.0),
                    file_args(dry_run=False), ssd.Progress(on_progress=seen.append))
    assert seen == [25, 50, 100]


FFMPEG_PROGRESS_BLOCK = ("frame=0\nfps=0.00\nstream_0_0_q=-1.0\nbitrate=N/A\ntotal_size=48\n"
                         "out_time_us=50000000\nout_time_ms=50000000\nout_time=00:00:50.000000\n"
                         "dup_frames=0\ndrop_frames=0\nspeed= 412x\nprogress=continue\n")


@pytest.mark.parametrize("duration", [100.0, None], ids=["known duration", "unknown duration"])
def test_ffmpeg_failure_message_leaves_out_progress_lines(tmp_path, monkeypatch, caplog, duration):
    """Runs apply_remux()'s real progress parsing over ffmpeg-style output:
    two progress blocks, then the error ffmpeg prints before exiting."""
    output = FFMPEG_PROGRESS_BLOCK * 2 + "[mp4 @ 0x5581] Could not find tag for codec\n"
    real_run_with_progress = ssd.run_with_progress

    def fake_ffmpeg(cmd, *args, **kwargs):
        code = f"import sys\nsys.stdout.write({output!r})\nsys.exit(1)"
        return real_run_with_progress(python_cmd(code), *args, **kwargs)
    monkeypatch.setattr(ssd, "run_with_progress", fake_ffmpeg)

    seen = []
    assert ssd.apply_remux(ssd.Plan(tmp_path / "v.mp4", ORIGINAL_AUDIO, 2, duration=duration),
                           file_args(dry_run=False), ssd.Progress(on_progress=seen.append)) is False

    error = caplog.records[-1].getMessage()
    assert error.endswith("ffmpeg remux failed: [mp4 @ 0x5581] Could not find tag for codec")
    assert seen == ([50] if duration else [])


@pytest.mark.parametrize("line, expected", [
    ("#GUI#progress 42%", 42),
    ("#GUI#progress 100%", 100),
    ("#GUI#warning Something odd", None),
    ("Progress: 42%", None),
])
def test_mkvmerge_pct(line, expected):
    assert ssd._mkvmerge_pct(line) == expected


@pytest.mark.parametrize("line, expected", [
    ("out_time_us=5000000", 50),
    ("out_time_us=N/A", 0),
    ("progress=continue", 0),
    ("[mp4 @ 0x1] some warning", None),
], ids=["halfway", "not known yet", "another key", "not a progress line"])
def test_ffmpeg_pct(line, expected):
    """ffmpeg reports out_time_us=N/A before its first packet, which is
    still a progress line, just with no progress to show."""
    assert ssd._ffmpeg_pct(10.0)(line) == expected


def test_run_with_progress_reports_increases_and_finishes_at_100():
    seen = []
    code = "for p in (10, 40, 40, 30, 70): print(f'pct={p}')"
    returncode, _ = ssd.run_with_progress(python_cmd(code), "x", parse_pct_line,
                                          ssd.Progress(on_progress=seen.append))
    assert returncode == 0
    assert seen == [10, 40, 70, 100]


def test_run_with_progress_keeps_only_the_last_50_lines():
    code = ("import sys\n"
            "for i in range(500): print(i)\n"
            "print('Error: boom', file=sys.stderr)\n"
            "sys.exit(3)")
    returncode, output = ssd.run_with_progress(python_cmd(code), "x", parse_pct_line)
    lines = output.splitlines()
    assert returncode == 3
    assert len(lines) == 50
    assert lines[-1] == "Error: boom"


def test_run_with_progress_leaves_progress_lines_out_of_the_output():
    code = ("print('Warning: early')\n"
            "for p in range(1, 101): print(f'pct={p}')")
    _, output = ssd.run_with_progress(python_cmd(code), "x", parse_pct_line)
    assert output.splitlines() == ["Warning: early"]


@pytest.mark.parametrize("exit_code, steps", [(0, [10, 30, 30, 30]), (1, [10, 30, 30])],
                         ids=["success fills the bar", "failure leaves it"])
def test_run_with_progress_shows_a_per_file_bar_and_closes_it(monkeypatch, exit_code, steps):
    pytest.importorskip("tqdm")
    bars = []

    class RecordingTqdm(ssd.tqdm):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            self.steps, self.was_closed = [], False
            bars.append(self)

        def update(self, n=1):
            self.steps.append(n)
            return super().update(n)

        def close(self):
            self.was_closed = True
            return super().close()

    monkeypatch.setattr(ssd, "HAVE_TQDM", True)
    monkeypatch.setattr(ssd, "tqdm", RecordingTqdm)
    code = ("import sys\n"
            "for p in (10, 40, 40, 30, 70): print(f'pct={p}')\n"
            f"sys.exit({exit_code})")

    returncode, _ = ssd.run_with_progress(python_cmd(code), "Episode 1.mkv", parse_pct_line,
                                          ssd.Progress(show=True))

    assert returncode == exit_code
    [bar] = bars
    assert bar.desc == "  Episode 1.mkv"
    assert bar.steps == steps
    assert bar.was_closed


UTF8_OUTPUT_CMD = python_cmd(
    "import sys\n"
    "sys.stdout.buffer.write('Título: Été — 日本語\\n'.encode('utf-8') + b'bad \\xff byte\\n')")


def test_run_reads_output_as_utf8_and_replaces_invalid_bytes():
    """ffprobe writes UTF-8 whatever the system's encoding, so a track name
    must come through intact even where the default is cp1252 (Windows),
    and one bad byte mustn't stop the run."""
    res = ssd.run(UTF8_OUTPUT_CMD)
    assert res.stdout.splitlines() == ["Título: Été — 日本語", "bad \ufffd byte"]


def test_run_with_progress_reads_output_as_utf8_and_replaces_invalid_bytes():
    returncode, output = ssd.run_with_progress(UTF8_OUTPUT_CMD, "x", parse_pct_line)
    assert returncode == 0
    assert output.splitlines() == ["Título: Été — 日本語", "bad \ufffd byte"]


def test_mkvmerge_is_told_to_write_utf8(tmp_path, caplog):
    caplog.set_level("INFO")
    ssd.apply_mkv(ssd.Plan(tmp_path / "v.mkv", ORIGINAL_AUDIO, 2), file_args())
    args = shlex.split(caplog.text.split("[dry-run] ", 1)[1])
    assert args[args.index("--output-charset") + 1] == "UTF-8"


def font(mimetype):
    """A font attachment stream with the given MIME type."""
    return ssd.Stream(index=9, type="attachment", codec="ttf", mimetype=mimetype)


@pytest.mark.parametrize("attachments, option_known, added", [
    ([font("application/x-truetype-font")], True, True),
    ([font("Application/Vnd.MS-OpenType")], True, True),
    ([font("font/ttf"), font("application/x-truetype-font")], True, True),
    ([font("application/x-truetype-font")], False, False),
    ([font("font/ttf")], True, False),
    ([], True, False),
], ids=["legacy ttf", "legacy otf, any case", "mixed", "mkvmerge too old", "new type only",
        "no fonts"])
def test_mkvmerge_keeps_legacy_font_types(tmp_path, caplog, monkeypatch, attachments,
                                          option_known, added):
    caplog.set_level("INFO")
    monkeypatch.setattr(ssd, "mkvmerge_can_keep_legacy_font_types", lambda: option_known)
    plan = ssd.Plan(tmp_path / "v.mkv", ORIGINAL_AUDIO, 2,
                    layout=[*ORIGINAL_AUDIO, *attachments])

    assert ssd.apply_mkv(plan, file_args())

    args = shlex.split(caplog.text.split("[dry-run] ", 1)[1])
    assert ("--enable-legacy-font-mime-types" in args) is added
    assert args.index("-o") < args.index("--default-track")


@pytest.mark.parametrize("help_text, expected", [
    ("  --enable-legacy-font-mime-types  Use legacy font MIME types", True),
    ("  --default-track <TID[:bool]>", False),
], ids=["has the option", "older mkvmerge"])
def test_mkvmerge_can_keep_legacy_font_types(monkeypatch, help_text, expected):
    monkeypatch.setattr(ssd, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=0, stdout=help_text, stderr=""))
    assert ssd.mkvmerge_can_keep_legacy_font_types() is expected


def test_mkvmerge_can_keep_legacy_font_types_without_mkvmerge(monkeypatch):
    def missing(cmd, **kw):
        raise FileNotFoundError(2, "No such file or directory")
    monkeypatch.setattr(ssd, "run", missing)
    assert ssd.mkvmerge_can_keep_legacy_font_types() is False


def test_stream_info_reads_an_attachments_mime_type():
    raw = {"index": 5, "codec_type": "attachment", "codec_name": "ttf",
           "tags": {"filename": "Font.ttf", "mimetype": "application/x-truetype-font"}}
    assert ssd._stream_info(raw).mimetype == "application/x-truetype-font"


def dry_run_flags(caplog):
    """The --default-track values from a logged mkvmerge dry-run command."""
    args = shlex.split(caplog.text.split("[dry-run] ", 1)[1])
    return [args[i + 1] for i, a in enumerate(args) if a == "--default-track"]


def test_mkvmerge_gets_its_own_track_ids_when_they_differ_from_ffprobes(tmp_path, caplog,
                                                                       mkvmerge_ids):
    """ffprobe calls the audio tracks streams 1 and 2, but mkvmerge (which
    also counts a track ffmpeg skipped) calls them 2 and 3."""
    caplog.set_level("INFO")
    mkvmerge_ids.value = [2, 3]

    assert ssd.apply_mkv(ssd.Plan(tmp_path / "v.mkv", ORIGINAL_AUDIO, 2), file_args())

    assert dry_run_flags(caplog) == ["2:no", "3:yes"]


def test_mkvmerge_keeps_the_original_track_order(tmp_path, caplog, monkeypatch):
    """mkvmerge writes video, then audio, then subtitles unless told
    otherwise, so a subtitle between two audio tracks would move to the end
    and the remux be rejected. --track-order lists every track as it was."""
    caplog.set_level("INFO")
    monkeypatch.setattr(ssd, "mkvmerge_tracks", lambda path: [
        (0, "video"), (1, "audio"), (2, "subtitles"), (3, "audio")])
    streams = [audio(1, 2, default=True), audio(3, 6, "eac3", default=True)]

    assert ssd.apply_mkv(ssd.Plan(tmp_path / "v.mkv", streams, 1), file_args())

    args = shlex.split(caplog.text.split("[dry-run] ", 1)[1])
    assert args[args.index("--track-order") + 1] == "0:0,0:1,0:2,0:3"
    assert dry_run_flags(caplog) == ["1:yes", "3:no"]


@pytest.mark.parametrize("ids, message", [
    ([1], "mkvmerge sees 1 audio track(s), but ffprobe sees 2"),
    ([], "mkvmerge sees 0 audio track(s), but ffprobe sees 2"),
    (None, "mkvmerge couldn't read the file, but ffprobe sees 2"),
], ids=["fewer", "none", "unreadable"])
def test_mkvmerge_and_ffprobe_disagreeing_leaves_the_file_alone(tmp_path, caplog, mkvmerge_ids,
                                                               ids, message):
    mkvmerge_ids.value = ids
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")

    assert ssd.apply_mkv(ssd.Plan(video, ORIGINAL_AUDIO, 2), file_args(dry_run=False)) is False

    assert message in caplog.text
    assert video.read_bytes() == b"original"


@pytest.mark.parametrize("returncode, stdout, expected", [
    (0, json.dumps({"tracks": [{"id": 0, "type": "video"}, {"id": 1, "type": "audio"},
                               {"id": 2, "type": "subtitles"}, {"id": 3, "type": "audio"}]}),
     [(0, "video"), (1, "audio"), (2, "subtitles"), (3, "audio")]),
    (0, json.dumps({"container": {"recognized": False}, "errors": []}), []),
    (2, "", None),
    (0, "not json", None),
], ids=["every track in order", "unrecognized file", "mkvmerge failed", "bad output"])
def test_mkvmerge_tracks(monkeypatch, returncode, stdout, expected):
    monkeypatch.setattr(ssd, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=""))
    assert real_mkvmerge_tracks(Path("v.mkv")) == expected


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
        ssd.run_with_progress(SLOW_CMD, "x", parse_pct_line, ssd.Progress(on_progress=fail))
    assert started[0].poll() is not None
    assert not ssd._active_procs


@pytest.mark.parametrize("have_tqdm, no_progress, terminal, shown", [
    (True, False, True, True),
    (True, False, False, False),
    (True, True, True, False),
    (False, False, True, False),
], ids=["terminal", "no terminal", "--no-progress", "no tqdm"])
def test_bars_need_tqdm_a_terminal_and_no_no_progress(monkeypatch, have_tqdm, no_progress,
                                                      terminal, shown):
    monkeypatch.setattr(ssd, "HAVE_TQDM", have_tqdm)
    monkeypatch.setattr(ssd.sys, "stderr", types.SimpleNamespace(isatty=lambda: terminal))
    assert ssd._show_bars(file_args(no_progress=no_progress)) is shown


@pytest.mark.parametrize("terminal", [True, False], ids=["terminal", "no terminal"])
def test_log_file_counter_only_shows_on_a_terminal(tmp_path, monkeypatch, capsys, terminal):
    """The counter redraws itself with a carriage return, which only works
    on a terminal; in a log it would run every count into one line."""
    monkeypatch.setattr(ssd, "_show_bars", lambda args: False)
    monkeypatch.setattr(ssd, "process_file", lambda path, args, **kwargs: "unchanged")
    monkeypatch.setattr(sys.stdout, "isatty", lambda: terminal)
    stats = {}

    ssd.process_all([tmp_path / "a.mkv", tmp_path / "b.mkv"],
                    file_args(log_file=str(tmp_path / "run.log")), stats)

    assert stats == {"unchanged": 2}
    assert ("\rProcessing 2/2..." in capsys.readouterr().out) is terminal


def test_the_script_runs_without_tqdm(tmp_path):
    """tqdm is optional: without it, the script must still import and run,
    logging through a plain handler with no progress bars. Run in a separate
    Python, where importing tqdm fails as if it weren't installed."""
    code = ("import sys\n"
            "sys.modules['tqdm'] = None\n"
            f"sys.path.insert(0, {str(Path(ssd.__file__).parent)!r})\n"
            "import set_stereo_default as ssd\n"
            "print('HAVE_TQDM', ssd.HAVE_TQDM)\n"
            f"sys.exit(ssd.main([{str(tmp_path)!r}]))\n")
    res = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                         encoding="utf-8", check=False)
    assert "HAVE_TQDM False" in res.stdout, res.stdout + res.stderr
    assert "No matching files found." in res.stdout + res.stderr
    assert res.returncode == 1
    assert "Traceback" not in res.stderr, res.stderr


def test_run_with_progress_kills_its_subprocess_if_the_bar_cant_be_created(monkeypatch):
    started = []
    real_popen = subprocess.Popen

    def recording_popen(*args, **kwargs):
        started.append(real_popen(*args, **kwargs))
        return started[-1]
    monkeypatch.setattr(ssd.subprocess, "Popen", recording_popen)
    monkeypatch.setattr(ssd, "HAVE_TQDM", True)

    def broken_tqdm(*args, **kwargs):
        raise RuntimeError("no terminal")
    monkeypatch.setattr(ssd, "tqdm", broken_tqdm)

    with pytest.raises(RuntimeError):
        ssd.run_with_progress(SLOW_CMD, "x", parse_pct_line, ssd.Progress(show=True))
    assert started[0].poll() is not None
    assert not ssd._active_procs


def test_stop_handler_doesnt_hang_if_it_interrupts_the_lock_holder():
    """The handler runs in the main thread, which may already hold
    _active_procs_lock when the signal arrives. Simulated here in a worker
    thread, so a regression shows up as a failed assert instead of a hang."""
    def holder():
        with ssd._active_procs_lock:
            ssd._terminate_active_procs()

    t = threading.Thread(target=holder, daemon=True)
    t.start()
    t.join(timeout=5)
    assert not t.is_alive()


def test_run_with_progress_kills_a_subprocess_started_after_ctrl_c():
    ssd._cancelled.set()
    t0 = time.monotonic()
    returncode, _ = ssd.run_with_progress(SLOW_CMD, "x", parse_pct_line)
    assert returncode != 0
    assert time.monotonic() - t0 < 5
    assert not ssd._active_procs


def test_terminate_active_procs_unblocks_a_worker_thread():
    result = {}

    def worker():
        result["returncode"] = ssd.run_with_progress(SLOW_CMD, "x", parse_pct_line)[0]

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


def test_a_stop_kills_a_quick_command_too_and_cancels_the_file():
    """run() (ffprobe, mkvmerge -J) is killed by a stop like a remux, e.g.
    when a network share has stopped answering, and raises Cancelled
    rather than returning a failure."""
    result = {}

    def worker():
        try:
            ssd.run(SLOW_CMD)
            result["outcome"] = "returned"
        except ssd.Cancelled:
            result["outcome"] = "cancelled"

    thread = threading.Thread(target=worker)
    thread.start()
    deadline = time.monotonic() + 10
    while not ssd._active_procs and time.monotonic() < deadline:
        time.sleep(0.05)

    ssd._cancelled.set()
    ssd._terminate_active_procs()
    thread.join(timeout=10)

    assert not thread.is_alive()
    assert result["outcome"] == "cancelled"
    assert not ssd._active_procs


def test_a_quick_command_started_after_a_stop_is_killed_straight_away():
    ssd._cancelled.set()
    started = time.monotonic()
    with pytest.raises(ssd.Cancelled):
        ssd.run(SLOW_CMD)
    assert time.monotonic() - started < 5
    assert not ssd._active_procs


def test_a_quick_command_is_only_listed_while_it_runs():
    assert ssd.run(python_cmd("print('done')")).stdout == "done\n"
    assert not ssd._active_procs


def test_run_kills_its_subprocess_if_interrupted(monkeypatch):
    """A stop (or any error) while waiting must not leave the tool running."""
    class Interrupted:
        returncode = None
        killed = waited = False

        def communicate(self):
            raise KeyboardInterrupt

        def kill(self):
            self.killed = True

        def wait(self, timeout=None):
            self.waited = True
    proc = Interrupted()
    monkeypatch.setattr(ssd.subprocess, "Popen", lambda *args, **kwargs: proc)

    with pytest.raises(KeyboardInterrupt):
        ssd.run(["ffprobe", "x"])

    assert proc.killed and proc.waited
    assert not ssd._active_procs


def test_a_file_stopped_while_being_checked_is_reported_as_cancelled(tmp_path, monkeypatch,
                                                                     caplog):
    """Not as "ffprobe failed": the command only failed because it was killed."""
    caplog.set_level("INFO")

    def stopped_probe(path, report=True):
        raise ssd.Cancelled
    monkeypatch.setattr(ssd, "probe_streams", stopped_probe)

    assert ssd.process_file(tmp_path / "v.mkv", file_args()) == "cancelled"
    assert caplog.records[-1].getMessage() == "  v.mkv: cancelled"
    assert not [r for r in caplog.records if r.levelname == "ERROR"]


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

    def fake_process_file(path, args, position=0, on_progress=None):
        for pct in (25, 50, 75):
            on_progress(pct)
        return "changed"

    shown = []

    class RecordingTqdm(ssd.tqdm):
        def refresh(self, *args, **kwargs):
            if self.desc == "Processing":
                shown.append(round(self.n, 2))
            return super().refresh(*args, **kwargs)

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(ssd, "tqdm", RecordingTqdm)
    monkeypatch.setattr(ssd, "_show_bars", lambda args: True)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--jobs", jobs])

    ssd.main()

    assert {round(0.25 * i, 2) for i in range(1, 13)} <= set(shown)
    assert max(shown) == 3.0


def under_headers(lines):
    """Pair each line with the file whose "[i/N] name" header it sits under,
    as a reader would: [(header file, line), ...] for every non-header,
    non-blank line. A header's file is the last part of its path."""
    pairs, current = [], None
    for line in lines:
        if line.startswith("[") and "] " in line:
            current = Path(line.split("] ", 1)[1]).name
        elif line.strip():
            pairs.append((current, line))
    return pairs


def test_lines_from_two_files_each_stay_under_their_own_header(caplog):
    """Both threads log their first line, wait until the other has too, then
    log a later line, so the later lines always follow the other file's
    lines. Each is printed under its own header, repeated for it."""
    caplog.set_level("INFO")
    barrier = threading.Barrier(2, timeout=5)

    def work(i, name):
        header = f"[{i}/2] {name}"
        with ssd.file_context(header):
            ssd.log.info(f"  {name} checked")
            barrier.wait()
            ssd.log.warning(f"    {name} command")

    threads = [threading.Thread(target=work, args=(i, name))
               for i, name in ((1, "a"), (2, "b"))]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    lines = "\n".join(r.getMessage() for r in caplog.records).splitlines()
    pairs = under_headers(lines)
    assert len(pairs) == 4
    assert all(line.split()[0] == owner for owner, line in pairs)
    assert [r.levelname for r in caplog.records if "command" in r.getMessage()] == ["WARNING"] * 2


def test_a_files_first_line_gets_its_header_and_the_rest_print_straight_away(caplog):
    """The header is added by the filter, so the code that logs a file's
    lines never includes it, whatever line comes first (a decision, or
    "ffprobe failed on ..."). Later lines from the same file don't repeat
    it."""
    caplog.set_level("INFO")
    with ssd.file_context("[1/1] a"):
        ssd.log.error("  ffprobe failed on a")
        assert caplog.records[-1].getMessage() == "\n[1/1] a\n  ffprobe failed on a"
        ssd.log.info("    a command")
        assert caplog.records[-1].getMessage() == "    a command"
    assert len(caplog.records) == 2


def test_lines_outside_a_file_pass_straight_through(caplog):
    caplog.set_level("INFO")
    ssd.log.info("Found 2 file(s).")
    assert caplog.records[0].getMessage() == "Found 2 file(s)."


def test_jobs_dry_run_shows_each_files_header_once(tmp_path, monkeypatch, capsys):
    """In a --jobs dry run, each file's "setting ..." line must print
    together with its command, so the file's header appears only once. Both
    files are held at the mkvmerge lookup, between the two lines, until
    both get there, so if the "setting ..." line printed on its own, the
    other file's lines would come between it and the command."""
    make_videos(tmp_path, 2)
    barrier = threading.Barrier(2, timeout=5)

    def slow_tracks(path):
        barrier.wait()
        return [(0, "video"), (1, "audio"), (2, "audio")]

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "probe_streams",
                        lambda path: (list(ORIGINAL_AUDIO), 100.0))
    monkeypatch.setattr(ssd, "mkvmerge_tracks", slow_tracks)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--no-progress",
                                      "--jobs", "2", "--dry-run"])
    ssd.main()

    lines = capsys.readouterr().out.splitlines()
    headers = [line for line in lines if line.startswith("[")]
    assert sorted(headers) == [f"[1/2] {tmp_path / 'e00.mkv'}", f"[2/2] {tmp_path / 'e01.mkv'}"]
    for header in headers:
        name = Path(header.split("] ", 1)[1]).name
        at = lines.index(header)
        assert lines[at + 1].startswith(f"  {name}: setting stream#2")
        assert lines[at + 2].startswith("    [dry-run] mkvmerge") and name in lines[at + 2]


def test_jobs_keeps_each_files_lines_under_its_own_header(tmp_path, monkeypatch, capsys):
    """With --jobs, each file's lines must print under that file's header.
    Both files log their first line before either logs its second, so the
    second lines follow the other file's; each must get its own header
    again rather than appearing under the other's. Lines must still print
    as they happen, not at the end."""
    make_videos(tmp_path, 2)
    barrier = threading.Barrier(2, timeout=5)

    def fake_process_file(path, args, position=0, on_progress=None):
        ssd.log.info(f"  {path.name} checked")
        barrier.wait()
        ssd.log.info(f"    {path.name} command")
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path),
                                      "--no-progress", "--jobs", "2"])
    ssd.main()

    pairs = [(owner, line) for owner, line in under_headers(capsys.readouterr().out.splitlines())
             if line.endswith((" checked", " command"))]
    assert len(pairs) == 4
    assert all(line.split()[0] == owner for owner, line in pairs)


def test_ctrl_c_with_jobs_skips_files_that_havent_started(tmp_path, monkeypatch):
    """Files still waiting their turn when Ctrl+C arrives must not start.
    Leaving the thread pool waits for its workers, which would otherwise
    keep taking queued files until the whole queue had been remuxed."""
    make_videos(tmp_path, 8)
    started = []

    def fake_process_file(path, args, position=0, on_progress=None):
        started.append(path.name)
        ssd._cancelled.set()
        time.sleep(0.1)
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--jobs", "2", "--no-progress"])

    ssd.main()

    assert 1 <= len(started) <= 2


def test_partial_summary_counts_unfinished_files_as_cancelled(tmp_path, monkeypatch, capsys):
    make_videos(tmp_path, 5)
    calls = []

    def fake_process_file(path, args, position=0, on_progress=None):
        calls.append(path.name)
        if len(calls) == 3:
            raise KeyboardInterrupt
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--no-progress"])

    code = ssd.main()

    out = capsys.readouterr().out
    assert code == 130
    assert "Summary (Partial -- interrupted)" in out
    assert "Changed: 2" in out
    assert "Cancelled: 3" in out


@pytest.mark.parametrize("dry_run, interrupted, heading, first_line", [
    (False, False, "----- Summary -----", "Changed: 2"),
    (True, False, "----- Summary (Dry run, nothing was changed) -----", "Would change: 2"),
    (False, True, "----- Summary (Partial -- interrupted) -----", "Changed: 1"),
    (True, True, "----- Summary (Partial -- interrupted; dry run, nothing was changed) -----",
     "Would change: 1"),
], ids=["normal", "dry run", "interrupted", "interrupted dry run"])
def test_summary_says_what_kind_of_run_it_was(tmp_path, monkeypatch, capsys, dry_run, interrupted,
                                              heading, first_line):
    make_videos(tmp_path, 2)
    calls = []

    def fake_process_file(path, args, position=0, on_progress=None):
        calls.append(path.name)
        if interrupted and len(calls) == 2:
            raise KeyboardInterrupt
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--no-progress",
                                      *(["--dry-run"] if dry_run else [])])

    ssd.main()

    lines = capsys.readouterr().out.splitlines()
    start = lines.index(heading)
    assert lines[start + 1:start + 5] == [first_line, "Unchanged: 0", "Skipped: 0", "Error: 0"]


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

    def fake_process_file(path, args, position=0, on_progress=None):
        calls.append(path.name)
        if len(calls) == 3:
            signal.raise_signal(signum)
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--no-progress"])

    exit_code = ssd.main()

    out = capsys.readouterr().out
    assert exit_code == code
    assert message in out
    assert "Changed: 2" in out
    assert "Cancelled: 3" in out
    assert len(calls) == 3


@pytest.mark.parametrize("signum, code, message", [
    (signal.SIGINT, 130, "Interrupted by user (Ctrl+C) while looking for files"),
    (signal.SIGTERM, 143, "Stopped by SIGTERM while looking for files"),
], ids=["SIGINT", "SIGTERM"])
def test_signal_while_looking_for_files_exits_cleanly(tmp_path, monkeypatch, capsys,
                                                      signum, code, message):
    def interrupted_scan(*args, **kwargs):
        yield tmp_path / "a.mkv"
        signal.raise_signal(signum)

    processed = []
    monkeypatch.setattr(ssd, "iter_files", interrupted_scan)
    monkeypatch.setattr(ssd, "process_file", lambda *a, **k: processed.append(a))
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--no-progress"])

    exit_code = ssd.main()

    out = capsys.readouterr().out
    assert exit_code == code
    assert message in out
    assert "No files were changed" in out
    assert "Summary" not in out
    assert processed == []


@pytest.mark.filterwarnings("error")
def test_overall_bar_never_drifts_past_the_total(tmp_path, monkeypatch):
    """Two files reporting 1% at a time used to add up to
    2.0000000000000004, which made tqdm warn and show a negative ETA."""
    pytest.importorskip("tqdm")
    for name in ("a.mkv", "b.mkv"):
        (tmp_path / name).write_text("x")

    def fake_process_file(path, args, position=0, on_progress=None):
        for pct in range(1, 99):
            on_progress(pct)
        return "changed"

    finals = []

    class RecordingTqdm(ssd.tqdm):
        def close(self):
            if self.desc == "Processing":
                finals.append(self.n)
            return super().close()

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: True)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(ssd, "tqdm", RecordingTqdm)
    monkeypatch.setattr(ssd, "_show_bars", lambda args: True)
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


def test_log_handler_reports_its_own_errors_instead_of_raising(monkeypatch):
    """A message that can't be written mustn't stop the run; logging's
    handleError() reports it instead."""
    def broken_write(text):
        raise OSError("console went away")
    handled = []
    monkeypatch.setattr(ssd, "tqdm", types.SimpleNamespace(write=broken_write))
    handler = ssd.TqdmLoggingHandler()
    monkeypatch.setattr(handler, "handleError", handled.append)
    record = logging.LogRecord("x", logging.INFO, __file__, 1, "hello", None, None)

    handler.emit(record)

    assert handled == [record]


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

    assert ssd.main() == 1
    assert expected in capsys.readouterr().out
    assert expected in (tmp_path / "run.log").read_text()
