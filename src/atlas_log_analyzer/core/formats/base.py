"""The :class:`LogFormat` protocol every format implements, and well-known context keys."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol, runtime_checkable

from atlas_log_analyzer.core.masking import MaskRule
from atlas_log_analyzer.core.models import LogEntry, LogLevel, SourceRef
from atlas_log_analyzer.core.signature import SignatureSpec

__all__ = [
    "CTX_IP",
    "CTX_REQUEST_ID",
    "CTX_REQUEST_URL",
    "CTX_SESSION",
    "CTX_URL",
    "CTX_USER",
    "WELL_KNOWN_CONTEXT_KEYS",
    "HeaderMatch",
    "LogFormat",
]

# Well-known ``LogEntry.context`` keys. Each of these gets its own column in the
# store; any other key lands in the generic ``context`` map column.
CTX_USER = "user"
CTX_REQUEST_ID = "request_id"
CTX_SESSION = "session"
CTX_IP = "ip"
CTX_URL = "url"
"""URL(s) from the thread segment (``url: /a, /b``)."""
CTX_REQUEST_URL = "request_url"
"""URL from the request-context segment."""

WELL_KNOWN_CONTEXT_KEYS: tuple[str, ...] = (
    CTX_USER,
    CTX_REQUEST_ID,
    CTX_SESSION,
    CTX_IP,
    CTX_URL,
    CTX_REQUEST_URL,
)


@dataclass(frozen=True, slots=True)
class HeaderMatch:
    """Fields extracted from a recognised header line."""

    timestamp: datetime
    level: LogLevel
    thread: str
    logger: str
    message: str
    context: Mapping[str, str]


@runtime_checkable
class LogFormat(Protocol):
    """A log file layout: how to recognise headers and build entries.

    Implementations are stateless and cheap to instantiate. ``is_header`` must
    be very fast (it runs on every line); ``parse_header`` does the real work.
    """

    name: str
    """Registry key, e.g. ``"jira"``."""
    description: str
    mask_rules: Sequence[MaskRule]
    """Format-specific masking rules, applied before the generic rules."""
    signature_spec: SignatureSpec
    """Default signature specification for this format."""

    def is_header(self, line: str) -> bool:
        """Cheap test whether ``line`` starts a new entry (e.g. begins with a timestamp)."""
        ...

    def parse_header(self, line: str) -> HeaderMatch | None:
        """Fully parse a header line; ``None`` when it is not a valid header after all."""
        ...

    def build(
        self,
        header: HeaderMatch | None,
        header_line: str,
        continuation: Sequence[str],
        source: SourceRef,
    ) -> LogEntry:
        """Assemble a :class:`LogEntry` from a parsed header and its continuation lines.

        When ``header`` is ``None`` the entry must be built with ``parse_error``
        set and ``message`` equal to ``header_line``.
        """
        ...

    def sniff(self, lines: Sequence[str]) -> float:
        """Confidence in ``[0, 1]`` that ``lines`` (first lines of a file) are in this format."""
        ...
