"""Property-based tests, with Hypothesis: random inputs for the script's
decisions that don't need real files, checking what must hold for every
input rather than for a few chosen examples. Each property runs many
examples, and a failure is shrunk to the smallest input that still fails
and printed with it. They need the hypothesis package (requirements-dev.txt)
and are skipped without it. SSD_FUZZ_CASES, as in test_real_files.py,
scales how many examples each property tries: five times as many."""

import dataclasses
import os
import random
import shlex
import tempfile
from pathlib import Path

import pytest

import set_stereo_default as ssd

hypothesis = pytest.importorskip("hypothesis", reason="the property tests need the hypothesis package")
st = pytest.importorskip("hypothesis.strategies")
given, settings, assume = hypothesis.given, hypothesis.settings, hypothesis.assume

settings.register_profile("ssd", max_examples=int(os.environ.get("SSD_FUZZ_CASES", "30")) * 5,
                          deadline=None)
settings.load_profile("ssd")

LANGUAGES = ["", "und", "eng", "en", "spa", "es", "jpn", "ja", "fre", "fra", "de", "ger", "deu", "pt-BR"]
"""Language tags as files really have them: missing, unknown, and the same
language in its two- and three-letter forms, with a region now and then."""

NAMES = ["Stereo", "Surround 5.1", "Director's Commentary", "Audio Description", "DVS",
         "Signs & Songs", "Español", "日本語"]
"""Track names, some of which mark commentary or audio description."""

FLAGS = ["forced", "hearing_impaired", "original", "dub", "comment"]
"""Flags ffprobe reports besides default, commentary and visual_impaired."""


def audio_track():
    """An audio stream as probe_streams() describes one, with any index."""
    return st.builds(ssd.Stream, index=st.just(0), type=st.just("audio"),
                     codec=st.sampled_from(["aac", "ac3", "eac3", "dts", "opus", "truehd"]),
                     channels=st.sampled_from([1, 2, 2, 6, 8]), default=st.booleans(),
                     comment=st.booleans(), visual_impaired=st.booleans(),
                     language=st.sampled_from(LANGUAGES),
                     names=st.lists(st.sampled_from(NAMES), max_size=2).map(tuple),
                     flags=st.frozensets(st.sampled_from(FLAGS), max_size=2))


def other_stream():
    """A video, subtitle, attachment or data stream, with any index."""
    kinds = [("video", "h264"), ("video", "hevc"), ("subtitle", "subrip"), ("subtitle", "ass"),
             ("attachment", "ttf"), ("data", "bin_data")]
    return st.builds(lambda kind, default, flags, names: ssd.Stream(
        0, kind[0], kind[1], default=default, flags=flags, names=names),
        kind=st.sampled_from(kinds), default=st.booleans(),
        flags=st.frozensets(st.sampled_from(FLAGS), max_size=1),
        names=st.lists(st.sampled_from(NAMES), max_size=1).map(tuple))


def numbered(streams):
    """streams with their indexes set to 0, 1, 2, ... as ffprobe numbers them."""
    return [dataclasses.replace(s, index=i) for i, s in enumerate(streams)]


def audio_tracks(min_size=1):
    """One to six audio streams, numbered from 1 as if a video came first."""
    return st.lists(audio_track(), min_size=min_size, max_size=6).map(
        lambda tracks: [dataclasses.replace(t, index=i) for i, t in enumerate(tracks, start=1)])


PREFERRED = st.one_of(st.none(), st.sampled_from([c for c in LANGUAGES if c not in ("", "und")]))
"""--prefer-lang: unset, or a real language tag."""


@given(streams=audio_tracks(), prefer=PREFERRED)
def test_choose_target_only_ever_picks_a_stereo_track_that_isnt_commentary(streams, prefer):
    """Whatever the tracks, the pick is one of them, stereo and not
    commentary or audio description; otherwise there's a reason."""
    target, note = ssd.choose_target(streams, prefer)

    if target is None:
        assert isinstance(note, str) and note
    else:
        assert target in streams
        assert target.channels == 2
        assert not ssd.is_commentary(target)


