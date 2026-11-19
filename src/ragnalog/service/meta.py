"""Documents about the tool rather than a set: version, formats, schema, health.

``version`` and ``formats`` load no polars (the startup guard checks them).
"""

from __future__ import annotations

import sys
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Any

from ragnalog import __version__
from ragnalog.service.errors import Front, OpError

if TYPE_CHECKING:
    from ragnalog.service.sets import Sets

__all__ = ["SNIFF_LINES", "TABLES", "formats", "health", "schema", "version"]

type Doc = dict[str, Any]

SNIFF_LINES = 200
"""Leading lines of a ``formats --sample`` file that every format is tested against."""
TABLES = ("entries", "groups", "merges", "logset")
"""The stored tables of ``schema`` (also the views of ``query``, plus the manifest)."""


def version() -> Doc:
    """``version.v1``: tool, schema, signature and masking-rule versions."""
    from ragnalog.core.masking import MASK_RULES_VERSION
    from ragnalog.core.signature import SIGNATURE_VERSION
    from ragnalog.store.schema import SCHEMA_VERSION

    return {
        "schema": "version.v1",
        "version": __version__,
        "schema_version": SCHEMA_VERSION,
        "signature_version": SIGNATURE_VERSION,
        "mask_rules_version": MASK_RULES_VERSION,
        "python": sys.version.split()[0],
    }


def formats(sample: Path | None = None) -> Doc:
    """``formats.v1``: every registered format with its signature spec and masking rules.

    With ``sample`` each item also has ``confidence`` (0..1) from sniffing the first
    :data:`SNIFF_LINES` lines of that file, and items are sorted by it.

    Raises:
        FileNotFoundError: ``sample`` does not exist (``file_not_found``).
        OpError: ``usage`` when ``sample`` is a directory.

    """
    from ragnalog.core import formats as registry
    from ragnalog.core.reader import iter_lines

    lines = None
    if sample is not None:
        if sample.is_dir():
            raise OpError(
                "usage",
                f"--sample {sample.as_posix()} is a directory",
                hint="name one log file of the directory",
            )
        lines = [line.text for line in islice(iter_lines(sample), SNIFF_LINES)]
    items: list[dict[str, Any]] = []
    for name in registry.names():
        fmt = registry.get(name)
        item: dict[str, Any] = {
            "name": fmt.name,
            "description": getattr(fmt, "description", ""),
            "signature_spec": fmt.signature_spec.key,
            "mask_rules": [rule.name for rule in fmt.mask_rules],
        }
        if lines is not None:
            item["confidence"] = round(float(fmt.sniff(lines)), 3)
        items.append(item)
    if lines is not None:
        items.sort(key=lambda i: -float(i["confidence"]))
    return {"schema": "formats.v1", "count": len(items), "items": items}


def schema(name: str | None, *, front: Front) -> Doc:
    """``schema.v1``: a stored table's columns, or the JSON Schema of a document.

    Without ``name`` the four tables, plus ``documents`` listing the document names.

    Raises:
        OpError: ``usage`` for an unknown name.

    """
    from ragnalog.service.documents import document_names, document_schema
    from ragnalog.store.schema import schema_json

    documents = document_names()
    if name is None:
        return {
            "schema": "schema.v1",
            "definitions": {n: schema_json(n) for n in TABLES},
            "documents": documents,
        }
    if name in TABLES:
        return {"schema": "schema.v1", "definitions": {name: schema_json(name)}}
    if name in documents:
        return {"schema": "schema.v1", "definitions": {name: document_schema(name)}}
    tables = ", ".join(f'"{n}"' for n in TABLES)
    raise OpError(
        "usage",
        f"unknown schema {name!r}; expected a table ({tables}) or a document name "
        'such as "triage.v1" (listed under "documents" by `ragnalog schema`)',
        hint="ragnalog schema --help" if front == "cli" else None,
    )


def health(sets: Sets) -> Doc:
    """``health.v1``: the data directory is readable; how many sets it holds."""
    return {"schema": "health.v1", "status": "ok", "sets": len(sets.manifests())}
