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

real_mkvmerge_audio_ids = ssd.mkvmerge_audio_ids


@pytest.fixture(autouse=True)
def mkvmerge_ids(monkeypatch):
    """mkvmerge never runs in these tests, so mkvmerge_audio_ids() reports
    audio track IDs 1 and 2, the indexes the ffprobe stand-ins use. Set
    mkvmerge_ids.value to report something else."""
    stand_in = types.SimpleNamespace(value=[1, 2])
    monkeypatch.setattr(ssd, "mkvmerge_audio_ids", lambda path: stand_in.value)
    return stand_in


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
    assert target["index"] == 2


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


@pytest.mark.parametrize("lang", ["", "und"])
def test_untagged_default_track_lets_a_stereo_track_in_any_language_through(lang):
    streams = [audio(1, 6, language=lang, default=True), audio(2, 2, language="spa")]
    target, note = ssd.choose_target(streams, None)
    assert target["index"] == 2 and note is None


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
    assert target["index"] == 2


def test_tracks_tagged_differently_in_one_language_arent_treated_as_a_dub():
    streams = [audio(1, 6, language="ger", default=True), audio(2, 2, language="deu")]
    target, note = ssd.choose_target(streams, None)
    assert target["index"] == 2 and note is None


def test_skip_note_shows_the_language_as_given():
    streams = [audio(1, 6, language="eng", default=True), audio(2, 2, language="spa")]
    _, note = ssd.choose_target(streams, "de")
    assert "no 2-channel track in 'de'" in note


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
        ssd.check_tools(need_mkvmerge)
        assert not caplog.records
    else:
        with pytest.raises(SystemExit) as exit_info:
            ssd.check_tools(need_mkvmerge)
        assert exit_info.value.code == 1
        assert f"Missing required tool(s): {reported}" in caplog.text
        assert "MKVToolNix (https://mkvtoolnix.download)" in caplog.text


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


def test_iter_files_accepts_files_passed_directly(library):
    paths = [library / "a.mkv", library / "a.mkv.tmp_remux.mkv", library / "notes.nfo"]
    assert names(ssd.iter_files(paths, ssd.DEFAULT_EXTS, recursive=True), library) == ["a.mkv"]


def test_iter_files_warns_about_leftover_temp_files(library, caplog):
    list(ssd.iter_files([library], ssd.DEFAULT_EXTS, recursive=True))
    assert "a.mkv.tmp_remux.mkv" in caplog.text
    assert "safe to delete" in caplog.text


def test_iter_files_warns_about_a_path_that_doesnt_exist(library, caplog):
    typo = library.parent / "vidoes"

    found = ssd.iter_files([typo, library], ssd.DEFAULT_EXTS, recursive=True)

    assert names(found, library) == ["Season 01/c.mkv", "a.mkv", "b.MP4"]
    assert f"Skipping {typo}: no such file or directory" in caplog.text


@pytest.mark.skipif(not hasattr(os, "mkfifo"), reason="needs a named pipe (not on Windows)")
def test_iter_files_warns_about_a_path_that_isnt_a_file_or_folder(tmp_path, caplog):
    pipe = tmp_path / "pipe.mkv"
    os.mkfifo(pipe)

    assert list(ssd.iter_files([pipe], ssd.DEFAULT_EXTS, recursive=True)) == []
    assert f"Skipping {pipe}: not a file or directory" in caplog.text


def test_iter_files_warns_about_a_folder_it_cant_open(library, monkeypatch, caplog):
    """os.walk() skips a folder it can't open without a word, so its files
    would quietly be missing from the run. The folder is made unreadable by
    failing os.scandir() for it, which works the same on every platform
    and when run as root."""
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

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        processed.append(path.name)
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
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

        def fake_process_file(path, args, position=0, header="", on_progress=None):
            seen.append(args.backup)
            return "changed"

        def fake_input(prompt):
            questions.append(prompt)
            if isinstance(answer, BaseException):
                raise answer
            return answer

        monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
        monkeypatch.setattr(ssd, "process_file", fake_process_file)
        monkeypatch.setattr(ssd, "_can_ask", lambda: tty)
        monkeypatch.setattr("builtins.input", fake_input)
        monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path),
                                          "--no-progress", *options])
        code = None
        try:
            ssd.main()
        except SystemExit as exc:
            code = exc.code
        return code, seen, questions
    return tmp_path, run


@pytest.mark.parametrize("answer, mode", [("d", "replace"), ("n", "number")])
def test_existing_backups_are_asked_about_once(backed_up, answer, mode):
    _, run = backed_up
    code, seen, questions = run("--backup", answer=answer)

    assert code is None
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

    assert code is None
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
    code, seen, questions = run("--backup", "--existing-backups", mode)

    assert questions == []
    assert seen == [mode, mode]


@pytest.mark.parametrize("options", [["--backup", "--dry-run"], []], ids=["dry run", "no --backup"])
def test_no_backup_question_when_no_backup_will_be_made(backed_up, options):
    _, run = backed_up
    code, seen, questions = run(*options)

    assert questions == []
    assert len(seen) == 2