@given(streams=audio_tracks(), prefer=PREFERRED)
def test_choose_target_keeps_the_language_players_start_on(streams, prefer):
    """A picked track is in the language of the track players start on now
    (the default one, or the first), or has none, so a stereo dub never
    replaces the original language. With --prefer-lang, it's in that
    language instead, unless no stereo track is, in which case the usual
    rule applies and the note says so. And the rule is the only reason a
    lone stereo track isn't picked."""
    target, note = ssd.choose_target(streams, prefer)
    current = next((s for s in streams if s.default), streams[0])

    def allowed(track, code):
        """Whether track is in the language code, or has none."""
        wanted, got = ssd.normalize_language(code), ssd.normalize_language(track.language)
        return wanted in ("", "und") or got in ("", "und", wanted)

    if target is None:
        assert "2-channel" in note
        candidates = [s for s in streams if s.channels == 2 and not ssd.is_commentary(s)]
        if len(candidates) == 1 and prefer is None:
            assert not allowed(candidates[0], current.language)
    elif prefer is None:
        assert note is None
        assert allowed(target, current.language)
    elif note is None:
        assert allowed(target, prefer)
    else:
        assert "so picked as if --prefer-lang wasn't given" in note
        assert allowed(target, current.language)


@given(streams=audio_tracks(), prefer=PREFERRED, rnd=st.randoms(use_true_random=False))
def test_choose_target_doesnt_depend_on_track_order_when_one_track_is_default(streams, prefer, rnd):
    """With one track flagged default, the same track is picked whatever
    order the tracks come in: nothing about the choice is positional."""
    assume(sum(s.default for s in streams) == 1)
    shuffled = list(streams)
    rnd.shuffle(shuffled)

    target, _ = ssd.choose_target(streams, prefer)
    again, _ = ssd.choose_target(shuffled, prefer)

    assert (target is None) == (again is None)
    assert target is None or target.index == again.index


@given(code=st.one_of(st.none(), st.text()))
def test_normalize_language_gives_one_form_for_any_tag(code):
    """The result is lowercase, the same again if normalized again, the
    same for the tag in any case or with spaces around it, never a code
    the alias table would map further, and "" for no tag at all."""
    out = ssd.normalize_language(code)

    assert out == out.lower()
    assert ssd.normalize_language(out) == out
    assert ssd.LANGUAGE_ALIASES.get(out, out) == out
    if code is None:
        assert out == ""
    else:
        assert ssd.normalize_language(code.lower()) == out
        assert ssd.normalize_language(f"  {code}\t") == out


@given(code=st.text(alphabet=st.characters(exclude_characters="-_")),
       region=st.text(alphabet=st.characters(exclude_characters="-_"), min_size=1))
def test_normalize_language_drops_any_region_or_script(code, region):
    """"pt-BR", "pt_BR" and "pt" all mean Portuguese."""
    expected = ssd.normalize_language(code)

    assert ssd.normalize_language(f"{code}-{region}") == expected
    assert ssd.normalize_language(f"{code}_{region}") == expected


def chapter_track():
    """An MP4 or MOV chapter track as probe_streams() describes one: a
    data stream tagged "text" or "tx3g", with whatever name and language
    the tool that wrote it gave it, and any index."""
    return st.builds(lambda tag, names, language: ssd.Stream(0, "data", "bin_data", language=language,
                                                             names=names, tag=tag),
                     tag=st.sampled_from(["text", "tx3g"]),
                     names=st.sampled_from([(), ("Chapters",), ("SubtitleHandler",), ("Chapter",)]),
                     language=st.sampled_from(["", "und", "eng", "jpn"]))


def layouts(extra=()):
    """A whole file as probe_streams() describes it, with a stereo track
    that choose_target() could pick: (layout, the audio streams, the
    target). The video comes first, as in every real file, then the rest,
    extra streams too, in any order."""
    def build(video, tracks, others, rnd):
        rest = tracks + others + list(extra)
        rnd.shuffle(rest)
        layout = numbered([video] + rest)
        audio = [s for s in layout if s.type == "audio"]
        stereo = [s for s in audio if s.channels == 2]
        return layout, audio, rnd.choice(stereo)

    return st.builds(build, video=other_stream().map(lambda s: dataclasses.replace(s, type="video")),
                     tracks=st.lists(audio_track(), min_size=1, max_size=5).filter(
                         lambda tracks: any(t.channels == 2 for t in tracks)),
                     others=st.lists(other_stream(), max_size=4), rnd=st.randoms(use_true_random=False))


