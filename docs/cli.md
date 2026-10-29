# `atlas-log` command reference

`atlas-log --help` and `atlas-log COMMAND --help` print the same information;
this page is the long form. Column-level schemas of the stored tables are
printed by `atlas-log schema` (see also `design.md` §7).

## Conventions

### Global options

Global options may appear before or after the command name.

| Option | Default | Meaning |
|---|---|---|
| `--output table\|json\|jsonl` | `table` if stdout is a terminal, else `json` | Output mode. |
| `--store DIR` | env `ATLAS_LOG_STORE`, else `./.atlas-log` | Store directory holding log sets. |
| `--quiet` | off | Suppress progress and diagnostics on stderr (warnings and errors still shown). |
| `--help`, `-h` | | Help for the app or a command. |
| `--version` | | Print the tool version. |

### Output modes

- **json** — exactly one JSON document on stdout. Every document is an object
  with a `"schema": "<name>.v1"` key. Listings put their rows under `"items"`
  and add `"count"`. Compact on a pipe, indented on a terminal.
- **jsonl** — listings: one row per line, without the envelope (`diff`: one row
  per line with an extra `"change"` key). Single documents (`stats`, `ingest`,
  …): the document on one line.
- **table** — rich tables on stdout; long cells are truncated with `…`,
  timestamps shown to the second (`Z` = UTC). No ANSI codes unless stdout is a
  terminal. Scalar metadata of listings (`logset=… count=…`) goes to stderr.

Values: timestamps are ISO 8601 strings; stored timestamps are UTC
(`…+00:00`), `parse` keeps the original offset. `level` is numeric in stored
tables (TRACE=10 DEBUG=20 INFO=30 WARN=40 ERROR=50 FATAL=60 UNKNOWN=0) with the
name in `level_name`; `parse` emits the name in `level`.

### Errors and exit codes

| Code | Meaning |
|---|---|
| 0 | OK (an empty listing is OK). |
| 1 | Usage error: bad option/argument, unknown format, invalid level, SQL error, incompatible sets in `diff` without `--force`, missing optional dependency. |
| 2 | Nothing found / parse failure: no input files, format not detected, no entries parsed, no entries for a signature, file not found. |
| 3 | Store error: unknown log set, ambiguous reference, corrupt manifest, unsupported schema version. |

In `json`/`jsonl` mode errors are written to **stdout** as

```json
{"schema": "error.v1", "error": {"code": "store_error", "message": "no log set 'x' in .atlas-log"}}
```

In `table` mode they go to stderr as `error: <message> [<code>]`. Error codes:
`usage`, `invalid_value`, `invalid_level`, `unknown_format`, `sql_error`,
`missing_dependency`, `file_not_found`, `no_sources`, `format_not_detected`,
`no_entries`, `not_found`, `store_error`, `internal_error` (unexpected
exception; set `ATLAS_LOG_DEBUG=1` for the traceback).

Diagnostics and progress always go to stderr.

### Log-set references

Wherever a command takes `SET`, it accepts the log-set id, its `--name`, or a
unique id prefix (resolved in that order).

### Filters

| Option | Semantics |
|---|---|
| `--level L[,L…]` | Exact level(s), case-insensitive: `ERROR`, `WARN,ERROR`. |
| `--min-level L` | This level or more severe. |
| `--logger X` | Logger name or package prefix: `c.a.jira` matches `c.a.jira.Foo`. |
| `--since T` / `--until T` | ISO 8601; naive times are UTC. `since` inclusive, `until` exclusive. For groups: `last_seen >= since`, `first_seen < until`. |
| `--limit N` | Maximum rows; `0` = all. |

## Commands

### `ingest PATH... [--format F] [--name N] [--frames N] [--overwrite] [--mine]`

Parse files/directories (directories: non-recursive `*.log*`; rotated `x.log.N`
read oldest first) into a log set. The id is derived from the file digests:
re-ingesting the same files prints the existing manifest
(`ingest.existing: true`) unless `--overwrite`. Format detection sniffs the
first 200 lines of the first file. `--frames N` adds the top N stack frames to
signatures. `--mine` runs Drain3 over the group templates (needs the `mine`
extra) and stores proposals in `merges.parquet`.

```json
{"schema": "logset.v1", "id": "99ee29398877dcd3", "name": "prod", "format": "jira",
 "sources": [{"path", "size", "xxh3", "first_ts", "last_ts", "entries"}],
 "first_ts", "last_ts", "entry_count", "group_count", "created_at", "tool_version",
 "schema_version", "signature_version", "mask_rules_version", "signature_spec",
 "ingest": {"parsed_entries", "parse_errors", "elapsed_s", "existing"},
 "merges": [{"cluster_id", "signatures": [], "drain_template"}]}
```

`merges` only with `--mine`. Exit: 0; 1 unknown format / missing drain3; 2 no
files, format not detected, or zero entries (manifest still printed); 3.

### `sets`

```json
{"schema": "sets.v1", "store": "…", "count": 1, "items": [<manifest without "schema">]}
```

Newest first. Exit: 0 (also empty); 3.

