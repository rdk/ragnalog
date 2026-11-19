"""Level filter names: one parser for the CLI, the web API and :class:`~.logset.LogSet`.

A filter is one name or comma-separated names, case-insensitive:
``TRACE DEBUG INFO WARN ERROR FATAL UNKNOWN``, with ``WARNING`` accepted for
``WARN``. ``UNKNOWN`` selects entries whose header could not be parsed
(``parse_error`` rows, level 0). A blank filter (``""``, ``" , "``) means no
filter. Input-format aliases such as ``SEVERE`` or ``FINE`` are not filter
names: stored rows only carry the names above (``level_name``).
"""

from __future__ import annotations

from ragnalog.core.models import LogLevel

__all__ = ["LEVEL_ALIASES", "LEVEL_NAMES", "InvalidLevel", "level_values", "parse_levels"]

LEVEL_NAMES: tuple[str, ...] = ("TRACE", "DEBUG", "INFO", "WARN", "ERROR", "FATAL", "UNKNOWN")
"""Accepted level names, least severe first; ``UNKNOWN`` = entries without a parsed level."""
LEVEL_ALIASES: dict[str, str] = {"WARNING": "WARN"}
"""Other accepted spellings and the name they stand for."""


class InvalidLevel(ValueError):
    """A level filter names a level that does not exist."""


def _expected() -> str:
    aliases = ", ".join(f"{alias} = {name}" for alias, name in LEVEL_ALIASES.items())
    return f"expected one of {', '.join(LEVEL_NAMES)} ({aliases})"


def parse_levels(value: str | None) -> str | None:
    """Normalise a level filter: ``"warn, Error"`` → ``"WARN,ERROR"``; blank → ``None``.

    Empty items are skipped and duplicates dropped (order kept).

    Raises:
        InvalidLevel: An item is not a level name or alias.

    """
    if value is None:
        return None
    names: list[str] = []
    for part in value.split(","):
        token = part.strip().upper()
        if not token:
            continue
        name = LEVEL_ALIASES.get(token, token)
        if name not in LEVEL_NAMES:
            raise InvalidLevel(f"unknown level {part.strip()!r}; {_expected()}")
        if name not in names:
            names.append(name)
    return ",".join(names) or None


def level_values(value: str | None) -> list[int]:
    """Numeric levels of a filter (``"WARN,ERROR"`` → ``[40, 50]``); ``[]`` when blank.

    Raises:
        InvalidLevel: An item is not a level name or alias.

    """
    names = parse_levels(value)
    return [int(LogLevel[name]) for name in names.split(",")] if names else []