@st.composite
def plans(draw):
    """A Plan as _process_file() makes one, for a file of a random
    container, with the remux that should pass its checks: (plan, after,
    after's duration). For an AVI reorder (plan.reordered) the remux has
    the target first; otherwise it keeps the order, with the default flag
    on the target alone.

    An MP4, M4V or MOV file may have a chapter track anywhere after the
    video. ffmpeg leaves the original's out and writes a new one, last,
    with a name and language of its own, so the remux has that instead."""
    suffix = draw(st.sampled_from([".mkv", ".webm", ".mp4", ".mov", ".m4v", ".avi"]))
    has_chapters = suffix in ssd.MOV_FASTSTART_EXTS and draw(st.booleans())
    layout, audio, target = draw(layouts([draw(chapter_track())] if has_chapters else []))
    duration = draw(st.one_of(st.none(), st.floats(min_value=1.0, max_value=36000.0)))
    reordered = suffix == ".avi" and draw(st.booleans())
    plan = ssd.Plan(Path(f"v{suffix}"), audio, target.index, duration, layout=layout, reordered=reordered)
    if reordered:
        return plan, numbered(ssd._expected_order(plan)), duration
    after = [dataclasses.replace(s, default=s.index == target.index) if s.type == "audio" else s
             for s in layout if not ssd.is_chapter_track(s)]
    if has_chapters:
        after.append(draw(chapter_track()))
    return plan, numbered(after), duration


def verified(plan, after, duration):
    """verify_remux(plan) with ffprobe answering after and duration for
    the remux, instead of reading a file."""
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ssd, "probe_streams", lambda path, report=True: (after, duration))
        return ssd.verify_remux(plan)


@given(case=plans())
def test_a_remux_that_kept_everything_passes_the_check(case):
    """The remux as it should come out: every stream as it was, in the
    expected order, the default flag on the target alone (or, for an AVI,
    the target first), the same duration. No problem, no warning."""
    plan, after, duration = case

    assert verified(plan, after, duration) == ssd.Verification()


@given(case=plans(), data=st.data())
def test_a_remux_that_lost_anything_is_rejected(case, data):
    """Any one loss is caught: a stream gone or added, a stream's type,
    codec or channel count changed, a known language changed to another,
    a name gone, the default flag on another audio track or off the
    target, or the file much shorter. In an MKV, a lost flag is caught too;
    other containers can't hold those flags, so there it's a warning and
    the remux still passes.

    A chapter track counts as lost if it's gone, copied twice (the bug
    ffmpeg's own chapter track used to cause), or no longer a chapter
    track; and a data stream that became one counts as a stream lost. Its
    codec, name, language and flags are ffmpeg's to choose, since it
    writes the track afresh, so changing those isn't a loss."""
    plan, after, duration = case
    i = data.draw(st.integers(0, len(after) - 1), label="stream")
    s = after[i]
    chapters = ssd.is_chapter_track(s)
    changes = ["remove", "add", "type"] + ([] if chapters else ["codec"])
    if s.type == "data":
        changes.append("tag")
    if s.type == "audio":
        changes.append("channels")
    if not chapters and ssd.normalize_language(s.language) not in ("", "und"):
        changes.append("language")
    if not chapters and s.names:
        changes.append("name")
    if not chapters and s.flags:
        changes.append("flag")
    if duration is not None:
        changes.append("duration")
    if not plan.reordered and s.type == "audio":
        changes.append("default")
    change = data.draw(st.sampled_from(changes), label="change")

    if change == "remove":
        after = after[:i] + after[i + 1:]
    elif change == "add":
        after = after + [dataclasses.replace(s, index=len(after))]
    elif change == "type":
        after[i] = dataclasses.replace(s, type="data" if s.type != "data" else "video")
    elif change == "tag":
        after[i] = dataclasses.replace(s, tag="tmcd" if chapters else "text")
    elif change == "codec":
        after[i] = dataclasses.replace(s, codec=s.codec + "2")
    elif change == "channels":
        after[i] = dataclasses.replace(s, channels=(s.channels or 0) + 1)
    elif change == "language":
        after[i] = dataclasses.replace(s, language="jpn" if ssd.normalize_language(s.language) != "jpn"
                                       else "spa")
    elif change == "name":
        after[i] = dataclasses.replace(s, names=())
    elif change == "flag":
        after[i] = dataclasses.replace(s, flags=frozenset())
    elif change == "duration":
        duration = duration - max(duration * ssd.MAX_DURATION_LOSS, ssd.MIN_DURATION_LOSS) - 0.1
    else:
        after[i] = dataclasses.replace(s, default=not s.default)

    result = verified(plan, after, duration)

    if change == "flag" and plan.path.suffix not in ssd.MKV_EXTS:
        assert result.problem is None
        assert len(result.notes) == 1 and "lost its" in result.notes[0]
    else:
        assert result.problem
        assert result.notes == ()


