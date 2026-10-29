"""Log format implementations and the format registry.

Public API::

    from atlas_log_analyzer.core import formats
    fmt = formats.get("jira")
    fmt = formats.detect(first_lines)      # or None
    formats.names()                        # ["jira", ...]

Formats register through the ``atlas_log_analyzer.formats`` entry-point group
and through :func:`register` for in-process additions.
"""

from atlas_log_analyzer.core.formats.base import (
    CTX_IP,
    CTX_REQUEST_ID,
    CTX_REQUEST_URL,
    CTX_SESSION,
    CTX_URL,
    CTX_USER,
    HeaderMatch,
    LogFormat,
)
from atlas_log_analyzer.core.formats.registry import detect, get, names, register

__all__ = [
    "CTX_IP",
    "CTX_REQUEST_ID",
    "CTX_REQUEST_URL",
    "CTX_SESSION",
    "CTX_URL",
    "CTX_USER",
    "HeaderMatch",
    "LogFormat",
    "detect",
    "get",
    "names",
    "register",
]
