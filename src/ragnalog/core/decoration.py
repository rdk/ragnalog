"""Decoration lines: rows of asterisks, dashes, equals signs or underscores that carry no text.

Some loggers frame what they say with such lines. ScriptRunner's ``GroovyCustomField``
logs a line of asterisks as an entry of its own right before each "Script field
failed" entry; Jira's startup logger leaves the header message empty and writes its
text between ``****`` lines on the following lines. Two rules of entry assembly use
these helpers; a format opts in to each (see
:func:`~ragnalog.core.formats.base.banner_window_ms` and
:func:`~ragnalog.core.formats.base.titles_from_body`):

* a *banner* (:func:`is_banner_text` header message, empty body) is attached to the
  next entry of its file (:func:`~ragnalog.core.parser.attach_banners`);
* an entry whose header message is blank is signed by its body's :func:`lead_line`.
"""

from __future__ import annotations

import re

__all__ = ["BANNER_MIN_CHARS", "DECORATION_RE", "is_banner_text", "is_decoration", "lead_line"]

DECORATION_RE = re.compile(r"[\W_]+")
"""A decoration-only line once stripped of white space: no letter and no digit."""
BANNER_MIN_CHARS = 10
"""Characters (white space stripped) a header message needs to count as a banner."""


def is_decoration(text: str) -> bool:
    """Whether ``text`` is a non-blank line without any letter or digit (``*****``, ``----``)."""
    stripped = text.strip()
    return bool(stripped) and DECORATION_RE.fullmatch(stripped) is not None


def is_banner_text(text: str) -> bool:
    """Whether ``text`` is decoration of at least :data:`BANNER_MIN_CHARS` characters."""
    stripped = text.strip()
    return len(stripped) >= BANNER_MIN_CHARS and DECORATION_RE.fullmatch(stripped) is not None


def lead_line(body: str) -> str:
    r"""Return the first line of ``body`` that is neither blank nor decoration, stripped.

    ``""`` when there is none. Lines are split at ``\n`` only, as entries join their
    continuation lines; the scan stops at the first such line, so a long body costs
    no more than the lines before it.
    """
    start = 0
    size = len(body)
    while start <= size:
        end = body.find("\n", start)
        if end < 0:
            end = size
        line = body[start:end].strip()
        if line and DECORATION_RE.fullmatch(line) is None:
            return line
        start = end + 1
    return ""