@given(case=plans(), data=st.data())
def test_a_remux_that_only_gained_something_passes_the_check(case, data):
    """What a tool may add loses nothing: a name (ffmpeg names MP4 tracks
    "SoundHandler"), a language for a track that had none ("und", or a
    real one), a flag, or a longer duration than the header claimed."""
    plan, after, duration = case
    i = data.draw(st.integers(0, len(after) - 1), label="stream")
    s = after[i]
    changes = ["name", "flag"]
    if ssd.normalize_language(s.language) in ("", "und"):
        changes.append("language")
    if duration is not None:
        changes.append("duration")
    change = data.draw(st.sampled_from(changes), label="change")

    if change == "name":
        after[i] = dataclasses.replace(s, names=s.names + ("SoundHandler",))
    elif change == "flag":
        after[i] = dataclasses.replace(s, flags=s.flags | {"clean_effects"})
    elif change == "language":
        after[i] = dataclasses.replace(s, language="und" if s.language == "" else "eng")
    else:
        duration = duration * 1.5

    assert verified(plan, after, duration) == ssd.Verification()


@given(case=plans())
def test_ffmpeg_leaves_out_exactly_the_chapter_tracks(case):
    """ffmpeg's command copies every stream (-map 0) but the original's
    chapter tracks, one -map -0:<index> each, since it writes its own; no
    other stream is ever left out. (An AVI reorder maps streams one by one
    instead, and AVI has no chapter track.)"""
    plan, _, _ = case
    assume(not plan.reordered)
    logged = []

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(ssd, "_announce", lambda intro, line=None: logged.append(line))
        assert ssd.apply_remux(plan, ssd.Options(dry_run=True, no_progress=True))

    args = shlex.split(logged[0].split("[dry-run] ", 1)[1])
    maps = args[args.index("-i") + 2:args.index("-c")]
    chapters = [s.index for s in plan.layout if ssd.is_chapter_track(s)]
    assert maps == ["-map", "0"] + [arg for i in chapters for arg in ("-map", f"-0:{i}")]


NAME_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789 -_'.éß日本"
"""Characters file names are made of here: safe on every file system, in
one case only (so two names can't collide on a case-insensitive one), and
with spaces and apostrophes, which --input-file lines may quote."""

RESERVED_NAMES = {"con", "prn", "aux", "nul"} | {f"{d}{n}" for d in ("com", "lpt") for n in range(1, 10)}
"""Names Windows reserves for devices, whatever their extension."""

file_names = st.text(alphabet=NAME_CHARS, min_size=1, max_size=20).filter(
    lambda n: n.strip(" .") == n and n not in (".", "..") and n.split(".")[0].lower() not in RESERVED_NAMES)
"""File names as above, never starting or ending with a space or dot
(Windows drops those), and never a device name."""


@given(name=file_names, form=st.sampled_from(["plain", "single-quoted", "double-quoted", "escaped"]))
def test_an_input_file_line_names_an_existing_file_however_its_written(name, form):
    """A line of an --input-file finds the file whether it's written
    plainly, quoted the way a shell or ls writes it, or with its spaces
    and apostrophes escaped, as long as the file exists."""
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        try:
            (base / name).write_bytes(b"")
        except OSError:
            assume(False)
        line = {"plain": name, "single-quoted": shlex.quote(name) if name != shlex.quote(name)
                else f"'{name}'", "double-quoted": '"' + name + '"',
                "escaped": name.replace(" ", "\\ ").replace("'", "\\'")}[form]

        assert ssd._input_path(line, base) == str(base / name)