def test_no_backup_question_without_existing_backups(backed_up):
    folder, run = backed_up
    (folder / "e00.mkv.bak").unlink()
    code, seen, questions = run("--backup")

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
    fake_ffprobe["orig"] = (ORIGINAL_LAYOUT, before)
    fake_ffprobe["tmp"] = (REMUXED_LAYOUT, after)

    problem = ssd.verify_remux("orig", "tmp", ORIGINAL_AUDIO, 2, reordered=False)

    if rejected:
        assert problem == (f"duration dropped from {before:.1f}s to {after:.1f}s; "
                           f"the original may be incomplete")
    else:
        assert problem is None



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
    def not_permitted(*args):
        raise PermissionError(1, "Operation not permitted")
    monkeypatch.setattr(ssd.os, "chown", not_permitted, raising=False)
    monkeypatch.setattr(ssd, "_owner", lambda path: (1000, 100))
    video = tmp_path / "v.mkv"
    video.write_bytes(b"original")
    tmp = tmp_path / "v.mkv.tmp_remux.mkv"
    tmp.write_bytes(b"remuxed")

    ssd.swap_in(video, tmp, backup=False)

    assert video.read_bytes() == b"remuxed"
    assert "Couldn't give" not in caplog.text


def not_permitted(*args):
    """A stand-in for os.chown() on an NFS share that squashes root."""
    raise PermissionError(1, "Operation not permitted")


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
    assert warnings == [
        "\nCouldn't give 2 remuxed files their original owner (Operation not permitted). "
        f"You can view the full list of files here: {listed.resolve()}\n\n"
        "These files should belong to tronyx:users (1000:100) but belong to "
        "nobody:nogroup (65534:65534). Permissions were still copied. Changing a file's "
        "owner needs root, and NFS shares usually turn root into 'nobody'. Run the script "
        "as the files' owner instead (sudo -u tronyx python3 ...)."]


def test_files_with_different_owners_are_listed_with_each_files_owners(nfs_owners, tmp_path,
                                                                        caplog):
    for name, wanted in (("a.mkv", (1000, 100)), ("b.mkv", (99, 100))):
        ssd._ownership_failures.append((str(tmp_path / name), wanted, (65534, 65534),
                                        "Operation not permitted"))

    ssd.report_ownership_failures(tmp_path)

    [listed] = tmp_path.glob("set_stereo_default-owners-*.log")
    assert listed.read_text(encoding="utf-8").splitlines() == [
        f"{tmp_path / 'a.mkv'}  (should belong to tronyx:users (1000:100), "
        f"belongs to nobody:nogroup (65534:65534))",
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


def test_owner_warning_comes_after_every_file_just_before_the_summary(tmp_path, monkeypatch,
                                                                        capsys):
    """The run from the bug report: with --jobs, the warning used to appear
    under whichever file failed first. The list of files is saved in the
    folder the script was run from."""
    videos, run_from = tmp_path / "videos", tmp_path / "run from here"
    videos.mkdir()
    run_from.mkdir()
    make_videos(videos, 3)
    monkeypatch.chdir(run_from)

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        ssd.log.info(f"\n{header}\n  {path.name}: setting stream#1 (eng, aac) as default audio")
        with ssd._ownership_lock:
            ssd._ownership_failures.append((str(path), (1000, 100), (65534, 65534),
                                            "Operation not permitted"))
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
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
    return ssd.apply_mkv(path, ORIGINAL_AUDIO, 2, dry_run=False, backup=True)


def remux_with_ffmpeg(path):
    """Fix path with apply_remux(), as if it had ORIGINAL_AUDIO's tracks,
    keeping a backup."""
    return ssd.apply_remux(path, ORIGINAL_AUDIO, 2, dry_run=False, backup=True,
                           reorder_for_avi=False, duration=100.0)


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

    assert ssd.apply_mkv(video, ORIGINAL_AUDIO, 2, dry_run=False, backup=False) is True
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
                  backup=False, keep_dates=False, no_progress=True, jobs=1)
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


def test_file_without_audio_is_skipped(tmp_path, monkeypatch, caplog):
    caplog.set_level("INFO")
    monkeypatch.setattr(ssd, "probe_audio_streams", lambda path: ([], 100.0))

    assert ssd.process_file(tmp_path / "v.mkv", file_args()) == "skipped"
    assert "v.mkv: no audio streams found, skipping" in caplog.text


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
    assert ssd.apply_remux(tmp_path / "v.mp4", ORIGINAL_AUDIO, 2, dry_run=False, backup=False,
                           reorder_for_avi=False, duration=duration, show_progress=False,
                           on_progress=seen.append) is False

    error = caplog.records[-1].getMessage()
    assert error.endswith("ffmpeg remux failed: [mp4 @ 0x5581] Could not find tag for codec")
    assert seen == ([50] if duration else [])



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

    returncode, _ = ssd.run_with_progress(python_cmd(code), "Episode 1.mkv", True, parse_pct_line)

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
    returncode, output = ssd.run_with_progress(UTF8_OUTPUT_CMD, "x", False, parse_pct_line)
    assert returncode == 0
    assert output.splitlines() == ["Título: Été — 日本語", "bad \ufffd byte"]


