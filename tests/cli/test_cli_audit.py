"""Audit fixes: one-statement query, diff --level errors, groups next_offset, batch line errors."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pytest

from atlas_log_analyzer.cli.app import run
from atlas_log_analyzer.config import Settings

from .conftest import MINI_LOG, SAMPLE_LOG, Invoke


@pytest.fixture
def ingested(jcli: Invoke) -> Invoke:
    assert jcli("ingest", MINI_LOG, "--name", "a").code == 0
    assert jcli("ingest", SAMPLE_LOG, "--name", "b").code == 0
    return jcli


def test_query_refuses_several_statements(ingested: Invoke) -> None:
    res = ingested("query", "a", "select 1; select 2")
    assert res.code == 1
    assert res.json()["error"]["code"] == "usage"
    assert "one SQL statement" in res.json()["error"]["message"]


def test_query_trailing_semicolon_is_fine(ingested: Invoke) -> None:
    res = ingested("query", "a", "select 1 as n;")
    assert res.code == 0
    assert res.json()["items"] == [{"n": 1}]


@pytest.mark.parametrize("flag", ["--level", "--min-level"])
def test_diff_invalid_level_names_the_typed_flag(ingested: Invoke, flag: str) -> None:
    res = ingested("diff", "a", "b", flag, "SEVERE")
    assert res.code == 1
    err = res.json()["error"]
    assert err["code"] == "invalid_level"
    assert err["message"].startswith(flag + ":")


def test_diff_level_is_min_level(ingested: Invoke) -> None:
    assert ingested("diff", "a", "b", "--level", "ERROR").code == 0


def test_groups_next_offset(ingested: Invoke) -> None:
    total = ingested("groups", "a").json()["total"]
    assert total >= 2
    first = ingested("groups", "a", "--limit", "1").json()
    assert first["next_offset"] == 1 and first["truncated"] is True
    last = ingested("groups", "a", "--limit", "1", "--offset", str(total - 1)).json()
    assert last["next_offset"] is None and last["truncated"] is False


def test_batch_errors_start_with_line_number(
    store_dir: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    lines = '["ingest"]\n["stats", "nope"]\n"x"\n'
    monkeypatch.setattr("sys.stdin", io.StringIO(lines))
    assert run(["--store", str(store_dir), "batch"]) == 0
    docs = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [d["schema"] for d in docs] == ["error.v1"] * 3
    for number, doc in enumerate(docs, start=1):
        assert doc["error"]["message"].startswith(f"line {number}: ")
    assert "PATH" in docs[0]["error"]["message"]


@pytest.mark.parametrize("blank", ["", "  "])
def test_blank_store_flag_is_not_given(blank: str) -> None:
    settings = Settings.load({"store": blank}, {"ATLAS_LOG_STORE": "/some/where"})
    assert settings.store == Path("/some/where")
    assert Settings.load({"store": blank}, {}).implicit_store is True


def test_blank_store_flag_in_cli(
    cli: Invoke, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    env_store = tmp_path / "env-store"
    monkeypatch.setenv("ATLAS_LOG_STORE", str(env_store))
    assert cli("--output", "json", "--store", "", "ingest", MINI_LOG).code == 0
    assert env_store.is_dir()