@given(name=file_names, quoted=st.booleans())
def test_an_input_file_line_for_a_missing_file_still_names_it_readably(name, quoted):
    """A line that names nothing that exists comes back as the path it
    would have been, unquoted if it was quoted, so the warning that it
    doesn't exist reads naturally."""
    with tempfile.TemporaryDirectory() as folder:
        base = Path(folder)
        line = f"'{name}'" if quoted else name
        assume("'" not in name)

        assert ssd._input_path(line, base) == str(base / name)


EXTS = [".mkv", ".MKV", ".mp4", ".Mp4", ".webm", ".avi", ".mov", ".m4v", ".txt", ".srt", ".nfo", ""]
"""Extensions a library holds, in any case: videos, and files to leave alone."""


def no_file_is_also_a_folder(paths):
    """Whether paths can all exist at once: none of them names a folder
    another one is in (compared ignoring case, for Windows and macOS)."""
    folders = {str(parent).lower() for p in paths for parent in Path(p).parents}
    return not any(p.lower() in folders for p in paths)


relative_paths = st.lists(
    st.builds(lambda parts, stem, ext: str(Path(*parts, stem + ext)),
              parts=st.lists(st.text(alphabet="abcdefghijklmnopqrstuvwxyz", min_size=1, max_size=4),
                             max_size=2),
              stem=st.text(alphabet="abcdefghijklmnopqrstuvwxyz0123456789 _-", min_size=1, max_size=8)
              .filter(lambda s: s.strip() == s),
              ext=st.sampled_from(EXTS)),
    max_size=12, unique_by=lambda p: p.lower()).filter(no_file_is_also_a_folder)
"""Files for a small library: up to two folders deep, unique even on a
case-insensitive file system, and with a temp-marker name and a backup
added by with_leftovers()."""


def with_leftovers(paths):
    """paths, plus the temp file an interrupted run would have left next to
    the first video, and that file's backup."""
    videos = [p for p in paths if Path(p).suffix.lower() in ssd.DEFAULT_EXTS]
    if videos:
        p = videos[0]
        paths = paths + [p + ssd.TMP_MARKER + Path(p).suffix, p + ".bak"]
    return paths


@given(paths=relative_paths.map(with_leftovers), recursive=st.booleans(),
       exts=st.sampled_from([ssd.DEFAULT_EXTS, {".mkv"}, {".mp4", ".mov"}]))
def test_iter_files_finds_exactly_the_videos_once_each_in_order(paths, recursive, exts):
    """Searching a folder yields every file with a wanted extension (in any
    case), in its subfolders too when recursive, each as an absolute path,
    once, in sorted order; and nothing else: no other files, no leftover
    temp file from an interrupted run, no backup. A video also named on its
    own, or named twice, is still yielded once."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        for p in paths:
            (root / p).parent.mkdir(parents=True, exist_ok=True)
            (root / p).write_bytes(b"")
        wanted = sorted(root / p for p in paths
                        if (root / p).suffix.lower() in exts and not (root / p).stem.endswith(ssd.TMP_MARKER)
                        and (recursive or (root / p).parent == root))
        extra = wanted[:1] * 2

        found = list(ssd.iter_files([*extra, root], exts, recursive))

        assert found == extra[:1] + [p for p in wanted if p not in extra]
        assert all(p.is_absolute() for p in found)
        assert len(found) == len(set(found))


@given(data=st.data())
def test_iter_files_keeps_the_order_the_paths_were_given_in(data):
    """Paths are searched in the order given, each one's files sorted, so
    listing one show before another processes it first."""
    with tempfile.TemporaryDirectory() as folder:
        root = Path(folder).resolve()
        shows = data.draw(st.lists(st.text(alphabet="abcdefghij", min_size=1, max_size=3),
                                   min_size=1, max_size=4, unique=True), label="shows")
        counts = data.draw(st.lists(st.integers(0, 3), min_size=len(shows), max_size=len(shows)),
                           label="episodes")
        expected = []
        for show, count in zip(shows, counts, strict=True):
            (root / show).mkdir()
            names = random.Random(show).sample("abcdefghijklmnop", count)
            for n in names:
                (root / show / f"{n}.mkv").write_bytes(b"")
            expected += sorted(root / show / f"{n}.mkv" for n in names)

        assert list(ssd.iter_files([root / show for show in shows], {".mkv"}, False)) == expected
