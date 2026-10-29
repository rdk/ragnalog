"""Streaming aggregation of signed entries into groups."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime

from atlas_log_analyzer.core.formats.base import CTX_USER
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
    """Entry ids of up to ``Grouper.max_examples`` representative entries.

    The first ``max_examples - 1`` entries seen plus the latest one, so the
    examples span the group's lifetime instead of its first millisecond.
    """


_SORT_KEYS = ("count", "first_seen", "last_seen")


class Grouper:
    """Aggregates :class:`SignedEntry` objects into :class:`Group` objects, keyed by signature."""

    def __init__(self, signer: Signer, *, max_examples: int = 3) -> None:
        self.signer = signer
        self.max_examples = max_examples
        self.groups: dict[str, Group] = {}

    def sign(self, entries: Iterable[LogEntry]) -> Iterator[SignedEntry]:
        """Sign entries, update group statistics, and yield the signed entries (streaming)."""
        sign_both = self.signer.sign_both
        add = self.add
        for entry in entries:
            full, coarse = sign_both(entry)
            signed = SignedEntry(entry, full, coarse)
            add(signed)
            yield signed

    def add(self, signed: SignedEntry) -> None:
        """Update statistics with one already-signed entry."""
        entry = signed.entry
        key = signed.signature.hash
        group = self.groups.get(key)
        if group is None:
            group = Group(
                signature=key,
                coarse_signature=signed.coarse.hash,
                template=signed.signature.template,
                level=entry.level,
                logger=entry.logger,
                exception_type=entry.exception_type,
            )
            self.groups[key] = group
        group.count += 1
        ts = entry.timestamp
        if group.first_seen is None or ts < group.first_seen:
            group.first_seen = ts
        if group.last_seen is None or ts > group.last_seen:
            group.last_seen = ts
        user = entry.context.get(CTX_USER)
        if user:
            group.users[user] += 1
        if entry.thread:
            group.threads[entry.thread] += 1
        group.sources[entry.source.path] += 1
        examples = group.examples
        if len(examples) < self.max_examples:
            examples.append(entry.id)
        else:
            examples[-1] = entry.id

    def sorted_groups(self, key: str = "count") -> list[Group]:
        """Return groups sorted by ``count`` (desc), ``first_seen`` (asc) or ``last_seen`` (desc).

        Ties are broken by signature so the order is deterministic.

        Raises:
            ValueError: For an unknown ``key``.

        """
        groups = sorted(self.groups.values(), key=lambda g: g.signature)
        if key == "count":
            groups.sort(key=lambda g: g.count, reverse=True)
        elif key == "first_seen":
            groups.sort(key=lambda g: _ts_key(g.first_seen))
        elif key == "last_seen":
            groups.sort(key=lambda g: _ts_key(g.last_seen), reverse=True)
        else:
            raise ValueError(f"unknown sort key {key!r}; expected one of {_SORT_KEYS}")
        return groups


def _ts_key(ts: datetime | None) -> float:
    return ts.timestamp() if ts is not None else float("-inf")
