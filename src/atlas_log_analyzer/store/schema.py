"""Versioned Arrow schemas for the store files.

``SCHEMA_VERSION`` is ``"<major>.<minor>"``; readers refuse a newer major.
"""

from __future__ import annotations

import pyarrow as pa

__all__ = ["ENTRIES_SCHEMA", "GROUPS_SCHEMA", "MERGES_SCHEMA", "SCHEMA_VERSION", "schema_json"]

SCHEMA_VERSION = "1.0"

ENTRIES_SCHEMA: pa.Schema
"""entries.v1 — one row per entry. See docs/formats/entries.v1.md."""

GROUPS_SCHEMA: pa.Schema
"""groups.v1 — one row per signature. See docs/formats/groups.v1.md."""

MERGES_SCHEMA: pa.Schema
"""merges.v1 — Drain3 merge proposals: cluster_id, signatures (list), drain_template."""


def schema_json(name: str) -> dict[str, object]:
    """JSON-serialisable description of ``entries``/``groups``/``merges``/``logset`` for agents."""
    raise NotImplementedError
