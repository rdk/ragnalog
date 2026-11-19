"""Guard against real data in the committed log fixtures (see AGENTS.md, "Data hygiene")."""

from __future__ import annotations

import os
import re
from pathlib import Path

import pytest

TESTS = Path(__file__).parent
FIXTURES = sorted([*(TESTS / "fixtures").rglob("*.log"), *(TESTS / "cli" / "data").rglob("*.log")])

ALLOWED_PROJECTS = frozenset({"ABC", "XYZ", "JRASERVER"})
NOT_ISSUE_KEYS = frozenset({"UTF", "ISO", "SHA", "HTTP", "TLS", "SSL"})
ISSUE_RE = re.compile(r"\b([A-Z][A-Z0-9]{1,9})-\d+\b")
IPV4_RE = re.compile(r"(?<![\w.])(\d{1,3})(?:\.\d{1,3}){3}(?![\w.])")
URL_HOST_RE = re.compile(r"\b[a-z][a-z0-9+.-]*://(?:[^@/\s]+@)?([^/:\s)'\"]+)", re.IGNORECASE)
ALLOWED_HOSTS = frozenset({"localhost", "go"})
"""``go`` appears in a Jira product message (``http://go/j/JRASERVER-...``)."""


def _forbidden() -> re.Pattern[str] | None:
    """Names of the real environment that must not reach a fixture, one regex per line.

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


def test_fixtures_found() -> None:
    assert FIXTURES


@pytest.mark.parametrize("path", FIXTURES, ids=lambda p: p.name)
def test_fixture_is_masked(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    keys = {m.group() for m in ISSUE_RE.finditer(text)}
    bad_keys = {k for k in keys if k.split("-")[0] not in ALLOWED_PROJECTS | NOT_ISSUE_KEYS}
    assert not bad_keys, f"unmasked issue keys: {sorted(bad_keys)}"
    bad_ips = {m.group() for m in IPV4_RE.finditer(text) if m.group(1) != "10"}
    assert not bad_ips, f"IPv4 addresses outside 10.0.0.0/8: {sorted(bad_ips)}"
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
