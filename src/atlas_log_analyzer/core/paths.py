"""File names that are safe on every platform the tool runs on."""

from __future__ import annotations

import re

__all__ = ["safe_name", "windows_safe"]

_RESERVED = frozenset(
    ["CON", "PRN", "AUX", "NUL"]
    + [f"{d}{i}" for d in ("COM", "LPT") for i in [*"123456789", "¹", "²", "³"]]
    + ["CONIN$", "CONOUT$"]
)
"""Device names in any case, with any extension; COM¹-COM³ and LPT¹-LPT³ (superscript
digits) are devices too."""
MAX_NAME = 120
"""Longest name :func:`safe_name` returns (room for a prefix, suffix and the store path)."""
_UNSAFE = re.compile(r"[^A-Za-z0-9_.-]+")  # ASCII only: Unicode \w lets COM¹ through


def windows_safe(name: str) -> str:
    """Prefix ``_`` to a name Windows reserves for a device (``nul.log``, ``COM1``, ``aux``).

    Windows maps such a name to a device in any directory and with any extension, so
    writing to it loses the bytes or fails; other names are returned unchanged.

    Examples:
        >>> windows_safe("nul.log"), windows_safe("Com1"), windows_safe("console.log")
        ('_nul.log', '_Com1', 'console.log')
        >>> windows_safe("COM¹.log"), windows_safe("aux .txt")
        ('_COM¹.log', '_aux .txt')

    """
    # Windows also ignores spaces before the extension: "nul .log" is NUL
    return "_" + name if name.split(".", 1)[0].rstrip(" ").upper() in _RESERVED else name


def safe_name(text: str, fallback: str) -> str:
    """``text`` reduced to ``[A-Za-z0-9_.-]`` for a file name, ``fallback`` when nothing is left.

    Other characters become ``_``; leading and trailing dots and underscores are dropped and
    a device name is prefixed (:func:`windows_safe`).

    Examples:
        >>> safe_name("prod 2026/10", "x"), safe_name("..", "x"), safe_name("nul", "x")
        ('prod_2026_10', 'x', '_nul')
        >>> safe_name("COM¹.log", "x"), safe_name("název.log", "x")
        ('COM_.log', 'n_zev.log')
        >>> len(safe_name("a" * 300 + ".log.1", "x")), safe_name("a" * 300 + ".log.1", "x")[-8:]
        (120, 'aa.log.1')

    """
    name = _UNSAFE.sub("_", text).strip("._") or fallback
    if len(name) > MAX_NAME:  # file systems refuse names past 255 bytes: keep the extension
        stem, dot, ext = name.partition(".")
        ext = (dot + ext)[-MAX_NAME // 3 :] if dot else ""
        name = stem[: MAX_NAME - len(ext)].rstrip("._") + ext
    return windows_safe(name)