### `groups SET [--level] [--min-level] [--logger] [--since] [--until] [--sort K] [--limit 50] [--coarse]`

One row per signature. `--sort`: `count` (default, desc), `last_seen` (desc),
`first_seen` (asc), `signature`, `template`. `--coarse` groups by coarse
signature (exception chain ignored); `signature` then holds the coarse
signature and `signatures` lists the members.

```json
{"schema": "groups.v1", "logset": "…", "count": 2, "items": [
  {"signature", "coarse_signature", "template", "level", "level_name", "logger",
   "exception_type", "count", "first_seen", "last_seen", "user_count", "thread_count",
   "source_count", "top_users", "top_threads", "sources", "examples"}]}
```

Exit: 0 (also empty); 1; 3.

### `show SET SIGNATURE [--limit 20] [--raw]`

Entries of one group, ordered by time, with `variables` (the values masking
replaced, in order). `--raw` prints the original log text of each entry,
separated by blank lines, in any output mode.

```json
{"schema": "entries.v1", "logset": "…", "signature": "…", "group": {<groups.v1 row>} | null,
 "count": 3, "items": [<entries.v1 row>]}
```

Exit: 0; 2 no entries with this signature; 3.

### `entries SET [--signature] [--level] [--min-level] [--logger] [--user] [--thread] [--since] [--until] [--contains] [--columns C]... [--limit 100]`

`--user`/`--thread` are exact; `--contains` is a case-insensitive substring of
`raw`; `--columns` (repeatable) selects columns.

```json
{"schema": "entries.v1", "logset": "…", "count": 10, "items": [<entries.v1 row>]}
```

entries.v1 columns: `id, ts, tz_offset, level, level_name, thread, logger,
message, body, user, request_id, session, ip, url, request_url, context,
exception_type, exception_message, exception_chain, signature,
coarse_signature, template, variables, source_path, source_line,
source_offset, raw, parse_error`. Exit: 0 (also empty); 1; 3.

### `query SET SQL`

DuckDB SQL over the views `entries`, `groups` and (when present) `merges`.

```json
{"schema": "query.v1", "logset": "…", "columns": ["level_name", "n"], "count": 4, "items": [{"level_name": "WARN", "n": 18}]}
```

Exit: 0 (also empty); 1 SQL error; 3.

### `stats SET`

```json
{"schema": "stats.v1", "logset": "…", "id", "name", "format", "entry_count", "group_count",
 "parse_errors", "levels": {"ERROR": 13}, "loggers": [{"logger", "count"}],
 "hours": [{"hour", "count"}], "time_range": {"first", "last"}, "sources": [...]}
```

Exit: 0; 3.

### `diff SET_A SET_B [--min-level L | --level L] [--coarse] [--force] [--limit 0]`

Compares stored group tables: `new` (only in B), `vanished` (only in A),
`changed` (both, different count; `ratio = count_b / count_a`).

```json
{"schema": "diff.v1", "a": "<id>", "b": "<id>", "new": [row], "vanished": [row],
 "changed": [row], "unchanged_count": 1}
```

Rows: `signature, template, level, level_name, logger, exception_type, count_a,
count_b, first_seen_a, last_seen_a, first_seen_b, last_seen_b` (+ `ratio` in
`changed`). Exit: 0; 1 sets signed differently (signature/mask-rule version or
spec) without `--force`; 3.

### `parse PATH... [--format F] [--limit 0]`

Streams entries as JSON Lines to stdout without a store; `--output` is ignored.

```json
{"id", "format", "timestamp": "2026-10-29T02:19:41.415+01:00", "level": "ERROR", "thread",
 "logger", "message", "body", "exception_type",
 "exceptions": [{"type", "message", "frames": [{"location", "file", "line", "module"}], "omitted_frames"}],
 "context": {"user", "request_id", "session", "ip", "url", "request_url"},
 "source": {"path", "line", "offset"}, "raw", "parse_error"}
```

Exit: 0; 1 unknown format; 2 no files / not detected / no entries.

### `regroup SET [--frames N] [--no-exceptions]`

Re-signs the stored entries with another signature spec and rewrites the
groups (any `merges.parquet` is dropped). Prints the manifest (`logset.v1`).
Diffs against sets with a different spec then need `--force`. Exit: 0; 1; 3.

### `formats [--sample FILE]`

```json
{"schema": "formats.v1", "count": 1, "items": [{"name": "jira", "description", "signature_spec", "mask_rules": [], "confidence"}]}
```

`confidence` (0–1) only with `--sample`; items are then sorted by it. Exit: 0; 2.

### `schema [entries|groups|merges|logset]`

```json
{"schema": "schema.v1", "definitions": {"groups": {"schema": "groups.v1", "version": "1.0", "fields": [{"name", "type", "nullable", "description"}]}}}
```

All four without an argument. Table mode prints the same JSON indented. Exit: 0; 1.

### `remove SET`

```json
{"schema": "remove.v1", "removed": "<id>", "name": "prod"}
```

Exit: 0; 3.

### `version`

```json
{"schema": "version.v1", "version": "0.1.0", "schema_version": "1.0", "signature_version": 1, "mask_rules_version": 1, "python": "3.14.2"}
```
