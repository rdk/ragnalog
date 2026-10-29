"""The :class:`Store`: a directory holding log sets."""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from atlas_log_analyzer.core.formats.base import LogFormat
from atlas_log_analyzer.core.grouping import Group, SignedEntry
from atlas_log_analyzer.core.reader import SourceFile
from atlas_log_analyzer.core.signature import SignatureSpec
from atlas_log_analyzer.store.logset import LogSet, LogSetManifest

__all__ = ["Store", "StoreError"]


class StoreError(Exception):
    """Any store-level failure (missing set, corrupt manifest, version mismatch)."""


class Store:
    """Directory of log sets; creates it on first write."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def list(self) -> list[LogSetManifest]:
        """Manifests of all log sets, newest first."""
        raise NotImplementedError

    def open(self, ref: str) -> LogSet:
        """Open a log set by id, unique id prefix, or name; raise :class:`StoreError`."""
        raise NotImplementedError

    def ingest(
        self,
        signed: Iterable[SignedEntry],
        groups_after: "callable[[], Iterable[Group]]",
        sources: Iterable[SourceFile],
        fmt: LogFormat,
        *,
        name: str | None = None,
        spec: SignatureSpec,
        overwrite: bool = False,
    ) -> LogSet:
        """Write a new log set.

        ``signed`` is consumed once, streamed into ``entries.parquet`` in
        batches with deduplication on ``entry.id``. ``groups_after`` is called
        after the stream is exhausted (the grouper has seen everything by then)
        and its result written to ``groups.parquet``. The log-set id is derived
        from the source digests; an existing set with the same id is left
        untouched unless ``overwrite`` is true.
        """
        raise NotImplementedError

    def regroup(self, ref: str, spec: SignatureSpec) -> LogSet:
        """Recompute ``groups.parquet`` from the stored entries with another spec."""
        raise NotImplementedError

    def remove(self, ref: str) -> None:
        """Delete a log set directory."""
        raise NotImplementedError
