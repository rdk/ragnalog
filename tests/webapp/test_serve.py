"""``serve`` wiring without starting a server: settings reach the reloading child."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from atlas_log_analyzer.cli.app import run
from atlas_log_analyzer.webapp import Settings

ENV_KEYS = (
    "ATLAS_LOG_STORE",
    "ATLAS_LOG_TOKEN",
    "ATLAS_LOG_MAX_UPLOAD_MB",
    "ATLAS_LOG_REPORT_CACHE",
)


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[tuple[tuple[Any, ...], dict[str, Any]]]:
    """Capture ``uvicorn.run`` calls; the environment is restored after the test."""
    import uvicorn

    # serve --reload writes into os.environ: setenv records each variable's original
    # state so that monkeypatch restores it, delenv starts the test without it
    for key in ENV_KEYS:
        monkeypatch.setenv(key, "")
        monkeypatch.delenv(key)
    seen: list[tuple[tuple[Any, ...], dict[str, Any]]] = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: seen.append((a, k)))
    return seen


def test_reload_passes_every_setting(calls: list[Any], tmp_path: Path) -> None:
    store = tmp_path / "store"
    argv = ["--store", store, "serve", "--reload", "--token", "s3cret", "--max-upload-mb", "7"]
    assert run([str(a) for a in argv]) == 0
    (args, kwargs), *_ = calls
    assert args == ("atlas_log_analyzer.webapp.app:create_app",)
    assert kwargs["factory"] is True and kwargs["reload"] is True
    child = Settings.from_env()  # what the reloaded factory builds
    assert child == Settings(store=store, max_upload_mb=7, token="s3cret")
    assert os.environ["ATLAS_LOG_TOKEN"] == "s3cret"


def test_reload_without_token_clears_it(calls: list[Any], tmp_path: Path) -> None:
    assert run(["--store", str(tmp_path / "s"), "serve", "--reload"]) == 0
    child = Settings.from_env()
    assert child.token is None and child.max_upload_mb == 2048
    assert child.store == tmp_path / "s"


def test_serve_env_max_upload(calls: list[Any], tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("ATLAS_LOG_MAX_UPLOAD_MB", "9")
    assert run(["--store", str(tmp_path / "s"), "serve"]) == 0
    (args, _), *_ = calls
    assert args[0].state.atlas.settings.max_upload_mb == 9


def test_settings_env_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(store=tmp_path, max_upload_mb=5, token="t", report_cache=3)
    for key, value in settings.to_env().items():
        monkeypatch.setenv(key, value)
    assert Settings.from_env() == settings


def test_serve_rejects_bad_max_upload(tmp_path: Path) -> None:
    assert run(["--output", "json", "--store", str(tmp_path), "serve", "--max-upload-mb", "0"]) == 1
