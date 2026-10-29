"""Self-contained HTML report of a log set (``atlas-log report``).

Public API::

    from atlas_log_analyzer.report import build_report, write_report
    html = build_report(logset, compare=baseline)
    info = write_report(logset, Path("prod.html"))   # ReportInfo(path, bytes, ...)

The page is driven by the ``report.v1`` payload from :func:`build_payload`
(``docs/formats/report.v1.md``); the assets live in ``report/assets``.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from atlas_log_analyzer.report.payload import REPORT_SCHEMA, build_payload, example_entries
from atlas_log_analyzer.report.render import asset_text, render_html
from atlas_log_analyzer.store.logset import LogSet

__all__ = [
    "REPORT_SCHEMA",
    "ReportInfo",
    "asset_text",
    "build_payload",
    "build_report",
    "example_entries",
    "render_html",
    "write_report",
]


@dataclass(frozen=True, slots=True)
class ReportInfo:
    """What :func:`write_report` wrote."""

    path: Path
    bytes: int
    groups: int
    """Groups in the payload."""
    entries: int
    """Example entries in the payload."""
    compare: bool
    """Whether the report contains a comparison."""


def build_report(
    logset: LogSet,
    *,
    compare: LogSet | None = None,
    min_level: str | None = None,
    examples: int = 3,
    max_raw: int = 4000,
    max_groups: int | None = None,
) -> str:
    """Return the HTML report page for ``logset`` (options as for :func:`build_payload`)."""
    payload = build_payload(
        logset,
        compare=compare,
        min_level=min_level,
        examples=examples,
        max_raw=max_raw,
        max_groups=max_groups,
    )
    return render_html(payload)


def write_report(
    logset: LogSet,
    path: Path,
    *,
    compare: LogSet | None = None,
    min_level: str | None = None,
    examples: int = 3,
    max_raw: int = 4000,
    max_groups: int | None = None,
) -> ReportInfo:
    """Write the HTML report to ``path`` (UTF-8) and describe the result.

    Raises:
        ValueError: Invalid option or the sets are not comparable (see :func:`build_payload`).
        OSError: ``path`` cannot be written.

    """
    payload: dict[str, Any] = build_payload(
        logset,
        compare=compare,
        min_level=min_level,
        examples=examples,
        max_raw=max_raw,
        max_groups=max_groups,
    )
    data = render_html(payload).encode("utf-8")
    path.write_bytes(data)
    return ReportInfo(
        path=path,
        bytes=len(data),
        groups=len(payload["groups"]),
        entries=len(payload["entries"]),
        compare=payload["compare"] is not None,
    )
