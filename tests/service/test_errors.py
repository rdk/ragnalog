"""One error model (``service/errors.py``): codes, reasons, mapping, and the docs' tables."""

from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

import pytest

from ragnalog.core.formats.registry import UnknownFormatError
from ragnalog.service.errors import (
    REASONS,
    SPEC,
    ExitCode,
    OpError,
    flag,
    from_exception,
    hint,
)
from ragnalog.service.views import AmbiguousSignature
from ragnalog.store.levels import InvalidLevel
from ragnalog.store.pipeline import FormatNotDetected, NoSourcesFound, NotInFormat
from ragnalog.store.store import AmbiguousLogSet, LogSetNotFound, StoreError

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src" / "ragnalog"
DOCS = [ROOT / "docs" / "cli.md", ROOT / "docs" / "webapp.md"]


def code_table() -> str:
    """The error-code table both docs carry, rendered from SPEC."""
    rows = [f"| `{c}` | {int(s.exit)} | {s.http} | {s.meaning} |" for c, s in SPEC.items()]
    return "\n".join(["| Code | Exit | HTTP | Meaning |", "|---|---|---|---|", *rows])


def reason_table() -> str:
    """The reason table both docs carry, rendered from REASONS."""
    rows = [f"| `{r}` | `{x.cli}` | `{x.api}` | {x.meaning} |" for r, x in REASONS.items()]
    return "\n".join(["| Reason | CLI code | API code | Meaning |", "|---|---|---|---|", *rows])


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
@pytest.mark.parametrize("table", [code_table, reason_table], ids=["codes", "reasons"])
def test_docs_tables_match_spec(doc: Path, table: object) -> None:
    expected = table()  # type: ignore[operator]
    assert expected in doc.read_text(encoding="utf-8"), f"paste into {doc.name}:\n{expected}"


def test_reasons_use_known_codes() -> None:
    for reason in REASONS.values():
        assert reason.cli in SPEC and reason.api in SPEC


def test_every_code_raised_in_the_package_is_in_spec() -> None:
    codes = re.compile(r"""OpError\(\s*["']([a-z_]+)["']""")
    reasons = re.compile(r"""(?:because|hint)\(\s*["']([a-z_]+)["']""")
    for path in SRC.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        for code in codes.findall(text):
            assert code in SPEC, f"{path.name}: error code {code!r} is not in SPEC"
        for reason in reasons.findall(text):
            assert reason in REASONS or reason in SPEC, f"{path.name}: unknown reason {reason!r}"


CASES: list[tuple[Exception, str, int, str, int, str | None]] = [
    # exception, CLI code and exit, API code and status, reason
    (LogSetNotFound("no log set 'x'"), "store_error", 3, "not_found", 404, "set_not_found"),
    (AmbiguousLogSet("name 'x'"), "store_error", 3, "ambiguous", 409, "ambiguous_ref"),
    (
        AmbiguousSignature("abcd", "s", ["abcd1", "abcd2"], 2),
        *("ambiguous", 1, "ambiguous", 409, "ambiguous_signature"),
    ),
    (InvalidLevel("unknown level 'x'"), "invalid_level", 1, "invalid_level", 400, None),
    (NoSourcesFound("none"), "no_sources", 2, "no_sources", 422, None),
    (FormatNotDetected("?"), "format_not_detected", 2, "format_not_detected", 422, None),
    (NotInFormat("big"), "not_in_format", 2, "not_in_format", 422, None),
    (StoreError("broken"), "store_error", 3, "store_error", 500, None),
    (UnknownFormatError("unknown format 'x'"), "unknown_format", 1, "unknown_format", 400, None),
    (FileNotFoundError(2, "No such file", "x"), "file_not_found", 2, "file_not_found", 404, None),
    (PermissionError(13, "Permission denied", "d"), "store_error", 3, "store_error", 500, None),
    (IsADirectoryError(21, "Is a directory", "d"), "store_error", 3, "store_error", 500, None),
    (FileExistsError(17, "File exists", "f"), "store_error", 3, "store_error", 500, None),
    (ValueError("bad"), "invalid_value", 1, "invalid_value", 400, None),
]


@pytest.mark.parametrize(("exc", "cli", "exit", "api", "status", "reason"), CASES)
def test_from_exception_per_front_end(
    exc: Exception, cli: str, exit: int, api: str, status: int, reason: str | None
) -> None:
    c, a = from_exception(exc, "cli"), from_exception(exc, "api")
    assert c is not None and a is not None
    assert (c.code, int(c.exit_code), a.code, a.status) == (cli, exit, api, status)
    assert c.reason == a.reason == reason


def test_file_not_found_messages_say_it_once() -> None:
    plain = from_exception(FileNotFoundError("no such file or directory: x.log"), "cli")
    assert plain is not None and plain.message == "no such file or directory: x.log"
    named = from_exception(FileNotFoundError(2, "No such file", "x.log"), "cli")
    assert named is not None and named.message == "no such file or directory: x.log"
    other = from_exception(FileNotFoundError("Failed to open x.parquet"), "cli")
    assert (
        other is not None and other.message == "no such file or directory: Failed to open x.parquet"
    )
    refused = from_exception(PermissionError(13, "Permission denied", "d"), "cli")
    assert refused is not None and refused.message == "Permission denied: d"


def test_from_exception_unknown_and_passthrough() -> None:
    assert from_exception(RuntimeError("boom"), "cli") is None
    # a closed stdout must reach the CLI's quiet exit, not become an error document
    assert from_exception(BrokenPipeError(32, "Broken pipe"), "cli") is None
    assert from_exception(OSError(22, "Invalid argument"), "cli") is None
    err = OpError("usage", "x")
    assert from_exception(err, "api") is err


def test_envelope_keys() -> None:
    err = OpError.because(
        "ambiguous_signature", "api", "prefix 'ab12'", hint="more", extra={"candidates": ["a"]}
    )
    assert err.envelope() == {
        "schema": "error.v1",
        "error": {
            "code": "ambiguous",
            "message": "prefix 'ab12'",
            "hint": "more",
            "reason": "ambiguous_signature",
            "candidates": ["a"],
        },
    }
    assert OpError("usage", "x").envelope()["error"] == {"code": "usage", "message": "x"}


def test_hints_and_flags_per_front_end() -> None:
    cli, api = hint("group_not_found", "cli", set="prod"), hint("group_not_found", "api", id="a")
    assert cli == "list signatures with `ragnalog groups prod`"
    assert api == "list signatures with GET /api/sets/a/groups"
    assert hint("set_not_found", "cli") is None  # the CLI lists the store's sets instead
    assert hint("no_such_thing", "api") is None
    assert (flag("max_raw", "cli"), flag("max_raw", "api")) == ("--max-raw", "max_raw")


def test_unknown_code_falls_back() -> None:
    err = OpError("made_up", "x")
    assert (err.exit_code, err.status) == (ExitCode.USAGE, 500)


def test_mapping_loads_no_data_libraries() -> None:
    script = (
        "import json, sys\n"
        "from ragnalog.service.errors import from_exception\n"
        "from_exception(ValueError('x'), 'cli'); from_exception(KeyError('k'), 'api')\n"
        "print(json.dumps([m for m in ('polars', 'ragnalog.store.pipeline')"
        " if m in sys.modules]))\n"
    )
    out = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, check=True)
    assert json.loads(out.stdout) == []