def test_mkvmerge_is_told_to_write_utf8(tmp_path, caplog):
    caplog.set_level("INFO")
    ssd.apply_mkv(tmp_path / "v.mkv", ORIGINAL_AUDIO, 2, dry_run=True, backup=False)
    args = shlex.split(caplog.text.split("[dry-run] ", 1)[1])
    assert args[args.index("--output-charset") + 1] == "UTF-8"


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

    assert ssd.apply_mkv(tmp_path / "v.mkv", ORIGINAL_AUDIO, 2, dry_run=True, backup=False)

    assert dry_run_flags(caplog) == ["2:no", "3:yes"]


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

    assert ssd.apply_mkv(video, ORIGINAL_AUDIO, 2, dry_run=False, backup=False) is False

    assert message in caplog.text
    assert video.read_bytes() == b"original"


@pytest.mark.parametrize("returncode, stdout, expected", [
    (0, json.dumps({"tracks": [{"id": 0, "type": "video"}, {"id": 1, "type": "audio"},
                               {"id": 2, "type": "subtitles"}, {"id": 3, "type": "audio"}]}),
     [1, 3]),
    (0, json.dumps({"container": {"recognized": False}, "errors": []}), []),
    (2, "", None),
    (0, "not json", None),
], ids=["audio tracks only", "unrecognized file", "mkvmerge failed", "bad output"])
def test_mkvmerge_audio_ids(monkeypatch, returncode, stdout, expected):
    monkeypatch.setattr(ssd, "run", lambda cmd, **kw: types.SimpleNamespace(
        returncode=returncode, stdout=stdout, stderr=""))
    assert real_mkvmerge_audio_ids(Path("v.mkv")) == expected


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
        ssd.run_with_progress(SLOW_CMD, "x", True, parse_pct_line)
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
    """Create count placeholder videos in folder: e00.mkv, e01.mkv, ... Only
    their names matter, since the tests that use them replace
    process_file()."""
    for i in range(count):
        (folder / f"e{i:02}.mkv").write_text("x")


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
    """Both threads log their header line, wait until the other has too, then
    log a later line, so the later lines always follow the other file's
    header. Each is printed under its own header, repeated for it."""
    caplog.set_level("INFO")
    barrier = threading.Barrier(2, timeout=5)

    def work(i, name):
        header = f"[{i}/2] {name}"
        with ssd.file_context(header):
            ssd.log.info(f"\n{header}\n  {name} checked")
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


def test_a_files_lines_print_straight_away_without_repeating_its_own_header(caplog):
    caplog.set_level("INFO")
    with ssd.file_context("[1/1] a"):
        ssd.log.info("\n[1/1] a\n  a checked")
        assert len(caplog.records) == 1
        ssd.log.info("    a command")
        assert caplog.records[-1].getMessage() == "    a command"


def test_a_files_first_line_gets_its_header_if_it_lacks_one(caplog):
    """e.g. "ffprobe failed on ...", logged before the header has been."""
    caplog.set_level("INFO")
    with ssd.file_context("[1/1] a"):
        ssd.log.error("  ffprobe failed on a")
    assert caplog.records[0].getMessage() == "\n[1/1] a\n  ffprobe failed on a"


def test_lines_outside_a_file_pass_straight_through(caplog):
    caplog.set_level("INFO")
    ssd.log.info("Found 2 file(s).")
    assert caplog.records[0].getMessage() == "Found 2 file(s)."


def test_jobs_keeps_each_files_lines_under_its_own_header(tmp_path, monkeypatch, capsys):
    """The run from the bug report: with --jobs, each file logs its header,
    then later its command. Both files are made to log their header before
    either logs its command, which used to put both commands under the
    second header. Lines must still print as they happen, not at the end."""
    make_videos(tmp_path, 2)
    barrier = threading.Barrier(2, timeout=5)

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        ssd.log.info(f"\n{header}\n  {path.name} checked")
        barrier.wait()
        ssd.log.info(f"    {path.name} command")
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path),
                                      "--no-progress", "--jobs", "2"])
    ssd.main()

    pairs = [(owner, line) for owner, line in under_headers(capsys.readouterr().out.splitlines())
             if line.endswith((" checked", " command"))]
    assert len(pairs) == 4
    assert all(line.split()[0] == owner for owner, line in pairs)


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

    def fake_process_file(path, args, position=0, header="", on_progress=None):
        calls.append(path.name)
        if interrupted and len(calls) == 2:
            raise KeyboardInterrupt
        return "changed"

    monkeypatch.setattr(ssd, "check_tools", lambda need_mkvmerge: None)
    monkeypatch.setattr(ssd, "process_file", fake_process_file)
    monkeypatch.setattr(sys, "argv", ["set_stereo_default.py", str(tmp_path), "--no-progress",
                                      *(["--dry-run"] if dry_run else [])])

    try:
        ssd.main()
    except SystemExit:
        pass

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

    with pytest.raises(SystemExit) as exit_info:
        ssd.main()

    out = capsys.readouterr().out
    assert exit_info.value.code == code
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
