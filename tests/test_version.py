"""The version is written in one place (pyproject.toml); everything that quotes it must agree.

A release bumps them together (see RELEASING.md); these tests catch the one that was missed:
the README's release badge, install and Docker commands, the version examples in the docs
and the CHANGELOG section of the release.
"""

import re
import tomllib
from pathlib import Path

import pytest

from ragnalog import __version__
from ragnalog._version import __version__ as literal

ROOT = Path(__file__).resolve().parents[1]
VERSION = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))["project"]["version"]
V = r"(\d+\.\d+\.\d+)"

QUOTED = {
    "README.md": [
        rf"badge/release-v{V}-",
        rf"ragnalog:{V}\b",
        rf"ragnalog\.git@v{V}\b",
        rf"releases/download/v{V}/",
        rf"ragnalog-{V}-py3",
    ],
    "docs/cli.md": [rf'"version": "{V}"'],
    "docs/report.md": [rf'"tool_version": "{V}"'],
}
"""Per file: patterns whose first group is the version; each must occur at least once."""


def test_version_literal_matches_pyproject() -> None:
    assert literal == VERSION
    assert __version__ == literal


@pytest.mark.parametrize(("name", "pattern"), [(n, p) for n, ps in QUOTED.items() for p in ps])
def test_quoted_versions_match(name: str, pattern: str) -> None:
    found = re.findall(pattern, (ROOT / name).read_text(encoding="utf-8"))
    assert found, f"{name}: no version quoted as {pattern!r} (the text moved? update QUOTED)"
    assert set(found) == {VERSION}, f"{name}: {sorted(set(found))} quoted, the version is {VERSION}"


def test_changelog_has_the_release() -> None:
    text = (ROOT / "CHANGELOG.md").read_text(encoding="utf-8")
    assert re.search(rf"^## \[{re.escape(VERSION)}\] - \d{{4}}-\d{{2}}-\d{{2}}$", text, re.M), (
        f"CHANGELOG.md has no '## [{VERSION}] - YYYY-MM-DD' section"
    )
    assert f"[{VERSION}]: https://github.com/rdk/ragnalog/releases/tag/v{VERSION}" in text
