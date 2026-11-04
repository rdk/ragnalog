"""Format conformance kit: properties every registered format must have on its fixtures.

Parametrised over each format in the registry and the files in
``tests/fixtures/<format>/``; a new format gets these checks by adding fixtures.
Foreign text (``tests/fixtures/foreign/``, plus the fixtures of the other formats)
must not be claimed by a format at all.
"""

from __future__ import annotations

from itertools import islice
from pathlib import Path

import pytest

from atlas_log_analyzer.core import formats
from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.models import LogEntry
from atlas_log_analyzer.core.parser import DETECT_LINES, parse_file
from atlas_log_analyzer.core.reader import iter_raw_lines

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures"
FOREIGN = sorted((FIXTURES / "foreign").glob("*.log"))

OWN = [(name, path) for name in formats.names() for path in sorted((FIXTURES / name).glob("*.log"))]
ALIEN = [
    (name, path)
    for name in formats.names()
    for path in [
        *FOREIGN,
        *(
            p
            for other in formats.names()
            if other != name
            for p in (FIXTURES / other).glob("*.log")
        ),
    ]
]


SNIFF_GAPS = {
    "jira": (
        "JiraFormat.sniff is the share of non-empty lines that are headers, so stack traces "
        "pull it down (sample.log 0.15, edge_cases.log 0.35); detection still works because "
        "the best score > 0 wins, but a second format could out-score it"
    ),
}
"""Formats whose ``sniff`` is known to miss the 0.5 bar on their own fixtures."""


def _sniff_params() -> list[object]:
    return [
        pytest.param(
            name,
            path,
            marks=[pytest.mark.xfail(strict=True, reason=SNIFF_GAPS[name])]
            if name in SNIFF_GAPS
            else [],
        )
        for name, path in OWN
    ]


def _id(value: object) -> str:
    return value.name if isinstance(value, Path) else str(value)


def _fresh(name: str) -> LogFormat:
    """A new instance, so no state is shared with other tests (formats may cache)."""
    return type(formats.get(name))()


def _entries(name: str, path: Path) -> list[LogEntry]:
    return list(parse_file(path, _fresh(name)))


def _head(path: Path) -> list[str]:
    return [text for _, _, text in islice(iter_raw_lines(path), DETECT_LINES)]


def test_every_format_has_fixtures() -> None:
    names = {name for name, _ in OWN}
    assert names == set(formats.names())
    assert FOREIGN


@pytest.mark.parametrize(("name", "path"), OWN, ids=_id)
def test_raw_is_lossless(name: str, path: Path) -> None:
    """Joined ``raw`` texts give back the file: the reader drops ``\r`` before ``\n``
    and the final newline; nothing else is lost or reordered."""
    text = path.read_bytes().decode("utf-8").replace("\r\n", "\n")
    text = text.removesuffix("\n")
    assert "\n".join(e.raw for e in _entries(name, path)) == text


@pytest.mark.parametrize(("name", "path"), OWN, ids=_id)
def test_ids_are_unique(name: str, path: Path) -> None:
    ids = [e.id for e in _entries(name, path)]
    dupes = sorted({i for i in ids if ids.count(i) > 1})
    assert not dupes, f"{len(ids) - len(set(ids))} duplicate id(s): {dupes}"


@pytest.mark.parametrize(("name", "path"), OWN, ids=_id)
def test_source_points_at_header_line(name: str, path: Path) -> None:
    data = path.read_bytes()
    lines = data.decode("utf-8").split("\n")
    for e in _entries(name, path):
        first = e.raw.partition("\n")[0]
        assert e.source.path == str(path)
        assert lines[e.source.line - 1].removesuffix("\r") == first, e.source
        assert data[e.source.offset :].startswith(first.encode("utf-8")), e.source
        assert e.source.offset == 0 or data[e.source.offset - 1 : e.source.offset] == b"\n"


@pytest.mark.parametrize(("name", "path"), OWN, ids=_id)
def test_parsing_is_deterministic(name: str, path: Path) -> None:
    assert _entries(name, path) == _entries(name, path)


@pytest.mark.parametrize(("name", "path"), OWN, ids=_id)
def test_raw_is_header_plus_body(name: str, path: Path) -> None:
    for e in _entries(name, path):
        first, sep, _ = e.raw.partition("\n")
        if sep:
            assert e.raw == f"{first}\n{e.body}", e.source
        else:
            assert e.body == "", e.source


@pytest.mark.parametrize(("name", "path"), _sniff_params(), ids=_id)
def test_sniff_recognises_own_fixtures(name: str, path: Path) -> None:
    assert _fresh(name).sniff(_head(path)) >= 0.5


@pytest.mark.parametrize(("name", "path"), ALIEN, ids=_id)
def test_sniff_rejects_foreign_text(name: str, path: Path) -> None:
    assert _fresh(name).sniff(_head(path)) == 0
