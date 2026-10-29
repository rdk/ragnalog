"""Log set manifest and directory conventions.

Directory layout::

    <store>/<logset-id>/
        manifest.json      logset.v1
        entries.parquet    entries.v1
        groups.parquet     groups.v1
        merges.parquet     merges.v1 (optional)
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import polars as pl

__all__ = ["LogSet", "LogSetManifest", "SourceInfo"]

MANIFEST_FILE = "manifest.json"
ENTRIES_FILE = "entries.parquet"
GROUPS_FILE = "groups.parquet"
MERGES_FILE = "merges.parquet"


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """One ingested file as recorded in the manifest."""

    path: str
    size: int
    xxh3: str
    first_ts: datetime | None
    last_ts: datetime | None
    entries: int


@dataclass(frozen=True, slots=True)
class LogSetManifest:
    """``manifest.json`` (schema ``logset.v1``)."""

    id: str
    """xxh3-64 hex of the sorted source digests."""
    name: str | None
    format: str
    sources: tuple[SourceInfo, ...]
    first_ts: datetime | None
    last_ts: datetime | None
    entry_count: int
    group_count: int
    created_at: datetime
    tool_version: str
    schema_version: str
    signature_version: int
    mask_rules_version: int
    signature_spec: str
    """``SignatureSpec.key`` used for the primary signature."""

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable dict with ``"schema": "logset.v1"``."""
        raise NotImplementedError

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogSetManifest:
        """Inverse of :meth:`to_dict`; raises ``ValueError`` on an unsupported schema version."""
        raise NotImplementedError


class LogSet:
    """A stored log set: manifest plus lazy access to its Parquet files."""

    def __init__(self, path: Path, manifest: LogSetManifest) -> None:
        self.path = path
        self.manifest = manifest

    @classmethod
    def load(cls, path: Path) -> LogSet:
        """Open the log set stored in ``path``."""
        raise NotImplementedError

    def entries_lazy(self) -> pl.LazyFrame:
        """``entries.parquet`` as a lazy frame."""
        raise NotImplementedError

    def groups_lazy(self) -> pl.LazyFrame:
        """``groups.parquet`` as a lazy frame."""
        raise NotImplementedError

    def groups(
        self,
        *,
        level: str | None = None,
        min_level: str | None = None,
        logger: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        sort: str = "count",
        limit: int | None = None,
        coarse: bool = False,
    ) -> pl.DataFrame:
        """Filtered, sorted groups. ``coarse=True`` aggregates by ``coarse_signature``."""
        raise NotImplementedError

    def entries(
        self,
        *,
        signature: str | None = None,
        level: str | None = None,
        min_level: str | None = None,
        logger: str | None = None,
        user: str | None = None,
        thread: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        contains: str | None = None,
        limit: int | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """Filtered entries, ordered by timestamp."""
        raise NotImplementedError

    def entry(self, entry_id: str) -> dict[str, Any] | None:
        """One entry as a dict (all columns), or ``None``."""
        raise NotImplementedError

    def stats(self) -> dict[str, Any]:
        """Counts per level, per logger (top N), per hour; time range; sources."""
        raise NotImplementedError

    def sql(self, query: str) -> pl.DataFrame:
        """Run DuckDB SQL with views ``entries`` and ``groups`` (and ``merges`` when present)."""
        raise NotImplementedError
