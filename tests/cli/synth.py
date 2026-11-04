"""Synthetic Jira log entries for CLI tests.

Everything here is invented: users, request ids, issue keys (``ABC-``), IPs
(``10.0.0.0/8``) and URLs. Tests write the text to ``tmp_path`` and ingest it.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class E:
    """One synthetic entry; ``ts`` is the time of day on 2026-10-29 (+01:00)."""

    ts: str
    thread: str
    level: str
    logger: str
    message: str
    user: str | None = None
    request_id: str | None = None
    body: str = ""
    day: str = "2026-10-29"


def render(e: E) -> str:
    """The entry in the Jira ``atlassian-jira.log`` layout."""
    head = f"{e.day} {e.ts}+0100 {e.thread}"
    if e.user is not None and e.request_id is not None:
        url = "/rest/acme-reports/1.0/report"
        head += (
            f" url: {url}; user: {e.user} {e.level} {e.user} {e.request_id} 1abcd2e 10.0.0.5 {url}"
        )
    else:
        head += f" {e.level}     "
    head += f" [{e.logger}] {e.message}"
    return head + ("\n" + e.body if e.body else "")


def write_log(path: Path, entries: Iterable[E]) -> Path:
    """Write ``entries`` to ``path`` (parent created) and return it."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(("\n".join(render(e) for e in entries) + "\n").encode())
    return path


def long_body(lines: int = 200) -> str:
    """A long continuation text (about 80 characters per line)."""
    return "\n".join(f"    detail {i:05d} " + "y" * 64 for i in range(lines))
