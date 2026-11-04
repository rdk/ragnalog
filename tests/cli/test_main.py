"""The console-script entry point: closed pipes and unexpected errors."""

from __future__ import annotations

import errno
import io
import os
import sys
from pathlib import Path

import pytest

from atlas_log_analyzer.cli import app as cli_app


class _ClosedPipe(io.TextIOBase):
    """A stdout whose reader has gone: every flush fails as on Windows (EINVAL)."""

    def __init__(self, fd: int) -> None:
        self._fd = fd
        self._broken = True

    def write(self, text: str) -> int:
        return len(text)

    def flush(self) -> None:
        if self._broken:
            raise OSError(errno.EINVAL, "Invalid argument")

    def close(self) -> None:
        self._broken = False  # let the finaliser's flush pass
        super().close()

    def fileno(self) -> int:
        return self._fd


@pytest.fixture
def spare_fd(tmp_path: Path):
    fd = os.open(tmp_path / "stdout.txt", os.O_WRONLY | os.O_CREAT)
    yield fd
    os.close(fd)


def _fail_with(exc: BaseException):
    def run(argv: object = None) -> int:
        raise exc

    return run


def test_windows_closed_pipe_exits_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], spare_fd: int
) -> None:
    monkeypatch.setattr(sys, "stdout", _ClosedPipe(spare_fd))
    monkeypatch.setattr(cli_app, "run", _fail_with(OSError(errno.EINVAL, "Invalid argument")))
    with pytest.raises(SystemExit) as stop:
        cli_app.main()
    assert stop.value.code == 1
    assert capsys.readouterr().err == ""


def test_broken_pipe_exits_quietly(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], spare_fd: int
) -> None:
    monkeypatch.setattr(sys, "stdout", _ClosedPipe(spare_fd))
    monkeypatch.setattr(cli_app, "run", _fail_with(BrokenPipeError(errno.EPIPE, "Broken pipe")))
    with pytest.raises(SystemExit) as stop:
        cli_app.main()
    assert stop.value.code == 1
    assert capsys.readouterr().err == ""


def test_other_einval_is_an_internal_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """EINVAL while stdout still works (e.g. an invalid file name) is reported."""
    monkeypatch.delenv("ATLAS_LOG_DEBUG", raising=False)
    monkeypatch.setattr(sys, "argv", ["atlas-log", "--output", "json", "sets"])
    monkeypatch.setattr(cli_app, "run", _fail_with(OSError(errno.EINVAL, "bad name")))
    with pytest.raises(SystemExit) as stop:
        cli_app.main()
    assert stop.value.code != 0
    assert '"internal_error"' in capsys.readouterr().out


def test_stdout_closed_classification(monkeypatch: pytest.MonkeyPatch, spare_fd: int) -> None:
    assert cli_app._stdout_closed(BrokenPipeError())
    assert not cli_app._stdout_closed(ValueError("x"))
    assert not cli_app._stdout_closed(OSError(errno.ENOENT, "missing"))
    assert not cli_app._stdout_closed(OSError(errno.EINVAL, "stdout is fine"))
    monkeypatch.setattr(sys, "stdout", _ClosedPipe(spare_fd))
    assert cli_app._stdout_closed(OSError(errno.EINVAL, "Invalid argument"))
