"""File names that are safe on every platform the tool runs on."""

from __future__ import annotations

import re

__all__ = ["safe_name", "windows_safe"]

_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"COM{i}" for i in range(1, 10)]
    + [f"LPT{i}" for i in range(1, 10)]
)
_UNSAFE = re.compile(r"[^\w.-]+")


def windows_safe(name: str) -> str:
    """Prefix ``_`` to a name Windows reserves for a device (``nul.log``, ``COM1``, ``aux``).

    Windows maps such a name to a device in any directory and with any extension, so
    writing to it loses the bytes or fails; other names are returned unchanged.

    Examples:
        >>> windows_safe("nul.log"), windows_safe("Com1"), windows_safe("console.log")
        ('_nul.log', '_Com1', 'console.log')

    """
    return "_" + name if name.split(".", 1)[0].upper() in _RESERVED else name


def safe_name(text: str, fallback: str) -> str:
    """``text`` reduced to ``[A-Za-z0-9_.-]`` for a file name, ``fallback`` when nothing is left.

    Other characters become ``_``; leading and trailing dots and underscores are dropped and
    a device name is prefixed (:func:`windows_safe`).

    Examples:
        >>> safe_name("prod 2026/10", "x"), safe_name("..", "x"), safe_name("nul", "x")
        ('prod_2026_10', 'x', '_nul')

    """
    return windows_safe(_UNSAFE.sub("_", text).strip("._") or fallback)
