"""The implicit default store ``./.atlas-log`` is not created inside a git work tree."""

from __future__ import annotations

from pathlib import Path

import pytest

from .conftest import MINI_LOG, Invoke


@pytest.fixture
def no_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_LOG_STORE", raising=False)


@pytest.fixture
def repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_env: None) -> Path:
    """A git work tree (a ``.git`` directory) with the current directory in a subfolder."""
    root = tmp_path / "repo"
    (root / ".git").mkdir(parents=True)
    work = root / "src" / "pkg"
    work.mkdir(parents=True)
    monkeypatch.chdir(work)
    return root


def _outside_git(path: Path) -> bool:
    return not any((p / ".git").exists() for p in (path, *path.parents))


def test_ingest_refuses_default_store_in_repo(cli: Invoke, repo: Path) -> None:
    res = cli("--output", "json", "ingest", MINI_LOG)
    assert res.code == 3
    err = res.json()["error"]
    assert err["code"] == "store_error"
    assert "committing real logs" in err["message"] and repo.name in err["message"]
    assert "--store" in err["hint"] and "ATLAS_LOG_STORE" in err["hint"]
    assert not Path(".atlas-log").exists()


def test_git_file_counts_as_work_tree(cli: Invoke, tmp_path: Path, monkeypatch, no_env) -> None:
    root = tmp_path / "worktree"
    root.mkdir()
    (root / ".git").write_text("gitdir: ../main/.git/worktrees/x\n", encoding="utf-8")
    monkeypatch.chdir(root)
    assert cli("--output", "json", "ingest", MINI_LOG).code == 3


def test_explicit_store_or_env_is_allowed_in_repo(
    cli: Invoke, repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    explicit = cli("--output", "json", "--store", tmp_path / "s1", "ingest", MINI_LOG)
    assert explicit.code == 0, explicit.stdout
    monkeypatch.setenv("ATLAS_LOG_STORE", str(tmp_path / "s2"))
    assert cli("--output", "json", "ingest", MINI_LOG).code == 0
    assert (tmp_path / "s2").is_dir() and not Path(".atlas-log").exists()


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_env_counts_as_unset(
    cli: Invoke, repo: Path, monkeypatch: pytest.MonkeyPatch, blank: str
) -> None:
    """``ATLAS_LOG_STORE=`` must not make the current directory the store."""
    monkeypatch.setenv("ATLAS_LOG_STORE", blank)
    res = cli("--output", "json", "ingest", MINI_LOG)
    assert res.code == 3 and res.json()["error"]["code"] == "store_error"
    assert list(Path.cwd().iterdir()) == []
    listing = cli("--output", "json", "sets").json()
    assert listing["store"] == ".atlas-log"


def test_existing_default_store_keeps_working(cli: Invoke, repo: Path) -> None:
    made = cli("--output", "json", "--store", ".atlas-log", "ingest", MINI_LOG, "--name", "m")
    assert made.code == 0 and Path(".atlas-log").is_dir()
    assert cli("--output", "json", "groups", "m").code == 0
    again = cli("--output", "json", "ingest", MINI_LOG, "--name", "m")
    assert again.code == 0 and again.json()["ingest"]["existing"] is True


def test_read_commands_do_not_need_a_store(cli: Invoke, repo: Path) -> None:
    res = cli("--output", "json", "sets")
    assert res.code == 0 and res.json()["count"] == 0
    assert not Path(".atlas-log").exists()


def test_default_store_outside_repo(
    cli: Invoke, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, no_env: None
) -> None:
    work = tmp_path / "plain"
    work.mkdir()
    if not _outside_git(work):
        pytest.skip("the temporary directory is inside a git work tree")
    monkeypatch.chdir(work)
    res = cli("--output", "json", "ingest", MINI_LOG)
    assert res.code == 0, res.stdout
    assert (work / ".atlas-log").is_dir()


def test_serve_refuses_default_store_in_repo(cli: Invoke, repo: Path) -> None:
    pytest.importorskip("uvicorn")
    pytest.importorskip("fastapi")
    res = cli("--output", "json", "serve")
    assert res.code == 3 and res.json()["error"]["code"] == "store_error"
