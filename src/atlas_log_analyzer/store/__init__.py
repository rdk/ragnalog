"""Persistence of log sets (Parquet + JSON manifest) and queries over them (DuckDB).

Public API::

    from atlas_log_analyzer.store import Store
    store = Store(Path(".atlas-log"))
    logset = store.ingest(signed_entries, groups, sources, fmt, name="prod-2026-10-29")
    store.list()                       # [LogSetManifest, ...]
    ls = store.open("prod-2026-10-29") # by name or id
    ls.groups()                        # polars DataFrame
    ls.entries(signature="…")          # polars DataFrame
    ls.sql("select … from entries")    # DuckDB → polars DataFrame
    diff(store.open(a), store.open(b)) # DiffResult
"""

from atlas_log_analyzer.store.logset import LogSet, LogSetManifest, SourceInfo, rows, to_records
from atlas_log_analyzer.store.query import DiffResult, diff
from atlas_log_analyzer.store.schema import SCHEMA_VERSION, schema_json
from atlas_log_analyzer.store.store import Store, StoreError

__all__ = [
    "SCHEMA_VERSION",
    "DiffResult",
    "LogSet",
    "LogSetManifest",
    "SourceInfo",
    "Store",
    "StoreError",
    "diff",
    "rows",
    "schema_json",
    "to_records",
]
