"""Regenerate the format docs in this directory from ``atlas_log_analyzer.store.schema``.

Run: ``uv run python docs/formats/generate.py``
"""

from __future__ import annotations

from pathlib import Path

from atlas_log_analyzer.store.schema import SCHEMA_NAMES, schema_json

HERE = Path(__file__).parent

INTRO = {
    "entries": """\
File `entries.parquet` in a log-set directory: one row per log entry, deduplicated
on `id`. Parquet, zstd compression, row groups of about 64k rows, rows in ingest order
(sources oldest-first, then file order).

- `level` is the numeric `LogLevel`; filter with `level >= 40` for WARN and above
  (`UNKNOWN` = 0 marks unparsed lines, see `parse_error`).
- Dictionary-encoded columns (`level_name`, `logger`, `source_path`) read as
  categoricals in polars and as `VARCHAR` in DuckDB.
- `context` holds header fields without a dedicated column; the well-known keys
  `user, request_id, session, ip, url, request_url` have their own columns.
- `exception_chain` element: `{type, message, frames: [{location, file, line, module}],
  omitted_frames}`; outermost exception first, then each `Caused by:`.
- `signature` joins to `groups.signature`.""",
    "groups": """\
File `groups.parquet`: one row per primary signature, sorted by `count` descending.
Computed at ingest (or by `regroup`) from the same entries as `entries.parquet`;
`sum(count)` equals the number of entries.

`top_users`, `top_threads` and `sources` are lists of `{value, count}` sorted by count
descending; `examples` are entry ids (join to `entries.id`).

`LogSet.groups(coarse=True)` aggregates these rows by `coarse_signature`; it returns the
same columns (with `signature` = `coarse_signature`) plus `signatures` (member list), and
`user_count`/`thread_count` become lower bounds (maximum over members).""",
    "merges": """\
Optional file `merges.parquet`, written by the `mine` step: Drain3 proposals to treat
several signatures as one. It never changes `groups.parquet`; apply it as a view.
Dropped by `regroup` because it refers to the old signatures.""",
    "logset": """\
File `manifest.json` in `<store>/<id>/`, UTF-8 JSON. Timestamps are ISO 8601 with offset.
The id is `xxh3_64` (hex) of the source files' xxh3 digests, sorted and joined with
`\\n`, so ingesting the same files again yields the same id (idempotent).

Readers refuse a manifest whose `schema` (`logset.vN`) or `schema_version` major is
newer than they support. Directories in the store whose names start with `.` are
in-progress writes and are ignored.""",
}


def render(name: str) -> str:
    """Markdown for one schema."""
    doc = schema_json(name)
    lines = [
        f"# {doc['schema']}",
        "",
        f"Schema version {doc['version']}. Generated from `atlas_log_analyzer.store.schema` "
        "by `docs/formats/generate.py`; machine-readable form: "
        f"`atlas-log schema {name}`.",
        "",
        INTRO[name],
        "",
        "| Column | Type | Nullable | Description |"
        if name != "logset"
        else "| Field | Type | Nullable | Description |",
        "|---|---|---|---|",
    ]
    fields = doc["fields"]
    assert isinstance(fields, list)
    for f in fields:
        typ = str(f["type"]).replace("|", "\\|")
        lines.append(
            f"| `{f['name']}` | `{typ}` | {'yes' if f['nullable'] else 'no'} | {f['description']} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    """Write all ``<name>.v1.md`` files."""
    for name in SCHEMA_NAMES:
        path = HERE / f"{name}.v1.md"
        path.write_text(render(name), encoding="utf-8", newline="\n")
        print(path)


if __name__ == "__main__":
    main()
