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
| `--store DIR` | env `ATLAS_LOG_STORE`, else `./.atlas-log` | Store directory holding log sets. The implicit default is not *created* inside a git work tree (a `.git` entry in the current directory or a parent): `ingest` and `serve` then fail with `store_error` (exit 3), because a store in a repository risks committing real logs. An existing `./.atlas-log` is used as before; an explicit `--store`/`ATLAS_LOG_STORE` is always used. |
| `--quiet` | off | Suppress progress and diagnostics on stderr (warnings and errors still shown). |
| `--width N` | terminal width; on a pipe `$COLUMNS`, else 160 | Table width (minimum 40). |
| `--color auto\|always\|never` | `auto` | Colour tables and `--raw` entries; `auto` = stdout is a terminal and `NO_COLOR` is not set. |
| `--help`, `-h` | | Help for the app or a command. |
| `--version` | | Print the tool version. |

### Output modes

- **json** — exactly one JSON document on stdout. Every document is an object
  with a `"schema": "<name>.v1"` key. Listings put their rows under `"items"`
  and add `"count"`. Compact on a pipe, indented on a terminal.
  Paged listings (`groups`, `entries`) also carry `"total"` (rows matching
  the filters before `--offset`/`--limit`), `"offset"` (the `--offset` used)
  and `"truncated"` (more rows follow this page: `offset + count < total`; the
  next page starts at `offset + count`); `count` is always `len(items)`. `query` has `"total" == "count"` and
  `"truncated": false` (use `LIMIT` in the SQL); `diff` has
  `"totals": {"new", "vanished", "changed"}` and one `"truncated"` flag.
- **jsonl** — listings: one row per line, without the envelope (`diff`: one row
  per line with an extra `"change"` key). Single documents (`stats`, `ingest`,
  …): the document on one line.
