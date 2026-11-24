"""``ragnalog.build``: the build an image records, a git checkout's, or none."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Iterator
from pathlib import Path

import pytest

from ragnalog import build as build_module
from ragnalog.build import Build, build


@pytest.fixture(autouse=True)
def _fresh() -> Iterator[None]:
    build.cache_clear()
    yield
    build.cache_clear()


def _image(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, **values: str) -> None:
    path = tmp_path / "build.json"
    path.write_text(json.dumps(values), encoding="utf-8")
    monkeypatch.setenv("RAGNALOG_BUILD_FILE", str(path))


def test_an_image_records_its_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    _image(tmp_path, monkeypatch, number="412", git="e141465", time="2026-11-24T19:58:12Z")
    found = build()
    assert found == Build(412, "e141465", "2026-11-24T19:58:12Z", "image")
    assert found.label() == "build 412"
    assert found.title() == "built 2026-11-24 19:58:12 UTC · git e141465"
    assert found.doc() == {
        "number": 412,
        "git": "e141465",
        "time": "2026-11-24T19:58:12Z",
        "source": "image",
    }


def test_an_image_built_without_arguments_has_its_time(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _image(tmp_path, monkeypatch, number="", git="", time="2026-11-24T19:58:12Z")
    found = build()
    assert (found.number, found.git, found.label()) == (None, None, "")
    assert found.title() == "built 2026-11-24 19:58:12 UTC"


def test_values_that_do_not_parse_are_dropped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _image(tmp_path, monkeypatch, number="4x", git="not-a-hash", time="yesterday")
    assert build() == Build(source="image")
    _image(tmp_path, monkeypatch, number="9", git="E141465ABCDEF", time="")
    build.cache_clear()
    assert build() == Build(9, "e141465", None, "image")  # a long hash is cut to 7


def test_a_checkout_reads_its_git(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RAGNALOG_BUILD_FILE", raising=False)
    root = Path(build_module.__file__).resolve().parents[2]
    if not (root / ".git").exists():
        pytest.skip("not a git checkout")

    def git(*args: str) -> str:
        return subprocess.run(
            ["git", "-C", str(root), *args], capture_output=True, text=True, check=True
        ).stdout.strip()

    found = build()
    assert found.source == "checkout" and found.time is None
    assert found.number == int(git("rev-list", "--count", "HEAD"))
    assert found.git == git("rev-parse", "HEAD")[:7]
    assert found.title() == f"a source checkout · git {found.git}"


def test_nothing_known(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RAGNALOG_BUILD_FILE", str(tmp_path / "missing.json"))
    monkeypatch.setattr(build_module, "_from_checkout", lambda root: None)
    assert build() == Build() and build().label() == "" and build().title() == ""


def test_version_and_pages_show_the_build(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("fastapi")
    from fastapi.testclient import TestClient

    from ragnalog.service.meta import version
    from ragnalog.webapp import Settings, create_app

    _image(tmp_path, monkeypatch, number="412", git="e141465", time="2026-11-24T19:58:12Z")
    assert version()["build"]["number"] == 412
    from ragnalog.webapp.pages import _head

    assert (
        '<meta name="ragnalog-build" content="build 412"'
        ' data-title="built 2026-11-24 19:58:12 UTC · git e141465">'
    ) in _head()  # the report and cluster pages' footers read it
    (tmp_path / "store").mkdir()
    with TestClient(create_app(Settings(store=tmp_path / "store"))) as c:
        index = c.get("/").text
        assert (
            '<span class="foot-build" title="built 2026-11-24 19:58:12 UTC · git e141465">'
            "build 412</span>"
        ) in index
        assert c.get("/api/version").json()["build"]["git"] == "e141465"
