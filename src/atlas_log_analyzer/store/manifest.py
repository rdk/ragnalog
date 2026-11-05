"""The ``logset.v1`` manifest and the file names of a log-set directory.

Kept apart from :mod:`atlas_log_analyzer.store.logset` so that listing a store
(:meth:`Store.list`, ``atlas-log sets``) reads manifests without loading polars.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from atlas_log_analyzer.core.pipeline import recipe_key
from atlas_log_analyzer.store.schema import SCHEMA_VERSION, schema_major

__all__ = [
    "ENTRIES_FILE",
    "GROUPS_FILE",
    "MANIFEST_FILE",
    "MANIFEST_SCHEMA",
    "MERGES_FILE",
    "LogSetManifest",
    "SourceInfo",
    "current_recipe_key",
    "derived_recipe_key",
]

MANIFEST_FILE = "manifest.json"
ENTRIES_FILE = "entries.parquet"
GROUPS_FILE = "groups.parquet"
MERGES_FILE = "merges.parquet"

MANIFEST_SCHEMA = "logset.v1"


def _iso(ts: datetime | None) -> str | None:
    return None if ts is None else ts.isoformat()


def _parse_ts(text: str | None) -> datetime | None:
    if text is None:
        return None
    ts = datetime.fromisoformat(text)
    return ts if ts.tzinfo is not None else ts.replace(tzinfo=UTC)


def _opt_str(value: Any) -> str | None:
    """Return an optional text field: ``None`` for a missing, null or empty value."""
    return str(value) if value not in (None, "") else None


def derived_recipe_key(
    format_name: str, signature_spec: str, signature_version: int, mask_rules_version: int
) -> str:
    """Recipe key of a manifest written before ``recipe_key`` existed (format version 1)."""
    return recipe_key(
        format_name,
        1,
        signature_spec,
        signature_version=signature_version,
        mask_rules_version=mask_rules_version,
    )


def current_recipe_key(format_name: str, signature_spec: str) -> str | None:
    """Key of the recipe this tool would use today for the format and spec.

    ``None`` when the format is not registered or the spec key is malformed (the
    set cannot be rebuilt, so it is not reported as stale either).
    """
    from atlas_log_analyzer.core import formats
    from atlas_log_analyzer.core.pipeline import Recipe
    from atlas_log_analyzer.core.signature import SignatureSpec

    try:
        return Recipe(formats.get(format_name), SignatureSpec.from_key(signature_spec)).key
    except LookupError, ValueError:
        return None


@dataclass(frozen=True, slots=True)
class SourceInfo:
    """One ingested file as recorded in the manifest."""

    path: str
    size: int
    xxh3: str
    first_ts: datetime | None
    last_ts: datetime | None
    entries: int

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable dict (ISO timestamps)."""
        return {
            "path": self.path,
            "size": self.size,
            "xxh3": self.xxh3,
            "first_ts": _iso(self.first_ts),
            "last_ts": _iso(self.last_ts),
            "entries": self.entries,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SourceInfo:
        """Inverse of :meth:`to_dict`."""
        return cls(
            path=str(data["path"]),
            size=int(data["size"]),
            xxh3=str(data["xxh3"]),
            first_ts=_parse_ts(data.get("first_ts")),
            last_ts=_parse_ts(data.get("last_ts")),
            entries=int(data.get("entries", 0)),
        )


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
    recipe_key: str = field(default="")
    """:attr:`~atlas_log_analyzer.core.pipeline.Recipe.key` the set was signed with; sets are
    comparable when it is equal. Derived from the version fields (format version 1) when
    empty, as for manifests written before it existed."""
    node: str | None = None
    """Cluster node id the logs come from; ``None`` when unknown (schema 1.0 sets)."""
    cluster: str | None = None
    """Name of the instance (cluster) the node belongs to."""
    capture: str | None = None
    """Id of the capture: the sets collected together (one export job, one split ingest)."""

    def __post_init__(self) -> None:
        if not self.recipe_key:
            key = derived_recipe_key(
                self.format, self.signature_spec, self.signature_version, self.mask_rules_version
            )
            object.__setattr__(self, "recipe_key", key)

    def to_dict(self) -> dict[str, Any]:
        """JSON-serialisable dict with ``"schema": "logset.v1"``."""
        return {
            "schema": MANIFEST_SCHEMA,
            "id": self.id,
            "name": self.name,
            "format": self.format,
            "sources": [s.to_dict() for s in self.sources],
            "first_ts": _iso(self.first_ts),
            "last_ts": _iso(self.last_ts),
            "entry_count": self.entry_count,
            "group_count": self.group_count,
            "created_at": self.created_at.isoformat(),
            "tool_version": self.tool_version,
            "schema_version": self.schema_version,
            "signature_version": self.signature_version,
            "mask_rules_version": self.mask_rules_version,
            "signature_spec": self.signature_spec,
            "recipe_key": self.recipe_key,
            "node": self.node,
            "cluster": self.cluster,
            "capture": self.capture,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> LogSetManifest:
        """Inverse of :meth:`to_dict`; raises ``ValueError`` on an unsupported schema version."""
        schema = data.get("schema")
        if not isinstance(schema, str) or not schema.startswith("logset.v"):
            raise ValueError(f"not a logset manifest (schema={schema!r})")
        try:
            schema_v = int(schema.removeprefix("logset.v"))
            version = str(data["schema_version"])
            major = schema_major(version)
        except (KeyError, ValueError) as exc:
            raise ValueError(f"invalid manifest schema version: {exc}") from exc
        current = schema_major(SCHEMA_VERSION)
        if schema_v > current or major > current:
            raise ValueError(
                f"log set uses schema {schema} / {version}, newer than supported {SCHEMA_VERSION}"
            )
        try:
            created = _parse_ts(data["created_at"])
            assert created is not None
            return cls(
                id=str(data["id"]),
                name=data.get("name"),
                format=str(data["format"]),
                sources=tuple(SourceInfo.from_dict(s) for s in data.get("sources", ())),
                first_ts=_parse_ts(data.get("first_ts")),
                last_ts=_parse_ts(data.get("last_ts")),
                entry_count=int(data["entry_count"]),
                group_count=int(data["group_count"]),
                created_at=created,
                tool_version=str(data["tool_version"]),
                schema_version=version,
                signature_version=int(data["signature_version"]),
                mask_rules_version=int(data["mask_rules_version"]),
                signature_spec=str(data["signature_spec"]),
                recipe_key=str(data.get("recipe_key") or ""),
                node=_opt_str(data.get("node")),
                cluster=_opt_str(data.get("cluster")),
                capture=_opt_str(data.get("capture")),
            )
        except (KeyError, TypeError, AssertionError) as exc:
            raise ValueError(f"invalid manifest: missing or bad field {exc}") from exc

    def write(self, directory: Path) -> None:
        """Write ``manifest.json`` into ``directory``."""
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        (directory / MANIFEST_FILE).write_text(text, encoding="utf-8", newline="\n")

    def replace_in(self, directory: Path) -> None:
        """Rewrite ``manifest.json`` of an installed set atomically (temp file, then rename)."""
        tmp = directory / f".{MANIFEST_FILE}.{os.getpid()}.{threading.get_ident()}.tmp"
        text = json.dumps(self.to_dict(), indent=2, ensure_ascii=False) + "\n"
        try:
            tmp.write_text(text, encoding="utf-8", newline="\n")
            os.replace(tmp, directory / MANIFEST_FILE)
        finally:
            tmp.unlink(missing_ok=True)

    @classmethod
    def read(cls, directory: Path) -> LogSetManifest:
        """Read ``manifest.json`` from ``directory``; ``ValueError`` when invalid."""
        try:
            data = json.loads((directory / MANIFEST_FILE).read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise ValueError(f"corrupt manifest in {directory}: {exc}") from exc
        if not isinstance(data, dict):
            raise ValueError(f"corrupt manifest in {directory}: not an object")
        return cls.from_dict(data)
