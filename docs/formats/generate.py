"""Regenerate the format docs from ``store.schema`` and ``service.documents``.

Writes, for every JSON document (``service.documents.DOCUMENTS``),
``docs/formats/<name>.v1.schema.json`` and ``docs/formats/<name>.v1.md``
(the stored tables ``entries``, ``groups``, ``merges``, ``logset`` keep their
column docs and gain a JSON section; ``report.v1.md`` is hand-written), and the
generated column blocks of ``docs/cli.md`` (between
``<!-- generated:NAME ... -->`` and ``<!-- /generated -->``).

Run: ``uv run python docs/formats/generate.py``
"""

from __future__ import annotations

import json
import re
import textwrap
from pathlib import Path
from typing import Any

from atlas_log_analyzer.service.documents import (
    DOCUMENTS,
    ROW_COMPONENTS,
    document_names,
    document_schema,
)
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


HAND_WRITTEN = frozenset({"report.v1"})
"""Documents whose ``.md`` is written by hand (only their ``.schema.json`` is generated)."""

TABLE_ROWS = {"entries": "EntryRow", "groups": "GroupRow", "merges": "MergeRow"}
"""Row component of each stored table: its columns are listed by the table doc already."""


def render(name: str) -> str:
    """Markdown for one stored table, plus its JSON document or JSON row form."""
    text = render_table(name)
    if f"{name}.v1" in DOCUMENTS:
        return text + "\n" + render_document(f"{name}.v1", table=name)
    return text + (
        f"\nIn JSON (`logset.v1` `merges` after `ingest --mine`, `query` over the `{name}` "
        f"view) a row is an object with these columns: `{TABLE_ROWS[name]}` in the "
        "documents' JSON Schemas (`atlas-log schema logset.v1`).\n"
    )


def render_table(name: str) -> str:
    """Markdown for one stored table schema."""
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


def _cell(text: str) -> str:
    return " ".join(text.split()).replace("|", "\\|")


def _ref_name(ref: str) -> str:
    return ref.rsplit("/", 1)[-1]


def type_label(schema: dict[str, Any]) -> str:
    """Compact type of a JSON Schema: ``string``, ``list<DiffRow>``, ``"a" | "b"``, ..."""
    if "$ref" in schema:
        return _ref_name(schema["$ref"])
    if "anyOf" in schema:
        return " | ".join(type_label(s) for s in schema["anyOf"])
    if "enum" in schema:
        return " | ".join(json.dumps(v) for v in schema["enum"])
    if "const" in schema:
        return json.dumps(schema["const"])
    kind = schema.get("type")
    if kind == "array":
        if "prefixItems" in schema:
            return "[" + ", ".join(type_label(s) for s in schema["prefixItems"]) + "]"
        return f"list<{type_label(schema.get('items', {}))}>"
    if kind == "object" and isinstance(schema.get("additionalProperties"), dict):
        return f"map<string, {type_label(schema['additionalProperties'])}>"
    if kind == "string" and schema.get("format") == "date-time":
        return "string (date-time)"
    return str(kind) if kind else "any"


def _part(schema: dict[str, Any], defs: dict[str, Any]) -> tuple[str | None, str]:
    """Struct part a property holds (directly, nullable or as list items) and its suffix."""
    for option in schema.get("anyOf", [schema]):
        node, suffix = option, ""
        if option.get("type") == "array" and "items" in option:
            node, suffix = option["items"], "[]"
        elif isinstance(option.get("additionalProperties"), dict):
            node, suffix = option["additionalProperties"], ".*"
        name = _ref_name(node["$ref"]) if "$ref" in node else None
        if name in defs and name not in ROW_COMPONENTS.values() and name not in DOCUMENTS:
            return name, suffix
    return None, ""


def _fields(schema: dict[str, Any], defs: dict[str, Any], prefix: str = "") -> list[str]:
    """Table rows of the properties of ``schema``, Struct parts expanded as ``a.b``."""
    lines = []
    required = set(schema.get("required", []))
    for key, prop in schema.get("properties", {}).items():
        name = prefix + key
        typ = json.dumps(prop["enum"][0]) if key == "schema" and not prefix else type_label(prop)
        need = "yes" if key in required else "no"
        text = _cell(prop.get("description", ""))
        lines.append(f"| `{name}` | `{_cell(typ)}` | {need} | {text} |")
        part, suffix = _part(prop, defs)
        if part is not None:
            lines.extend(_fields(defs[part], defs, f"{name}{suffix}."))
    return lines


def _row_section(row: str, defs: dict[str, Any], table: str | None) -> list[str]:
    schema = defs[row]
    known = set(columns(table)) if table and TABLE_ROWS.get(table) == row else set()
    lines = ["", f"### `{row}`", ""]
    if known:
        lines.append(
            "Rows hold the columns above (`--columns`/`--brief` select them, so every "
            "column is optional in JSON), plus:"
        )
    else:
        lines.append(f"{schema['description']} Every column is optional.")
    lines += ["", "| Column | Type | Description |", "|---|---|---|"]
    for key, prop in schema["properties"].items():
        if key not in known:
            text = _cell(prop.get("description", ""))
            lines.append(f"| `{key}` | `{_cell(type_label(prop))}` | {text} |")
    return lines


def render_document(name: str, *, table: str | None = None) -> str:
    """Markdown for one JSON document (a section of the table doc when ``table`` is given)."""
    schema = document_schema(name)
    defs = schema.get("$defs", {})
    lines = [
        f"## JSON document `{name}`" if table else f"# {name}",
        "",
        _cell(schema["description"]),
        "",
        "Generated from `atlas_log_analyzer.service.documents` by `docs/formats/generate.py`; "
        f"JSON Schema: [`{name}.schema.json`]({name}.schema.json), also "
        f"`atlas-log schema {name}`. A key with Required = no appears only in some modes "
        "(see its description); readers ignore keys they do not know.",
        "",
        "| Key | Type | Required | Description |",
        "|---|---|---|---|",
        *_fields(schema, defs),
    ]
    for row in sorted(r for r in ROW_COMPONENTS.values() if r in defs):
        lines += _row_section(row, defs, table)
    return "\n".join(lines) + "\n"


def render_schema(name: str) -> str:
    """``<name>.schema.json``: the document's JSON Schema, indented."""
    return json.dumps(document_schema(name), indent=2, ensure_ascii=False) + "\n"


def outputs() -> dict[Path, str]:
    """Every generated file under ``docs/formats`` with its content."""
    files = {HERE / f"{name}.v1.md": render(name) for name in SCHEMA_NAMES}
    for name in document_names():
        files[HERE / f"{name}.schema.json"] = render_schema(name)
        if name.removesuffix(".v1") not in SCHEMA_NAMES and name not in HAND_WRITTEN:
            files[HERE / f"{name}.md"] = render_document(name)
    return files


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
    """Write the files of :func:`outputs` and the generated blocks of ``docs/cli.md``."""
    for path, text in outputs().items():
        path.write_text(text, encoding="utf-8", newline="\n")
        print(path)
    text = CLI_MD.read_text(encoding="utf-8")
    CLI_MD.write_text(render_cli_md(text), encoding="utf-8", newline="\n")
    print(CLI_MD)


if __name__ == "__main__":
    main()
