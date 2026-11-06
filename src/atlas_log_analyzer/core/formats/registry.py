"""Format registry: built-in formats, entry-point discovery and sniffing."""

from __future__ import annotations

import threading
import warnings
from collections.abc import Sequence

from atlas_log_analyzer.core.formats.base import LogFormat

__all__ = ["ENTRY_POINT_GROUP", "UnknownFormatError", "detect", "get", "names", "register"]

ENTRY_POINT_GROUP = "atlas_log_analyzer.formats"

_formats: dict[str, LogFormat] = {}
_loaded = False
_load_lock = threading.Lock()


class UnknownFormatError(LookupError):
    """Raised by :func:`get` for an unknown format name."""


def _builtin_formats() -> list[LogFormat]:
    from atlas_log_analyzer.core.formats.jira import JiraFormat

    return [JiraFormat()]


def _load() -> None:
    """Load built-ins and entry points once; failing entry points are skipped with a warning."""
    if _loaded:
        return
    with _load_lock:  # concurrent first lookups (web app threads) must not see it half-filled
        if not _loaded:
            _load_formats()


def _load_formats() -> None:
    global _loaded
    import importlib.metadata  # ~70 ms; only commands that look up a format pay it

    found: dict[str, LogFormat] = {fmt.name: fmt for fmt in _builtin_formats()}
    try:
        entry_points = list(importlib.metadata.entry_points(group=ENTRY_POINT_GROUP))
    except Exception as exc:  # broken metadata must not break the tool
        warnings.warn(f"cannot list format entry points: {exc}", RuntimeWarning, stacklevel=3)
        entry_points = []
    for ep in entry_points:
        if ep.name in found:
            continue  # built-ins win; avoids re-instantiating the bundled formats
        try:
            obj = ep.load()
            fmt = obj() if isinstance(obj, type) else obj
            if not isinstance(fmt, LogFormat):
                raise TypeError(f"{obj!r} does not implement LogFormat")
        except Exception as exc:
            warnings.warn(
                f"cannot load log format {ep.name!r} ({ep.value}): {exc}",
                RuntimeWarning,
                stacklevel=3,
            )
            continue
        found[fmt.name] = fmt
    # in-process registrations made before loading take precedence
    found.update(_formats)
    _formats.clear()
    _formats.update(found)
    _loaded = True


def register(fmt: LogFormat) -> None:
    """Register a format instance under ``fmt.name`` (replaces an existing one)."""
    _load()
    _formats[fmt.name] = fmt


def get(name: str) -> LogFormat:
    """Return the format registered under ``name``; raise :class:`UnknownFormatError` otherwise."""
    _load()
    try:
        return _formats[name]
    except KeyError:
        known = ", ".join(sorted(_formats)) or "none"
        raise UnknownFormatError(f"unknown log format {name!r} (known: {known})") from None


def names() -> list[str]:
    """Sorted names of all known formats (built-in + entry points + registered)."""
    _load()
    return sorted(_formats)


def detect(lines: Sequence[str]) -> LogFormat | None:
    """Pick the format with the highest ``sniff`` confidence (> 0) for the given first lines."""
    _load()
    best: LogFormat | None = None
    best_score = 0.0
    for name in sorted(_formats):
        fmt = _formats[name]
        try:
            score = fmt.sniff(lines)
        except Exception:
            continue
        if score > best_score:
            best, best_score = fmt, score
    return best
