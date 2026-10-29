"""A JSON response encoded with msgspec (fast on multi-MB report payloads)."""

from __future__ import annotations

from typing import Any

import msgspec
from starlette.responses import JSONResponse

__all__ = ["JSON"]

_ENCODER = msgspec.json.Encoder()


class JSON(JSONResponse):
    """``application/json`` via msgspec; datetimes become ISO 8601 strings."""

    def render(self, content: Any) -> bytes:
        """Encode ``content``."""
        return _ENCODER.encode(content)