- **table** — rich tables on stdout; long cells are truncated with `…`,
  timestamps shown to the second (`Z` = UTC). Ids, levels, counts and
  timestamps never wrap; numbers are right-aligned; `level_name` is coloured
  (ERROR red, WARN yellow) when colour is on. No ANSI codes unless colour is on
  (see `--color`). Scalar metadata of listings (`logset=… count=… total=…
  truncated=…`) goes to stderr. `show --raw` / `entries --raw` with colour on
  highlight the header (timestamp, level, logger), exception class names,
  `Caused by:`, stack frames (application frames in bold, see
  [Application frames](#application-frames)) and separate entries with a
  `file:line` rule.

### Application frames

`frames` and the highlighting of `--raw` output separate application code from
library/platform code. A frame is a *library* frame when its class starts with
one of the prefixes in `core/java.py` `LIBRARY_PACKAGES`: the JDK and JVM
languages (`java.`, `javax.`, `jakarta.`, `jdk.`, `sun.`, `com.sun.`, `kotlin.`,
`scala.`, `groovy.`, `org.codehaus.groovy.`), the Jira platform and its
libraries (`com.atlassian.`, `io.atlassian.`, `org.ofbiz.`, `webwork.`,
`net.java.ao.`, `org.hibernate.`, `com.google.`, `org.osgi.`,
`org.springframework.`, `org.apache.`, `org.eclipse.`, `org.glassfish.`,
`io.netty.`, `com.zaxxer.`) and JDBC drivers (`org.postgresql.`, `oracle.`,
`com.mysql.`, `com.microsoft.sqlserver.`); every other frame — plugin code,
Groovy scripts — is an *application* frame. Set the environment variable
`ATLAS_LOG_APP_PACKAGES` to comma-separated prefixes
(`ATLAS_LOG_APP_PACKAGES=com.acme.,org.example.`) to invert this: a frame is
then an application frame only when it starts with one of them.

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
| 3 | Store error: unknown log set, ambiguous reference, corrupt manifest, unsupported schema version, default store refused inside a git work tree. |

In `json`/`jsonl` mode errors are written to **stdout** as

```json
{"schema": "error.v1", "error": {"code": "store_error", "message": "no log set 'x'",
 "hint": "available sets: prod-1029, prod-1030", "store": ".atlas-log"}}
```

`"hint"` (optional) suggests the next step (available set names for an unknown
set; `atlas-log groups SET` for an unknown signature); other optional keys
carry context such as `"store"`. In `table` mode errors go to stderr as
`error: <message> [<code>]` followed by `hint: …`. Error codes:
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
| `--since T` / `--until T` | ISO 8601 (`2026-10-29T09:00`, naive = UTC), a date (`2026-10-29` = midnight UTC), or a duration `Ns`/`Nm`/`Nh`/`Nd` **before the last entry of the set** (`2h` = the last two hours of the log, not of the wall clock). `since` inclusive, `until` exclusive. For `groups` the filter is on the group's time span: `last_seen >= since` (the group has at least one entry at/after `since`) and `first_seen < until`; the group's `count` still covers the whole set. Anything else → error `invalid_value`. |
| `--with-exception` | `groups`, `entries`: only rows that carry an exception (`exception_type` not null), at any level — a DEBUG entry with a stack trace is found although `--min-level WARN` hides it. |
| `--exception TEXT` | `groups`, `entries`: case-insensitive substring of `exception_type` (the outermost exception of the chain; `Caused by:` types are not searched). |
| `--contains TEXT` | `entries`: case-insensitive substring of `raw`. `groups`: case-insensitive substring of the masked `template`. |
| `--limit N` | Maximum rows; `0` = all. |
| `--offset N` | `groups`, `entries`: skip N rows first (paging; default 0). |

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

### `groups SET [--level] [--min-level] [--logger] [--since] [--until] [--with-exception] [--exception T] [--contains T] [--sort K] [--limit 50] [--offset 0] [--coarse] [--columns C[,C…]]... [--brief]`

One row per signature. `--sort`: `count` (default, desc), `last_seen` (desc),
`first_seen` (asc), `signature`, `template`. `--coarse` groups by coarse
signature (exception chain ignored); `signature` then holds the coarse
signature and `signatures` lists the members.

`--columns` keeps only the named groups.v1 columns (comma-separated and/or
repeated, as for `entries`; `signatures` is valid with `--coarse`). `--brief`
is the preset `signature, template, level_name, logger, exception_type, count,
first_seen, last_seen`. `--brief` together with `--columns` is a usage error.
With `--coarse` the filters apply to the member groups before aggregation.

```json
{"schema": "groups.v1", "logset": "…", "count": 2, "total": 7, "offset": 0, "truncated": true, "items": [
  {"signature", "coarse_signature", "template", "level", "level_name", "logger",
   "exception_type", "count", "first_seen", "last_seen", "user_count", "thread_count",
   "source_count", "top_users", "top_threads", "sources", "examples"}]}
```

Exit: 0 (also empty); 1 bad option, unknown column, `--brief` with `--columns`; 3.

### `show SET SIGNATURE [--limit 20] [--raw] [--max-raw N]`

Entries of one group, ordered by time, with `variables` (the values masking
replaced, in order). `--raw` prints the original log text of each entry,
separated by blank lines, in any output mode (highlighted, with `file:line`
rules, when colour is on). The table starts with a three-line group header:
template; logger, level and exception; count, first and last seen.

`--max-raw N` keeps at most N characters of `raw` and `body` per entry
(`0` = no limit). Default: **4000 in `json`/`jsonl`**, no limit in `table` and
`--raw` mode. A cut text ends with `… [truncated: N chars total, use --max-raw 0]`
and the row gets `"body_truncated": true` and `"raw_chars": <original length
of raw>` (of `body` when `raw` is not among the columns). Rows that were not
cut have neither key, so short entries look exactly as before.

```json
{"schema": "entries.v1", "logset": "…", "signature": "…", "group": {<groups.v1 row>} | null,
 "count": 3, "items": [<entries.v1 row>]}
```

Exit: 0; 1 bad option; 2 no entries with this signature; 3.

### `entries SET [--signature] [--level] [--min-level] [--logger] [--user] [--thread] [--request-id] [--since] [--until] [--contains] [--with-exception] [--exception T] [--columns C[,C…]]... [--brief] [--limit 100] [--offset 0] [--raw] [--max-raw N]`

`--user`/`--thread`/`--request-id` are exact (`--request-id` is the Jira
request id of the request context, e.g. `540x101x1`: all entries of one HTTP
request); `--contains` is a case-insensitive substring of `raw`; `--columns` selects columns, comma-separated and/or repeated
(`--columns ts,level_name --columns message`; order kept, duplicates dropped).
An unknown column is error `invalid_value` naming it, with the valid columns
in `hint`. `--brief` is the preset `id, ts, level_name, thread, logger,
message, exception_type, signature` (not combinable with `--columns`).
`--raw` prints the raw text like `show --raw`. `--max-raw` cuts
`raw`/`body` as described for `show` (default 4000 in `json`/`jsonl`).

```json
{"schema": "entries.v1", "logset": "…", "count": 10, "total": 42, "offset": 0,
 "truncated": true, "items": [<entries.v1 row>]}
```

entries.v1 columns: `id, ts, tz_offset, level, level_name, thread, logger,
message, body, user, request_id, session, ip, url, request_url, context,
exception_type, exception_message, exception_chain, signature,
coarse_signature, template, variables, source_path, source_line,
source_offset, raw, parse_error`. Exit: 0 (also empty); 1 bad option, unknown
column, `--brief` with `--columns`; 3.

### `context SET ENTRY_ID [--before 5] [--after 5] [--any-thread] [--max-raw N]`

The entries immediately around one entry in time order (ties in file order),
restricted to the anchor's thread unless `--any-thread`; the anchor itself is
included. Entry ids are the `id` column of `entries`/`show` or a group's
`examples`. `--max-raw` as for `show`.

```json
{"schema": "entries.v1", "logset": "…", "anchor": "<entry id>", "thread": "http-nio-8080-exec-1",
 "any_thread": false, "count": 11, "items": [<entries.v1 row>]}
```

At most `--before + 1 + --after` items. `jsonl`: one row per line, the anchor
row with an extra `"anchor": true`. The table marks the anchor row with `▶`.
Exit: 0; 1 bad option; 2 no entry with this id (`not_found`, with a hint); 3.

### `frames SET REF [--limit 10] [--all]`

The exception chain of one entry with its application frames (see
[Application frames](#application-frames)). `REF` is an entry id or a group
signature (16 hex chars); entry ids are tried first, and for a signature the
group's first example entry is used.

```json
{"schema": "frames.v1", "logset": "…", "entry": "<entry id>", "signature": "…",
 "ts": "…", "level_name": "DEBUG", "logger": "c.q.r.stats.type.StatTypeHelper",
 "message": "…", "app_packages": null, "count": 2,
 "items": [{"type": "com.qoti.rf.exceptions.InternalException", "message": "…",
            "app_frames": [{"location": "com.qoti.rf.stats.type.StatTypeHelper.resolve",
                            "file": "StatTypeHelper.java", "line": 88}],
            "app_frames_total": 2, "frames_total": 6, "omitted_frames": 0}]}
```

`items` is the chain, outermost exception first, then each `Caused by:`
(empty, exit 0, when the entry has no parsed exception). `app_frames` holds the
first `--limit` application frames (`0` = all) and `app_frames_total` counts
them; `frames_total` is the number of frames in the log text and
`omitted_frames` the `... N more` count. `--all` returns all application frames
and adds `frames`: every frame with an `"app": true|false` flag.
`app_packages` echoes `ATLAS_LOG_APP_PACKAGES` (`null` when unset). `jsonl`:
one exception per line. The table prints each exception with its frames as
text. Only the first exception chain of an entry is parsed (`design.md` §11),
and the chain ends at the first line that is not a frame.

Exit: 0; 1 bad option; 2 no entry or group `REF` (`not_found`, with a hint); 3.

### `query SET SQL`

DuckDB SQL over the views `entries`, `groups` and (when present) `merges`.

```json
{"schema": "query.v1", "logset": "…", "columns": ["level_name", "n"], "count": 4, "total": 4, "truncated": false, "items": [{"level_name": "WARN", "n": 18}]}
```

Exit: 0 (also empty); 1 SQL error; 3.

### `stats SET`

```json
{"schema": "stats.v1", "logset": "…", "id", "name", "format", "entry_count", "group_count",
 "parse_errors", "levels": {"ERROR": 13}, "loggers": [{"logger", "count"}],
 "hours": [{"hour", "count"}], "time_range": {"first", "last"}, "sources": [...]}
```

The table shows `hours` compactly: one row per day with the day total, the
busiest hour and a 24-character sparkline (`▁…█`) of the hourly counts.

Exit: 0; 3.

### `diff SET_A SET_B [--min-level L | --level L] [--coarse] [--force] [--limit 0] [--rate]`

Compares stored group tables: `new` (only in B), `vanished` (only in A),
`changed` (both, different count; `ratio = count_b / count_a`).

```json
{"schema": "diff.v1", "a": "<id>", "b": "<id>", "new": [row], "vanished": [row],
 "changed": [row], "unchanged_count": 1,
 "totals": {"new": 3, "vanished": 0, "changed": 5}, "truncated": false,
 "warnings": ["Set B (night) covers 30 min but set A (day) covers 6.9 h (13.7x shorter): …"]}
```

The table prints `vanished: none` (etc.) instead of an empty table.

Rows: `signature, template, level, level_name, logger, exception_type, count_a,
count_b, first_seen_a, last_seen_a, first_seen_b, last_seen_b` (+ `ratio` in
`changed`).

`--rate` adds `rate_a`, `rate_b` (count per hour of each set's span, first to
last entry) and `rate_ratio = rate_b / rate_a` to the `changed` rows and sorts
`changed` by `rate_ratio` descending — use it when the sets cover different
lengths of time (`ratio` compares raw counts).

`warnings` is present (independently of `--rate`) only when the spans of A and
B differ by more than 4x either way: one sentence saying that new/vanished
groups are unreliable, because groups that occur only at certain times of day
(daytime traffic, nightly jobs) look new or vanished. In `table` and `jsonl`
mode the sentence is printed on stderr (`warning: …`) instead. Exit: 0; 1 sets signed differently (signature/mask-rule version or
spec) without `--force`; 3.

### `triage SET [--baseline SET|auto] [--min-level WARN] [--grow 2.0] [--limit 50]`

"Are there new kinds of errors since the baseline?" in one document — the same
`triage.v1` as `GET /api/sets/SET/triage` of the web app, without its
`report_url` links. It compares the baseline with SET like
`diff BASELINE SET --min-level L` and sorts the result for a decision.
`--baseline auto` (default) picks the newest other set that ends before SET
starts, else the newest other set that ends no later than SET; without one the
verdict is `no_baseline`.

```jsonc
{"schema": "triage.v1",
 "set": {"id", "name", "first_ts", "last_ts", "entries"},
 "baseline": {"id", "name", "first_ts", "last_ts", "resolved_by": "auto|param"} | null,
 "min_level": "WARN",
 "verdict": "new_errors | new_warnings | new_exceptions | clean | no_baseline",
 "summary": {"new", "new_by_level": {"ERROR": n}, "new_exceptions", "grew", "vanished",
             "changed", "unchanged"} | null,
 "coverage": {"hours_baseline": 6.88, "hours_set": 0.51, "ratio": 0.074},
 "warnings": ["The set covers 31 min but the baseline covers 6.9 h (13.5x shorter): …"],
 "new":            [<diff row> + rate_per_hour],   // ERROR first, then by count
 "new_exceptions": [<diff row> + rate_per_hour],   // any level, not in "new"
 "grew":           [<diff row> + ratio + rate_ratio],
 "vanished":       [<diff row>]}
```

- `new`: groups at or above `--min-level` that exist only in SET, most severe
  first, then by count.
- `new_exceptions`: new groups of **any** level whose `exception_type` is set
  and that are not already in `new` (a new DEBUG stack trace), same order.
  Empty when `--min-level` is the lowest level that matters (everything new is
  then in `new`).
- `grew`: groups in both sets whose hourly rate rose at least `--grow` times
  (`rate_ratio`; rates normalise sets of different length).
- `verdict`: `new_errors` (a new ERROR/FATAL group), `new_warnings` (other new
  groups), `new_exceptions` (`new` is empty but `new_exceptions` is not),
  `clean`, `no_baseline`.
- `coverage`: span of each set in hours and `ratio = hours_set /
  hours_baseline`. When the spans differ by more than 4x either way,
  `warnings` holds a sentence saying that `new`/`vanished` are unreliable (a
  nightly set lacks daytime groups); otherwise `warnings` is `[]`.
- Lists hold at most `--limit` rows (`0` = all); `summary` counts all.
- `jsonl` prints the document on one line; the table prints the verdict, the
  summary and one table per list, with the warnings on stderr.

Exit: 0 whatever the verdict (also `no_baseline`); 1 bad option, invalid level,
`--grow` not > 1, or the sets were signed differently; 3 unknown set.

### `report SET [--out FILE] [--compare SET_B] [--min-level L] [--examples 3] [--max-raw 4000] [--max-groups 0] [--open]`

Writes a single self-contained HTML file (CSS, JS and data inlined; no network
requests, works from `file://`, light and dark theme): overview tiles, a
per-hour timeline by level (drag to filter groups to a time window), the
filterable group table (search incl. `sig:` prefix, level chips, logger
prefix tree, exception type, coarse / Drain3-merged views) and a detail drawer
per group with example entries and the equivalent `show`/`entries` commands.
The view state lives in the URL hash. The embedded data is the `report.v1`
payload ([`formats/report.v1.md`](formats/report.v1.md)); design in
[`report.md`](report.md).

`--out` defaults to `<name or id>.html` in the current directory.
`--compare SET_B` adds the comparison of SET against the baseline SET_B (as
`diff SET_B SET`; no `--force`). `--min-level` restricts the groups (and the
comparison); the overview always covers the whole set. `--examples` example
entries per group (0 = none), `--max-raw` characters of raw text per example
(0 = no limit), `--max-groups` only the N largest groups (0 = all). `--open`
opens the file in the default browser.

```json
{"schema": "report-file.v1", "path": "C:/…/prod.html", "bytes": 9913581, "groups": 2953, "entries": 5529, "compare": true}
```

`groups`: groups in the report; `entries`: embedded example entries. Table mode
prints one line (`wrote <path> (…)`). Exit: 0; 1 bad option, invalid level, or
the sets were signed differently; 2 output directory does not exist; 3.

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

### `serve [--host 127.0.0.1] [--port 8321] [--token T] [--max-upload-mb 2048] [--reload]`

Runs the web app over the store until interrupted (needs the `web` extra). Pages:
`/` (sets with triage verdicts and upload), `/sets/SET` (report, `?compare=auto|SET`),
`/sets/SET/triage`; JSON API under `/api` with the same documents as this CLI and
OpenAPI at `/docs`. `--token` (env `ATLAS_LOG_TOKEN`) requires
`Authorization: Bearer T` on `/api`. Nothing is printed to stdout; the server
logs to stderr. See `docs/webapp.md`.

Exit: 0 stopped; 1 the `web` extra is missing (`missing_dependency`) or bad option; 3.

### `version`

```json
{"schema": "version.v1", "version": "0.1.0", "schema_version": "1.0", "signature_version": 1, "mask_rules_version": 1, "python": "3.14.2"}
```

## Recipes

```sh
# Upgrade comparison: what is new or louder after the upgrade?
atlas-log ingest logs-before/ --name before
atlas-log ingest logs-after/ --name after
atlas-log diff before after --min-level WARN --limit 20 --rate   # rates if spans differ

# Who triggers a group? (users and request URLs of one signature)
atlas-log query after "select user, count(*) n from entries
  where signature = '0123456789abcdef' group by 1 order by 2 desc"

# Hourly error rate
atlas-log query after "select date_trunc('hour', ts) h,
  count(*) filter (where level >= 50) errors, count(*) total,
  round(100.0 * errors / total, 1) pct from entries group by 1 order by 1"

# Exceptions at any level (DEBUG stack traces included), then one exception type
atlas-log groups after --with-exception --brief
atlas-log entries after --exception InternalException --brief

# What broke since the previous snapshot? (verdict, new types, growth, coverage)
atlas-log triage after --baseline before

# The last 2 hours of the log (relative to its last entry)
atlas-log groups after --since 2h --min-level ERROR
atlas-log entries after --since 2h --level ERROR --raw
```
