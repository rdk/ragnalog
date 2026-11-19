"""``batch``: many commands in one process, one JSON document per input line."""

from __future__ import annotations

import io
import json
from pathlib import Path
from typing import Any

import pytest

from ragnalog.cli.app import run

from .conftest import MINI_LOG, Invoke


def _batch(
    lines: list[Any],
    store: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    *globals_: str,
) -> tuple[int, list[dict[str, Any]], str]:
    text = "\n".join(line if isinstance(line, str) else json.dumps(line) for line in lines)
    monkeypatch.setattr("sys.stdin", io.StringIO(text + "\n"))
    capsys.readouterr()
    code = run([*globals_, "--store", str(store), "batch"])
    captured = capsys.readouterr()
    return code, [json.loads(x) for x in captured.out.splitlines()], captured.err


@pytest.fixture
def mini(jcli: Invoke) -> str:
    assert jcli("ingest", MINI_LOG, "--name", "mini").code == 0
    return "mini"


def test_one_document_per_line_in_order(
    mini: str,
    store_dir: Path,
    jcli: Invoke,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    lines = [
        ["groups", mini, "--brief", "--limit", "2"],
        "",
        ["stats", "nope"],
        ["entries", mini, "--limit", "3"],
        ["version"],
    ]
    expected_entries = jcli("entries", mini, "--limit", "3").json()
    code, docs, _ = _batch(lines, store_dir, capsys, monkeypatch, "--output", "table")
    assert code == 0
    assert [d["schema"] for d in docs] == ["groups.v1", "error.v1", "entries.v1", "version.v1"]
    assert docs[1]["error"]["reason"] == "set_not_found" and "store" in docs[1]["error"]
    assert docs[2] == expected_entries  # the same document as a separate call


@pytest.mark.parametrize(
    ("line", "message"),
    [
        ("not json", "line 1: not JSON"),
        ('{"a": 1}', "line 1: expected a JSON array of strings"),
        ("[]", "line 1: expected a JSON array of strings"),
        ('["groups", 5]', "line 1: expected a JSON array of strings"),
        ('["parse", "x.log"]', "line 1: parse does not write one JSON document"),
        ('["serve"]', "line 1: serve does not write one JSON document"),
        ('["batch"]', "line 1: batch does not write one JSON document"),
        ('["groups", "mini", "--help"]', "line 1: --help and --version"),
        ('["entries", "mini", "--raw", "--limit", "1"]', "line 1: entries did not write one"),
    ],
)
def test_bad_lines_are_errors_and_never_stop(
    mini: str,
    store_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
    line: str,
    message: str,
) -> None:
    code, docs, _ = _batch([line, ["version"]], store_dir, capsys, monkeypatch)
    assert code == 0 and len(docs) == 2
    assert docs[0]["error"]["code"] == "usage" and docs[0]["error"]["message"].startswith(message)
    assert docs[1]["schema"] == "version.v1"


def test_global_options_inside_a_line_are_usage_errors(
    mini: str, store_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = [["groups", mini, "--output", "table"], ["--store", "elsewhere", "sets"]]
    _, docs, _ = _batch(lines, store_dir, capsys, monkeypatch)
    assert docs[0]["error"]["code"] == "usage" and "--output" in docs[0]["error"]["message"]
    # a global option before the command: said so, not "Unknown command"
    assert docs[1]["error"]["code"] == "usage"
    assert "global option (--store)" in docs[1]["error"]["message"]


def test_unexpected_exception_is_internal_error(
    mini: str, store_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from ragnalog.service import meta

    def boom() -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(meta, "version", boom)
    _, docs, _ = _batch([["version"], ["stats", mini]], store_dir, capsys, monkeypatch)
    assert docs[0]["error"]["code"] == "internal_error" and "boom" in docs[0]["error"]["message"]
    assert docs[1]["schema"] == "stats.v1"


def test_unreadable_input(
    store_dir: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    raw = io.TextIOWrapper(io.BytesIO(b'["version"]\n\xff\xfe\n'), encoding="utf-8")
    monkeypatch.setattr("sys.stdin", raw)
    capsys.readouterr()
    code = run(["--output", "json", "--store", str(store_dir), "batch"])
    out = capsys.readouterr().out.splitlines()
    assert code == 1
    assert json.loads(out[-1])["error"]["message"].startswith("cannot read the batch input")
