"""JSON responses: msgspec encoding, and cached report payloads with ETag and gzip."""

from __future__ import annotations

import gzip
import threading
from collections import OrderedDict
from collections.abc import Sequence
from typing import Any

import msgspec
import xxhash
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

__all__ = ["JSON", "payload_response"]


def _enc_hook(obj: Any) -> Any:
    if isinstance(obj, Sequence):  # a RecordStream (rows serialised in batches)
        return list(obj)
    raise NotImplementedError(f"cannot encode {type(obj).__name__} as JSON")


_ENCODER = msgspec.json.Encoder(enc_hook=_enc_hook)
_GZIPPED: OrderedDict[str, bytes] = OrderedDict()
_GZIP_LOCK = threading.Lock()
_GZIP_KEEP = 8
"""Compressed payloads kept (the report caches hold the same few payloads uncompressed)."""


class JSON(JSONResponse):
    """``application/json`` via msgspec; datetimes become ISO 8601 strings."""

    def render(self, content: Any) -> bytes:
        """Encode ``content``."""
        return _ENCODER.encode(content)


def _gzipped(tag: str, data: bytes) -> bytes:
    with _GZIP_LOCK:
        hit = _GZIPPED.get(tag)
        if hit is not None:
            _GZIPPED.move_to_end(tag)
            return hit
    packed = gzip.compress(data, compresslevel=6, mtime=0)
    with _GZIP_LOCK:
        _GZIPPED[tag] = packed
        while len(_GZIPPED) > _GZIP_KEEP:
            _GZIPPED.popitem(last=False)
    return packed


def payload_response(request: Request, data: bytes) -> Response:
    """Return an encoded report payload: ``304`` when the browser has it, else gzipped.

    The ETag is a hash of the bytes, so a revisit (``Cache-Control: no-cache``) costs a
    round trip instead of megabytes; the compressed bytes are kept for the next client
    (multi-MB payloads compress about tenfold). Call it from a worker thread: compressing a
    new payload takes tens of milliseconds.
    """
    tag = f'"{xxhash.xxh3_64_hexdigest(data)}"'
    headers = {"ETag": tag, "Cache-Control": "no-cache", "Vary": "Accept-Encoding"}
    if tag in {t.strip() for t in request.headers.get("if-none-match", "").split(",")}:
        return Response(status_code=304, headers=headers)
    if "gzip" in request.headers.get("accept-encoding", "").lower():
        headers["Content-Encoding"] = "gzip"
        return Response(_gzipped(tag, data), media_type="application/json", headers=headers)
    return Response(data, media_type="application/json", headers=headers)
