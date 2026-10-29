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
``entry``, ``histogram``, ``stats`` and ``sql`` — plus :func:`rows` and
:func:`to_records` to turn frames into Python or JSON-ready dicts.
"""

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
