"""``serve`` wiring without starting a server: settings reach the reloading child."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from ragnalog.cli.app import run
from ragnalog.config import ENV_VARS
from ragnalog.webapp import Settings

ENV_KEYS = tuple(var.name for var in ENV_VARS)


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
    assert args == ("ragnalog.webapp.app:create_app",)
    assert kwargs["factory"] is True and kwargs["reload"] is True
    child = Settings.from_env()  # what the reloaded factory builds
    assert child == Settings(store=store, max_upload_mb=7, token="s3cret")
    assert os.environ["RAGNALOG_TOKEN"] == "s3cret"


def test_reload_without_token_clears_it(calls: list[Any], tmp_path: Path) -> None:
    assert run(["--store", str(tmp_path / "s"), "serve", "--reload"]) == 0
    child = Settings.from_env()
    assert child.token is None and child.max_upload_mb == 2048
    assert child.store == tmp_path / "s"


def test_serve_env_max_upload(calls: list[Any], tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("RAGNALOG_MAX_UPLOAD_MB", "9")
    assert run(["--store", str(tmp_path / "s"), "serve"]) == 0
    (args, _), *_ = calls
    assert args[0].state.ragnalog.settings.max_upload_mb == 9


def test_settings_env_round_trip(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    settings = Settings(
        store=tmp_path,
        max_upload_mb=5,
        token="t",
        report_cache=3,
        app_packages=("org.acme.", "com.example."),
        debug=True,
    )
    for key, value in settings.to_env().items():
        monkeypatch.setenv(key, value)
    assert Settings.from_env() == settings


def test_serve_rejects_bad_max_upload(tmp_path: Path) -> None:
    assert run(["--output", "json", "--store", str(tmp_path), "serve", "--max-upload-mb", "0"]) == 1


def test_reload_passes_app_packages(calls: list[Any], tmp_path: Path, monkeypatch: Any) -> None:
    monkeypatch.setenv("RAGNALOG_APP_PACKAGES", "org.acme.")
    assert run(["--store", str(tmp_path / "s"), "serve", "--reload"]) == 0
    assert Settings.from_env().app_packages == ("org.acme.",)


def test_network_bind_without_token_warns(
    calls: list[Any], tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(["--store", str(tmp_path / "s"), "serve", "--host", "0.0.0.0"]) == 0
    assert "no --token is set" in capsys.readouterr().err
    assert run(["--store", str(tmp_path / "s"), "serve", "--host", "0.0.0.0", "--token", "t"]) == 0
    assert run(["--store", str(tmp_path / "s"), "serve"]) == 0  # loopback: no warning
    assert "no --token" not in capsys.readouterr().err


def test_allowed_host_compared_without_port_or_case(calls: list[Any], tmp_path: Path) -> None:
    from fastapi.testclient import TestClient

    argv = ["--store", str(tmp_path / "s"), "serve", "--allowed-host", "Logs.Example.org:443"]
    assert run(argv) == 0
    (args, _), *_ = calls
    with TestClient(args[0], base_url="http://127.0.0.1:8321") as c:
        assert c.get("/api/sets", headers={"Host": "logs.example.org"}).status_code == 200
        assert c.get("/api/sets", headers={"Host": "[::1]:8321"}).status_code == 200
        assert c.get("/api/sets", headers={"Host": "other.example"}).status_code == 400


def test_reload_factory_checks_host_on_loopback(calls: list[Any], tmp_path: Path) -> None:
    """The reloading child is not told its address: it checks per connection."""
    from fastapi.testclient import TestClient

    from ragnalog.webapp import create_app

    assert run(["--store", str(tmp_path / "s"), "serve", "--reload"]) == 0
    app = create_app()  # what uvicorn's factory call builds
    with TestClient(app, base_url="http://127.0.0.1:8321") as c:
        assert c.get("/api/sets").status_code == 200
        assert c.get("/api/sets", headers={"Host": "rebind.example:8321"}).status_code == 400
    with TestClient(create_app(), base_url="http://192.0.2.7:8321") as c:  # on the network
        assert c.get("/api/sets", headers={"Host": "logs.example.org"}).status_code == 200
