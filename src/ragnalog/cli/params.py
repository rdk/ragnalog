"""CLI side of the request models: option defaults and CLI-only resolution rules.

The models themselves are :mod:`ragnalog.service.params`, shared with the
web API; here are the defaults of the flattened options, the CLI's ``--max-raw``
default (which depends on the output mode) and the docstring column markers.
"""

from __future__ import annotations

import textwrap
from collections.abc import Sequence
from typing import Any

from ragnalog.cli import context
from ragnalog.service.params import (
    DEFAULT_MAX_RAW,
    EntryQuery,
    GroupQuery,
    IngestTarget,
    Page,
    TextCap,
    parse_level,
)

__all__ = ["ALL_ENTRIES", "ALL_GROUPS", "ANY_TARGET", "cap", "fill_doc", "level", "limit"]

ALL_GROUPS = GroupQuery()
"""No group filters (default of the flattened ``groups`` options)."""
ALL_ENTRIES = EntryQuery()
"""No entry filters (default of the flattened ``entries`` options)."""
ANY_TARGET = IngestTarget()
"""No node, cluster or capture given, ``--split auto`` (default of the ``ingest`` options)."""


def cap(value: int | None, *, text: bool) -> TextCap:
    """Resolve ``--max-raw``: explicit value (0 = no limit), else 4000 for json/jsonl.

    ``text`` is true for the plain-text modes (``--raw`` and table), which default
    to no limit.
    """
    default = None if text or context.out().mode == "table" else DEFAULT_MAX_RAW
    return TextCap.parse(value, default=default, front="cli")


def level(value: str | None, name: str = "min_level") -> str | None:
    """Parse a level option (``store.levels``: names, ``WARNING``, ``UNKNOWN``; blank = unset)."""
    return parse_level(value, name, front="cli")


def limit(value: int) -> int | None:
    """``--limit`` (0 = no limit)."""
    return Page.parse(value, front="cli").limit


def fill_doc(func: Any, **columns: Sequence[str]) -> None:
    """Replace ``<NAME>`` marker lines in ``func``'s docstring (= its ``--help``).

    Each marker becomes the comma-separated ``columns[NAME]``, wrapped and indented
    like the marker line.
    """
    lines = str(func.__doc__ or "").splitlines()
    for i, line in enumerate(lines):
        key = line.strip()[1:-1]
        if line.strip().startswith("<") and key in columns:
            indent = line[: len(line) - len(line.lstrip())]
            lines[i] = textwrap.fill(
                ", ".join(columns[key]), width=78, initial_indent=indent, subsequent_indent=indent
            )
    func.__doc__ = "\n".join(lines)
