"""Server logging: uvicorn's configuration with the API token kept out of the access log."""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

__all__ = ["RedactToken", "log_config"]

_TOKEN = re.compile(r"(?i)\b(token=)[^&\s\"]+")


class RedactToken(logging.Filter):
    """Replace ``token=VALUE`` in a record's message and arguments with ``token=***``.

    The first visit opens ``/?token=SECRET`` (the server then sets a cookie); without this
    filter that request line, secret included, is printed by the access log.

    Examples:
        >>> r = logging.LogRecord("a", 20, "", 0, '%s "%s"', ("ip", "GET /?token=s3&x=1"), None)
        >>> RedactToken().filter(r), r.getMessage()
        (True, 'ip "GET /?token=***&x=1"')

    """

    def filter(self, record: logging.LogRecord) -> bool:
        """Redact the record in place; always keep it."""
        if isinstance(record.msg, str):
            record.msg = _TOKEN.sub(r"\1***", record.msg)
        if isinstance(record.args, tuple):
            record.args = tuple(
                _TOKEN.sub(r"\1***", a) if isinstance(a, str) else a for a in record.args
            )
        return True


def log_config() -> dict[str, Any]:
    """Return uvicorn's default logging configuration, :class:`RedactToken` on every handler."""
    from uvicorn.config import LOGGING_CONFIG

    config = copy.deepcopy(LOGGING_CONFIG)
    config.setdefault("filters", {})["redact_token"] = {"()": f"{__name__}.RedactToken"}
    for handler in config.get("handlers", {}).values():
        handler.setdefault("filters", []).append("redact_token")
    return config
