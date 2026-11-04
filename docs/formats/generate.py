"""Regenerate the format docs from ``atlas_log_analyzer.store.schema``.

Writes ``docs/formats/<name>.v1.md`` and the generated column blocks of
``docs/cli.md`` (between ``<!-- generated:NAME ... -->`` and ``<!-- /generated -->``).

Run: ``uv run python docs/formats/generate.py``
"""

from __future__ import annotations

import re
import textwrap
from pathlib import Path

from atlas_log_analyzer.store.schema import SCHEMA_NAMES, schema_json

HERE = Path(__file__).parent
CLI_MD = HERE.parent / "cli.md"
BLOCK_RE = re.compile(
    r"(?P<open><!-- generated:(?P<name>[\w-]+)[^>]*-->\n)"
    r"(?P<body>.*?)"
    r"(?P<close><!-- /generated -->)",
    re.S,
)

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


def columns(name: str) -> list[str]:
    """Column names of a stored table, in schema order."""
    fields = schema_json(name)["fields"]
    assert isinstance(fields, list)
    return [str(f["name"]) for f in fields]


def _wrap(text: str, indent: str = "") -> str:
    return textwrap.fill(text, width=80, subsequent_indent=indent) + "\n"


def cli_blocks() -> dict[str, str]:
    """Bodies of the generated blocks of ``docs/cli.md``, by block name."""

    def listing(name: str) -> str:
        return ", ".join(f"`{c}`" for c in columns(name))

    views = "".join(_wrap(f"- `{v}`: {listing(v)}", "  ") for v in ("entries", "groups", "merges"))
    return {
        "entries-columns": _wrap(f"entries.v1 columns: {listing('entries')}."),
        "view-columns": "Columns of the views:\n\n" + views,
    }


def render_cli_md(text: str) -> str:
    """``text`` (the content of ``docs/cli.md``) with its generated blocks rewritten."""
    blocks = cli_blocks()

    def fill(m: re.Match[str]) -> str:
        body = blocks.get(m["name"])
        if body is None:
            raise KeyError(f"unknown generated block {m['name']!r} in {CLI_MD}")
        return m["open"] + body + m["close"]

    return BLOCK_RE.sub(fill, text)


def main() -> None:
    """Write all ``<name>.v1.md`` files and the generated blocks of ``docs/cli.md``."""
    for name in SCHEMA_NAMES:
        path = HERE / f"{name}.v1.md"
        path.write_text(render(name), encoding="utf-8", newline="\n")
        print(path)
    text = CLI_MD.read_text(encoding="utf-8")
    CLI_MD.write_text(render_cli_md(text), encoding="utf-8", newline="\n")
    print(CLI_MD)


if __name__ == "__main__":
    main()
