"""Fixtures for CLI tests: in-process invocation with captured stdout/stderr."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest

from ragnalog.cli.app import run

DATA = Path(__file__).parent / "data"
MINI_LOG = DATA / "mini.log"
SAMPLE_LOG = Path(__file__).parents[1] / "fixtures" / "jira" / "sample.log"


@dataclass
class Result:
    code: int
    stdout: str
    stderr: str

    def json(self) -> Any:
        return json.loads(self.stdout)

    def jsonl(self) -> list[Any]:
        return [json.loads(line) for line in self.stdout.splitlines() if line.strip()]


type Invoke = Callable[..., Result]


@pytest.fixture
def cli(capsys: pytest.CaptureFixture[str]) -> Invoke:
    """Run ``ragnalog`` in-process with the given arguments."""

    def invoke(*args: object) -> Result:
        capsys.readouterr()
        code = run([str(a) for a in args])
        captured = capsys.readouterr()
        return Result(code, captured.out, captured.err)

    return invoke


@pytest.fixture
def store_dir(tmp_path: Path) -> Path:
    return tmp_path / "store"


@pytest.fixture
def jcli(cli: Invoke, store_dir: Path) -> Invoke:
    """Like ``cli`` but in json mode against a temporary store."""

    def invoke(*args: object) -> Result:
        return cli("--output", "json", "--store", store_dir, *args)

    return invoke
