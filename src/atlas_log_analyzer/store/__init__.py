"""Persistence of log sets (Parquet + JSON manifest) and queries over them (DuckDB).

Public API::

    from atlas_log_analyzer.store import Store, ingest_paths
    store = Store(Path(".atlas-log"))
    result = ingest_paths(store, [Path("logs/")], name="prod-2026-10-29")
    store.list()                       # [LogSetManifest, ...]
    ls = store.open("prod-2026-10-29") # by id, name or id prefix
    ls.groups()                        # polars DataFrame
    ls.entries(signature="…")          # polars DataFrame
    ls.sql("select … from entries")    # DuckDB → polars DataFrame
    diff(store.open(a), store.open(b)) # DiffResult

Stable read API (used by the CLI and the web app): :meth:`Store.list`,
:meth:`Store.open` (raising :class:`LogSetNotFound` / :class:`AmbiguousLogSet`,
both :class:`StoreError`) and the read methods of :class:`LogSet` —
``manifest``, ``groups``/``count_groups``/``group``, ``entries``/``count_entries``/
``entry``/``context``, ``histogram``, ``stats`` and ``sql`` — plus :func:`rows` and
:func:`to_records` to turn frames into Python or JSON-ready dicts.

Names are resolved on first access (PEP 562), so ``import atlas_log_analyzer.store``
loads neither polars nor pyarrow, and reading a set does not load the ingest
pipeline, the Parquet writer or DuckDB.
"""

from __future__ import annotations

import importlib
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from atlas_log_analyzer.store.logset import (
        LevelSpec,
        LogSet,
        LogSetManifest,
        SourceInfo,
        rows,
        to_records,
    )
    from atlas_log_analyzer.store.pipeline import (
        FormatNotDetected,
        IngestProgress,
        IngestResult,
        NoSourcesFound,
        ingest_paths,
        ingest_sources,
    )
    from atlas_log_analyzer.store.query import DiffResult, diff
    from atlas_log_analyzer.store.schema import SCHEMA_VERSION, schema_json
    from atlas_log_analyzer.store.store import AmbiguousLogSet, LogSetNotFound, Store, StoreError

_EXPORTS: dict[str, str] = {
    "LevelSpec": "logset",
    "LogSet": "logset",
    "LogSetManifest": "logset",
    "SourceInfo": "logset",
    "rows": "logset",
    "to_records": "logset",
    "FormatNotDetected": "pipeline",
    "IngestProgress": "pipeline",
    "IngestResult": "pipeline",
    "NoSourcesFound": "pipeline",
    "ingest_paths": "pipeline",
    "ingest_sources": "pipeline",
    "DiffResult": "query",
    "diff": "query",
    "SCHEMA_VERSION": "schema",
    "schema_json": "schema",
    "AmbiguousLogSet": "store",
    "LogSetNotFound": "store",
    "Store": "store",
    "StoreError": "store",
}
"""Public name → submodule defining it."""

__all__ = [
    "SCHEMA_VERSION",
    "AmbiguousLogSet",
    "DiffResult",
    "FormatNotDetected",
    "IngestProgress",
    "IngestResult",
    "LevelSpec",
    "LogSet",
    "LogSetManifest",
    "LogSetNotFound",
    "NoSourcesFound",
    "SourceInfo",
    "Store",
    "StoreError",
    "diff",
    "ingest_paths",
    "ingest_sources",
    "rows",
    "schema_json",
    "to_records",
]


def __getattr__(name: str) -> Any:
    """Import the submodule defining ``name`` on first access and cache the value."""
    module = _EXPORTS.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f"{__name__}.{module}"), name)
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted({*globals(), *_EXPORTS})
