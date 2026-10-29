"""Streaming aggregation of signed entries into groups."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from atlas_log_analyzer.core.models import LogEntry, LogLevel
from atlas_log_analyzer.core.signature import Signature, Signer

__all__ = ["Group", "Grouper", "SignedEntry"]


@dataclass(frozen=True, slots=True)
class SignedEntry:
    """An entry together with its two signatures; the unit the store writes."""

    entry: LogEntry
    signature: Signature
    coarse: Signature


@dataclass(slots=True)
class Group:
    """Statistics for all entries sharing one signature. Mutable while aggregating."""

    signature: str
    coarse_signature: str
    template: str
    level: LogLevel
    logger: str
    exception_type: str | None
    count: int = 0
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    users: Counter[str] = field(default_factory=Counter)
    threads: Counter[str] = field(default_factory=Counter)
    sources: Counter[str] = field(default_factory=Counter)
    examples: list[str] = field(default_factory=list)
    """Entry ids of up to ``Grouper.max_examples`` representative entries."""


class Grouper:
    """Aggregates :class:`SignedEntry` objects into :class:`Group` objects, keyed by signature."""

    def __init__(self, signer: Signer, *, max_examples: int = 3) -> None:
        self.signer = signer
        self.max_examples = max_examples
        self.groups: dict[str, Group] = {}

    def sign(self, entries: Iterable[LogEntry]) -> Iterator[SignedEntry]:
        """Sign entries, update group statistics, and yield the signed entries (streaming)."""
        raise NotImplementedError

    def add(self, signed: SignedEntry) -> None:
        """Update statistics with one already-signed entry."""
        raise NotImplementedError

    def sorted_groups(self, key: str = "count") -> list[Group]:
        """Groups sorted by ``count`` (desc), ``first_seen`` or ``last_seen``."""
        raise NotImplementedError
