"""Guard against real data in the repository (see AGENTS.md, "Data hygiene").

Log fixtures get the strictest checks; test code, docs and sources are checked for private
addresses and, when the data folder is present, for the real environment's names.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
ROOT = TESTS.parent
FIXTURES = sorted([*(TESTS / "fixtures").rglob("*.log"), *(TESTS / "cli" / "data").rglob("*.log")])
TEXT_SUFFIXES = frozenset(
    {".py", ".md", ".log", ".json", ".yml", ".yaml", ".toml", ".txt", ".html", ".js", ".mjs"}
    | {".css", ".svg", ".cfg", ".ini"}
)
TEXT_FILES = sorted(
    p
    for d in ("src", "tests", "docs", "scripts", ".github")
    for p in (ROOT / d).rglob("*")
    if p.is_file() and p.suffix in TEXT_SUFFIXES and "__pycache__" not in p.parts
) + sorted(
    p
    for p in ROOT.iterdir()
    if p.is_file() and (p.suffix in TEXT_SUFFIXES or p.name == "Dockerfile")
)
"""Every tracked kind of text file: sources, tests, docs, scripts, workflows, top-level files."""

ALLOWED_PROJECTS = frozenset({"ABC", "XYZ", "JRASERVER"})
NOT_ISSUE_KEYS = frozenset({"UTF", "ISO", "SHA", "HTTP", "TLS", "SSL"})
ISSUE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")
JQL_PROJECT_RE = re.compile(r"\bproject\s*(?:=|in)\s*\(?\s*\"?([A-Z][A-Z0-9_]{1,9})\b")
IPV4_RE = re.compile(r"(?<![\w.])(\d{1,3})(?:\.\d{1,3}){3}(?![\w.])")
PRIVATE_IP_RE = re.compile(
    r"(?<![\w.])(?:10(?:\.\d{1,3}){3}|172\.(?:1[6-9]|2\d|3[01])(?:\.\d{1,3}){2}"
    r"|192\.168(?:\.\d{1,3}){2})(?![\w.])"
)
SYNTHETIC_IPS = frozenset({"10.1.2.3", "10.20.30.40", "10.20.30.41"})
"""Invented private addresses in use besides 10.0.0.0/16, the range for masked or made-up ones."""
URL_HOST_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^@/\s]+@)?([^/:\s)'\"]+)", re.IGNORECASE)
ALLOWED_HOSTS = frozenset({"localhost", "go"})
"""``go`` appears in a Jira product message (``http://go/j/JRASERVER-...``)."""


def _forbidden() -> re.Pattern[str] | None:
    """Names of the real environment that must not reach the repository, one regex per line.

    They are kept OUT of the repository, in ``forbidden.txt`` of the data folder
    (``RAGNALOG_DATA``, else ``../ragnalog-data`` beside the repository), so
    this guard never writes them down itself; without that file (CI) it checks nothing.
    """
    root = Path(os.environ.get("RAGNALOG_DATA") or TESTS.parents[1] / "ragnalog-data")
    try:
        lines = (root / "forbidden.txt").read_text(encoding="utf-8").splitlines()
    except OSError:
        return None
    pats = [ln.strip() for ln in lines if ln.strip() and not ln.lstrip().startswith("#")]
    return re.compile("|".join(f"(?:{p})" for p in pats), re.IGNORECASE) if pats else None


FORBIDDEN_RE = _forbidden()


def _real_private_ips(text: str) -> set[str]:
    """Private addresses outside 10.0.0.0/16 and the known invented ones: likely real."""
    return {
        m.group()
        for m in PRIVATE_IP_RE.finditer(text)
        if not m.group().startswith("10.0.") and m.group() not in SYNTHETIC_IPS
    }


def test_fixtures_found() -> None:
    assert FIXTURES
    assert len(TEXT_FILES) > 100


@pytest.mark.parametrize("path", TEXT_FILES, ids=lambda p: p.relative_to(ROOT).as_posix())
def test_text_file_is_clean(path: Path) -> None:
    """No real private address anywhere, and no name from the data folder's forbidden.txt."""
    text = path.read_text(encoding="utf-8", errors="replace")
    real_ips = _real_private_ips(text)
    assert not real_ips, f"private addresses outside 10.0.0.0/16: {sorted(real_ips)}"
    found = FORBIDDEN_RE.search(text) if FORBIDDEN_RE else None
    assert found is None, (
        f"a name from the data folder's forbidden.txt at offset {found and found.start()}"
    )


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_is_masked(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    keys = {m.group() for m in ISSUE_RE.finditer(text)}
    bad_keys = {k for k in keys if k.split("-")[0] not in ALLOWED_PROJECTS | NOT_ISSUE_KEYS}
    assert not bad_keys, f"unmasked issue keys: {sorted(bad_keys)}"
    bad_projects = {m.group(1) for m in JQL_PROJECT_RE.finditer(text)} - ALLOWED_PROJECTS
    assert not bad_projects, f"unmasked JQL project keys: {sorted(bad_projects)}"
    bad_ips = {m.group() for m in IPV4_RE.finditer(text) if m.group(1) != "10"}
    bad_ips |= _real_private_ips(text)
    assert not bad_ips, f"IPv4 addresses outside 10.0.0.0/16: {sorted(bad_ips)}"
    bad_hosts = {
        h
        for h in (m.group(1).lower() for m in URL_HOST_RE.finditer(text))
        if h not in ALLOWED_HOSTS and h != "example.com" and not h.endswith(".example.com")
    }
    assert not bad_hosts, f"hosts outside example.com: {sorted(bad_hosts)}"
    found = FORBIDDEN_RE.search(text) if FORBIDDEN_RE else None
    assert found is None, (
        f"a name from the data folder's forbidden.txt at offset {found and found.start()}"
    )


def test_guard_catches_examples() -> None:
    """The checks flag invented stand-ins for the kinds of values that once slipped through.

    The addresses are built from parts so that this file passes its own check.
    """
    a, b = ".".join(["10", "99", "1", "1"]), ".".join(["192", "168", "5", "5"])
    assert _real_private_ips(f"from {a} and {b}, not 10.0.0.24") == {a, b}
    assert [m.group(1) for m in JQL_PROJECT_RE.finditer("jql='project=QQQ AND x'")] == ["QQQ"]
