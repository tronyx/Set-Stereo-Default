"""Checks that every link in the project's Markdown leads somewhere: the
README, the pages in docs/, the changelog and the files in .github. A
relative link must name a file that exists, and an #anchor must match a
heading in it, by the rules GitHub uses to make a heading's anchor. Links
to this repository's own files on GitHub, as in the issue forms and the
script's --help, must name a file that exists here too. Pure Python, so
it runs wherever the logic tests do."""

import re
import unicodedata
from pathlib import Path
from urllib.parse import unquote

import pytest

ROOT = Path(__file__).resolve().parent.parent
"""The repository's root folder."""

REPO_URL = re.compile(r"https://github\.com/tronyx/Set-Stereo-Default/(?:blob|tree)/[^/\s]+/([^\s)\"'#]+)(#[^\s)\"']+)?")
"""A link to one of this repository's files on GitHub, on any branch: the
file's path, and its #anchor if it has one."""


def markdown_files(root):
    """Every Markdown file of the project under root, but not the ones in
    a virtual environment or other folder a contributor may have added."""
    found = [root / name for name in ("README.md", "CHANGELOG.md", "LICENSE.md")]
    found += sorted((root / "docs").glob("**/*.md")) + sorted((root / ".github").glob("**/*.md"))
    return [path for path in found if path.is_file()]


def files_linking_to_the_repo(root):
    """The files besides Markdown that link to this repository's files on
    GitHub: the issue forms, and the script, whose --help ends with one."""
    return sorted((root / ".github" / "ISSUE_TEMPLATE").glob("*.yml")) + [root / "set_stereo_default.py"]


def without_code(text):
    """text with its fenced code blocks and inline code removed, since
    neither holds links or headings."""
    text = re.sub(r"^ *```.*?^ *```", "", text, flags=re.MULTILINE | re.DOTALL)
    return re.sub(r"`[^`\n]*`", "", text)


def github_slug(heading):
    r"""The anchor GitHub gives a heading: lowercased, with letters, marks,
    numbers, "-" and "_" kept, spaces turned into hyphens, and everything
    else dropped, emoji included. An emoji's variation selector (U+FE0F)
    is a mark, so it stays: "⚙️ Options" becomes "️-options"."""
    kept = []
    for char in heading.strip().lower():
        if char == " ":
            kept.append("-")
        elif char in "-_" or unicodedata.category(char)[0] in "LMN":
            kept.append(char)
    return "".join(kept)


def anchors(path):
    """The anchors of every heading in the Markdown file at path. A
    repeated heading gets "-1", "-2" and so on, as on GitHub."""
    seen, found = {}, set()
    for heading in re.findall(r"^#{1,6} +(.+?) *#* *$", without_code(path.read_text(encoding="utf-8")), re.MULTILINE):
        slug = github_slug(heading)
        count = seen.get(slug, 0)
        found.add(slug if count == 0 else f"{slug}-{count}")
        seen[slug] = count + 1
    return found


def problem_with(source, root, target_path, anchor):
    """Why a link from the file source to target_path (relative to root if
    it starts with "/", else to source's folder) with anchor ("" for none)
    leads nowhere, or None if it's fine."""
    target = (root / target_path.lstrip("/")) if target_path.startswith("/") else (source.parent / target_path)
    target = target.resolve() if target_path else source
    if not target.exists():
        return f"{target_path} doesn't exist"
    if anchor and target.suffix == ".md" and unquote(anchor) not in anchors(target):
        return f"{target_path or source.name} has no heading for #{anchor}"
    return None


def broken_links(root):
    """Every link under root that leads nowhere, as "file: link: why"."""
    problems = []
    for source in markdown_files(root):
        text = without_code(source.read_text(encoding="utf-8"))
        links = re.findall(r"\]\(<?([^)\s>]+)>?(?:\s+\"[^\"]*\")?\)", text)
        links += re.findall(r"^\[[^\]]+\]:\s*(\S+)", text, re.MULTILINE)
        for link in links:
            if re.match(r"^[a-z][a-z+.-]*:", link):
                continue
            path, _, anchor = link.partition("#")
            why = problem_with(source, root, unquote(path), anchor)
            if why:
                problems.append(f"{source.relative_to(root)}: {link}: {why}")
    for source in markdown_files(root) + files_linking_to_the_repo(root):
        for path, anchor in REPO_URL.findall(source.read_text(encoding="utf-8")):
            why = problem_with(source, root, "/" + path, anchor.lstrip("#"))
            if why:
                problems.append(f"{source.relative_to(root)}: {path}{anchor}: {why}")
    return problems


@pytest.mark.parametrize("heading, anchor", [
    ("🩺 Troubleshooting", "-troubleshooting"),
    ("🔊 set_stereo_default", "-set_stereo_default"),
    ("🌱 Seeding and cross-seeding will break", "-seeding-and-cross-seeding-will-break"),
    ("⚙️ Options", "️-options"),
    ("⚠️ Limitations", "️-limitations"),
], ids=["emoji dropped", "underscore kept", "hyphen kept", "variation selector kept", "another one"])
def test_headings_get_the_anchors_github_gives_them(heading, anchor):
    """Each of these is the anchor GitHub gave the heading on this
    project's own page, so the check below agrees with GitHub, not just
    with itself."""
    assert github_slug(heading) == anchor


def test_every_link_in_the_docs_leads_somewhere():
    """A page moved or renamed, or a heading reworded, breaks the links to
    it without a word, so every link is followed here instead."""
    assert broken_links(ROOT) == []


def test_a_broken_link_is_found(tmp_path):
    """The check itself: a missing file, a missing heading, a link to a
    repeated heading that has no second copy, and a link to this
    repository on GitHub naming a missing file are each reported; the
    links that are fine, a link in a code block, and links elsewhere
    aren't."""
    (tmp_path / "docs").mkdir()
    (tmp_path / ".github" / "ISSUE_TEMPLATE").mkdir(parents=True)
    (tmp_path / "set_stereo_default.py").write_text('"""Full guide: https://example.com"""\n', encoding="utf-8")
    (tmp_path / "docs" / "page.md").write_text("# 📄 Page\n\n## ⚙️ Options\n\n## Twice\n\n## Twice\n", encoding="utf-8")
    (tmp_path / "README.md").write_text(
        "# Read me\n\n"
        "[fine](docs/page.md#%EF%B8%8F-options) [fine](docs/page.md#twice-1) [fine](#read-me)\n"
        "[gone](docs/missing.md) [no heading](docs/page.md#-optionz) [no third](docs/page.md#twice-2)\n"
        "[elsewhere](https://example.com/x.md#y)\n\n"
        "```text\n[in code](nowhere.md)\n```\n",
        encoding="utf-8")
    (tmp_path / ".github" / "ISSUE_TEMPLATE" / "bug.yml").write_text(
        "url: https://github.com/tronyx/Set-Stereo-Default/blob/master/docs/page.md#-page\n"
        "other: https://github.com/tronyx/Set-Stereo-Default/blob/master/docs/gone.md\n",
        encoding="utf-8")

    problems = broken_links(tmp_path)

    assert [p.replace("\\", "/") for p in problems] == [
        "README.md: docs/missing.md: docs/missing.md doesn't exist",
        "README.md: docs/page.md#-optionz: docs/page.md has no heading for #-optionz",
        "README.md: docs/page.md#twice-2: docs/page.md has no heading for #twice-2",
        ".github/ISSUE_TEMPLATE/bug.yml: docs/gone.md: /docs/gone.md doesn't exist",
    ]
