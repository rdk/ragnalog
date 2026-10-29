"""``atlas-log report`` run in-process against the module's tmp store."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from atlas_log_analyzer.cli.app import run

from .conftest import report_data


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str

    def json(self) -> Any:
        return json.loads(self.stdout)


@pytest.fixture
def report(capsys: pytest.CaptureFixture[str], store_dir: Path) -> Any:
    def invoke(*args: object, mode: str = "json") -> Result:
        capsys.readouterr()
        code = run(["--output", mode, "--store", str(store_dir), "report", *map(str, args)])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture(autouse=True)
def no_browser(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    opened: list[str] = []
    monkeypatch.setattr("webbrowser.open", lambda url, *a, **k: opened.append(url) or True)
    return opened


def test_writes_file_and_reports_bytes(report: Any, tmp_path: Path) -> None:
    out = tmp_path / "sample.html"
    res = report("sample", "--out", out)
    assert res.code == 0, res.stdout + res.stderr
    doc = res.json()
    assert doc["schema"] == "report-file.v1"
    assert set(doc) == {"schema", "path", "bytes", "groups", "entries", "compare"}
    assert Path(doc["path"]) == out.resolve()
    assert doc["bytes"] == out.stat().st_size
    assert doc["compare"] is False
    data = report_data(out.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert doc["groups"] == len(data["groups"])
    assert doc["entries"] == len(data["entries"])


def test_default_out_is_name_in_cwd(
    report: Any, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    res = report("sample")
    assert res.code == 0, res.stdout
    assert (tmp_path / "sample.html").is_file()
    assert Path(res.json()["path"]) == (tmp_path / "sample.html").resolve()


def test_options(report: Any, tmp_path: Path) -> None:
    out = tmp_path / "small.html"
    res = report("sample", "--out", out, "--max-groups", "2", "--examples", "1", "--max-raw", "50",
                 "--min-level", "warn")  # fmt: skip
    assert res.code == 0, res.stdout
    data = report_data(out.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert len(data["groups"]) == 2 == res.json()["groups"]
    assert all(g["level"] >= 40 for g in data["groups"])
    assert all(len(e["raw"]) <= 50 for e in data["entries"].values())
    assert data["options"]["min_level"] == "WARN"


def test_compare(report: Any, tmp_path: Path) -> None:
    out = tmp_path / "cmp.html"
    res = report("sample", "--compare", "edge", "--out", out)
    assert res.code == 0, res.stdout
    assert res.json()["compare"] is True
    data = report_data(out.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    assert data["compare"]["other"]["name"] == "edge"


def test_incomparable_sets_exit_1(report: Any, tmp_path: Path) -> None:
    res = report("sample", "--compare", "edge-f2", "--out", tmp_path / "x.html")
    assert res.code == 1
    assert res.json()["error"]["code"] == "invalid_value"
    assert "not comparable" in res.json()["error"]["message"]
    assert not (tmp_path / "x.html").exists()


def test_errors(report: Any, tmp_path: Path) -> None:
    assert report("nope", "--out", tmp_path / "a.html").code == 3
    assert report("sample", "--min-level", "LOUD", "--out", tmp_path / "b.html").code == 1
    assert report("sample", "--examples", "-1", "--out", tmp_path / "c.html").code == 1
    missing = report("sample", "--out", tmp_path / "no" / "such" / "dir.html")
    assert missing.code == 2
    assert missing.json()["error"]["code"] == "file_not_found"


def test_table_mode_prints_one_line(report: Any, tmp_path: Path) -> None:
    res = report("sample", "--out", tmp_path / "t.html", mode="table")
    assert res.code == 0
    lines = res.stdout.strip().splitlines()
    assert len(lines) == 1 and lines[0].startswith("wrote ")


def test_open_uses_webbrowser(report: Any, tmp_path: Path, no_browser: list[str]) -> None:
    out = tmp_path / "o.html"
    assert report("sample", "--out", out).code == 0
    assert no_browser == []
    assert report("sample", "--out", out, "--open").code == 0
    assert no_browser == [out.resolve().as_uri()]


def test_help(capsys: pytest.CaptureFixture[str]) -> None:
    capsys.readouterr()
    assert run(["report", "--help"]) == 0
    text = capsys.readouterr().out
    assert "Usage: atlas-log report" in text
    for needle in ("Exit codes:", "Output", '"schema"', "--compare", "--max-raw", "--open"):
        assert needle in text
    capsys.readouterr()
    run(["--help"])
    assert " report " in capsys.readouterr().out
