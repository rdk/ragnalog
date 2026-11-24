# `ragnalog` command reference

`ragnalog --help` and `ragnalog COMMAND --help` print the same information;
this page is the long form. Column-level schemas of the stored tables are
printed by `ragnalog schema`, the JSON Schema of every document by
`ragnalog schema NAME.v1` (see also `design.md` §7 and `docs/formats/`).

Typical flow:

1. `ingest PATH --name N` builds a log set.
2. `triage N` answers "new kinds of errors since the previous set?".
3. `groups N --brief` lists one row per message signature;
   `groups N --with-exception --brief` finds stack traces at any level.
4. `show N SIG` (entries of a group), `frames N SIG` (its stack trace,
   application frames first) and `context N ENTRY` (what the thread did around
   an entry) drill into one group.
5. `diff A B --rate` compares two sets.

`sets --brief` and `set SET` show what is in the data directory. A Jira Data Center
cluster export (one ZIP with `job.json` and `NODE_atlassian-jira.log*`) becomes
one set per node; `captures` lists such sets collected together and `label`
sets a node, cluster or capture by hand (`cluster.md`). `capture`, `matrix`,
`events`, `interactions`, `timeline`, `entries`, `context --all-nodes` and
`report` show the nodes of a capture side by side (recipe "Clusters" at the
end). Agents start with
`ragnalog manifest` (bare `ragnalog` prints the same JSON when stdout is not a
terminal): the workflow, every command with its usage, output document and exit
codes, and where the document schemas are.

## Conventions

### Global options

Global options may appear before or after the command name.

| Option | Default | Meaning |
|---|---|---|
| `--output table\|json\|jsonl` | `table` if stdout is a terminal, else `json` | Output mode. |
| `--store DIR` | env `RAGNALOG_STORE` (a blank `--store` or variable is ignored), else `./.ragnalog` | The data directory, the folder that keeps the log sets (older text calls it the store); the option and variable that name it keep the name `--store` / `RAGNALOG_STORE`. The implicit default is not *created* inside a git work tree (a `.git` entry in the current directory or a parent): `ingest` and `serve` then fail with `store_error` (exit 3), because a data directory in a repository risks committing real logs. An existing `./.ragnalog` is used as before; an explicit `--store`/`RAGNALOG_STORE` is always used. |
| `--server URL` | env `RAGNALOG_SERVER`, else none | Remote mode: commands that read or label sets ask the `/api` of the ragnalog web app at `URL` instead of a data directory, with the token in `RAGNALOG_TOKEN` (see [Remote mode](#remote-mode---server)). |
| `--quiet` | off | Suppress progress and diagnostics on stderr (warnings and errors still shown). |
| `--width N` | terminal width; on a pipe `$COLUMNS`, else 160 | Table width (minimum 40). |
| `--color auto\|always\|never` | `auto` | Colour tables and `--raw` entries; `auto` = stdout is a terminal and `NO_COLOR` is not set. |
| `--help`, `-h` | | Help for the app or a command. |
| `--version` | | Print the tool version. |

### Environment variables

One settings object (`config.py`, `Settings` and `ENV_VARS`) serves the CLI and
the web app: a flag wins over its variable, a variable over the default, and a
blank variable counts as unset. A number that does not parse is error
`invalid_value` (exit 1).

| Variable | Default | Meaning |
|---|---|---|
| `RAGNALOG_STORE` | `./.ragnalog` | Data directory holding the log sets (`--store` wins). |
| `RAGNALOG_SERVER` | none | Base URL of a ragnalog web app (`https://logs.example.org`): commands that read or label sets ask its `/api` instead of a data directory, with `RAGNALOG_TOKEN` (`--server` wins; an explicit `--store` runs locally). |
| `RAGNALOG_APP_PACKAGES` | none | Comma-separated package prefixes of application code for `frames` and the highlighting of raw entries (`com.acme.,org.example.`); default: every frame outside the library packages. |
| `RAGNALOG_DEBUG` | off | Any value: an unexpected exception shows its traceback instead of `internal_error`. |
| `RAGNALOG_TOKEN` | none | `serve`: API token (`--token` wins); with `--server`, the token the CLI sends. |
| `RAGNALOG_ALLOWED_HOSTS` | none | `serve`: comma-separated host names a loopback server also answers (`logs.example.org`, behind a reverse proxy that passes the public `Host` through; `--allowed-host` wins). |
| `RAGNALOG_MAX_UPLOAD_MB` | 2048 | `serve`: largest upload in MiB (`--max-upload-mb` wins). |
| `RAGNALOG_REPORT_CACHE` | 8 | `serve`: report payloads kept in memory. |

### Remote mode (`--server`)

With `--server URL` (or `RAGNALOG_SERVER`) the commands that read or label sets run against
a ragnalog web app instead of a data directory: each sends the `/api` request it is paired
with in the parity contract (`tests/contract/test_parity.py`) and prints the document the
server returns, in the same output modes and with the same exit codes as a local run on
the server's data directory. The token is `RAGNALOG_TOKEN` (sent as `Authorization: Bearer`);
a missing or wrong one is `unauthorized` (exit 1). Nothing else is needed on the machine:
no data directory, no copy of the logs.

```sh
export RAGNALOG_SERVER=https://logs.example.org RAGNALOG_TOKEN=...
ragnalog sets --brief
ragnalog triage prod-1030 --baseline prod-1029
ragnalog query prod-1030 "select level_name, count(*) n from entries group by 1"
printf '%s\n' '["groups","prod-1030","--brief"]' '["stats","prod-1030"]' | ragnalog batch
```

- Remote: `sets`, `set`, `captures`, `capture`, `groups`, `show`, `entries`, `context`,
  `frames`, `query`, `stats`, `timeline`, `users`, `matrix`, `events`, `interactions`,
  `diff`, `triage`, `label`, `remove`, `formats`, `version`, and `batch` line by line.
- Refused with `usage` (exit 1): `ingest`, `regroup`, `upgrade` and `report` (they need a
  data directory: upload files on the web app's start page, open its report page, or run
  them where the data directory is), and the options the API does not have (`--raw` of
  `show` and `entries`, `formats --sample`). `parse`, `schema`, `manifest` and `serve` run
  locally as always; unset `RAGNALOG_SERVER`, or name a data directory with `--store`, to
  run the others locally.
- Redirects are not followed (they would carry the token to the new address): `--server`
  is the web app's final base URL; a 3xx answer is an `http` error.
- The server's limits apply: the page caps of the API (`HTTP_CAPS` in the parity test) and,
  for `query`, at most 10,000 rows (`"truncated": true` beyond; page with `LIMIT`/`OFFSET`),
  64 MiB of result, 60 s and 1 GB of memory per query, two queries at a time (a third:
  `http`, HTTP 503). `frames` marks application frames by the server's
  `RAGNALOG_APP_PACKAGES`.
- A server error keeps its `reason`, mapped to the CLI's code (`set_not_found` is
  `store_error`, exit 3) and carries `"server"` instead of `"store"`. An unreachable server
  is `http` (exit 1); a server too old for a command answers that it lacks the route.

### Output modes

- **json** — exactly one JSON document on stdout. Every document is an object
  with a `"schema": "<name>.v1"` key. Listings put their rows under `"items"`
  and add `"count"`. Compact on a pipe, indented on a terminal.
  Paged listings (`groups`, `entries`) also carry `"next_offset"` (where the next page
  starts, `null` on the last) and `"total"` (rows matching
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
`RAGNALOG_APP_PACKAGES` to comma-separated prefixes
(`RAGNALOG_APP_PACKAGES=com.acme.,org.example.`) to invert this: a frame is
then an application frame only when it starts with one of them.

Values: timestamps are ISO 8601 strings; stored timestamps are UTC
(`…+00:00`), `parse` keeps the original offset. `level` is numeric in stored
tables (TRACE=10 DEBUG=20 INFO=30 WARN=40 ERROR=50 FATAL=60 UNKNOWN=0) with the
name in `level_name`; `parse` emits the name in `level`. Counts, offsets and
lengths (`--limit`, `--offset`, `--max-raw`, `--before`, `--examples`, …) and
seconds (`--delta`, `context --window`) are at most 2147483647, the API's bound;
a larger or negative value is `invalid_value`. Signatures and their prefixes may
be typed in upper case.

### Errors and exit codes

| Code | Meaning |
|---|---|
| 0 | OK (an empty listing is OK). |
| 1 | Usage error: bad option/argument, unknown format, invalid level, SQL error, incompatible sets in `diff` without `--force`, ambiguous signature prefix, missing optional dependency. |
| 2 | Nothing found / parse failure: no input files, format not detected, a large named file not in the format, no entries parsed, no entries for a signature, file not found. |
| 3 | Store error (data directory): unknown log set, ambiguous reference, corrupt manifest, unsupported schema version, default data directory refused inside a git work tree. |

In `json`/`jsonl` mode errors are written to **stdout** as

```json
{"schema": "error.v1", "error": {"code": "store_error", "message": "no log set 'x'",
 "hint": "available sets: prod-1029, prod-1030", "reason": "set_not_found",
 "store": ".ragnalog"}}
```

`"hint"` (optional) suggests the next step (available set names for an unknown
set; `ragnalog groups SET` for an unknown signature; `ragnalog CMD --help`
for a `usage` error from argument parsing); other optional keys carry context
such as `"store"`. An argument a command does not take is reported as, e.g.,
`unexpected argument 'nope'; sets takes no arguments` or `unexpected argument
'yes'; --brief is a flag and takes no value`. In `table` mode errors go to stderr as
`error: <message> [<code>]` followed by `hint: …`.

Error codes, with the CLI's exit code and the web API's HTTP status (one table in
`service/errors.py`, `SPEC`; `internal_error` is an unexpected exception: set
`RAGNALOG_DEBUG=1` for the traceback):

| Code | Exit | HTTP | Meaning |
|---|---|---|---|
| `usage` | 1 | 400 | Bad option or argument (parse error, unexpected argument, `brief` with `columns`, a page over the API's cap). |
| `invalid_value` | 1 | 400 | A value that is not valid for its option or out of range (time, column, limit, bucket; a number option given no number is `usage`); in the CLI also sets that are not comparable. |
| `invalid_level` | 1 | 400 | Unknown level name. |
| `unknown_format` | 1 | 400 | Unknown format name. |
| `sql_error` | 1 | 400 | DuckDB rejected the SQL of `query`. |
| `ambiguous` | 1 | 409 | A signature prefix (or, in the API, a set reference) matches several; signature errors list `candidates`. |
| `incompatible_sets` | 1 | 409 | The API's code for sets built with different signature or mask-rule versions or specs. |
| `missing_dependency` | 1 | 500 | An optional extra is not installed (`mine`, `web`). |
| `unauthorized` | 1 | 401 | Missing or wrong API token. |
| `payload_too_large` | 1 | 413 | An upload over the size limit. |
| `file_not_found` | 2 | 404 | A named file or directory is missing. |
| `no_sources` | 2 | 422 | No log files in the given paths. |
| `format_not_detected` | 2 | 422 | No format recognises the files. |
| `not_in_format` | 2 | 422 | A named file over 64 MiB is not in the format (force reads it). |
| `no_entries` | 2 | 422 | No entries could be parsed. |
| `not_found` | 2 | 404 | No such group, entry or job; in the API also no such set. |
| `store_error` | 3 | 500 | Data directory failure: corrupt manifest or damaged set, newer schema, data directory refused or in use, a path the system refuses (permission, a file where a directory belongs); in the CLI also an unknown or ambiguous set. |
| `http` | 1 | 400 | Another HTTP error (the API sends its own status). |
| `internal_error` | 1 | 500 | Unexpected exception (set `RAGNALOG_DEBUG=1` for the traceback). |

Some failures carry a `"reason"` that is the same in the CLI and the API, where the
two report them under different codes (the codes predate it and stay as they are;
`REASONS` in `service/errors.py`). Branch on `reason` when it is present:

| Reason | CLI code | API code | Meaning |
|---|---|---|---|
| `set_not_found` | `store_error` | `not_found` | No log set has this id, name or id prefix. |
| `ambiguous_ref` | `store_error` | `ambiguous` | Several log sets share this name or id prefix. |
| `ambiguous_signature` | `ambiguous` | `ambiguous` | A signature prefix matches several groups (`candidates`). |
| `incompatible_sets` | `invalid_value` | `incompatible_sets` | The sets were built with different signature or mask-rule versions or specs (`diff` takes force). |
| `group_not_found` | `not_found` | `not_found` | No group (or no entries) with this signature. |
| `entry_not_found` | `not_found` | `not_found` | No entry with this id. |
| `ref_not_found` | `not_found` | `not_found` | `frames`: neither an entry id nor a group signature. |
| `capture_not_found` | `not_found` | `not_found` | No capture matches this `capture:ID` prefix, `cluster:NAME` or `cluster:NAME@DAY`. |
| `ambiguous_capture` | `ambiguous` | `ambiguous` | Several captures match this capture id prefix or cluster day. |

When the reader of stdout goes away early (`| head`), the command stops
quietly with exit code 1 and no message, on Windows as well as POSIX.

Diagnostics and progress always go to stderr.

### Log-set references

Wherever a command takes `SET`, it accepts the log-set id, its `--name`, or a
unique id prefix (resolved in that order).

A capture (the sets of several cluster nodes collected together, see
`captures`) is referred to as `capture:ID` (its id or a unique prefix of it),
`cluster:NAME` (the newest capture of the cluster) or `cluster:NAME@YYYY-MM-DD`
(the cluster's capture whose last entry falls on that UTC day). A bare capture
id (as `captures` prints it) also names its capture where a set is accepted,
when no set has that id or name. No match is
reason `capture_not_found` (`not_found`, exit 2), several are
`ambiguous_capture` (`ambiguous`, exit 1). `capture`, `timeline`, `matrix`,
`events`, `interactions`, `entries`, `context --all-nodes` and `report` take a
capture reference (`cluster.md` §6), a bare capture id included; the first five
also take a set: the capture it belongs to, else that set as a capture of one
node.

Every capture view counts within one **window**: `--window overlap` (default:
from the latest first entry of the nodes to the earliest last entry, the time
all nodes have logs for, so a group is not "only on NODE2" because NODE1's
files start later), `--window union` (earliest first to latest last), or
`--since`/`--until` (an explicit window; relative times count back from the
capture's last entry). Both ends are inclusive. With only one of them the other
end is the union's (`--until T` alone runs from the capture's first entry, not
from the overlap's start); `--window` does not apply. An explicit window is
clamped to the logs (`--until 9999-12-31` ends at the last entry); one that
misses them entirely is `invalid_value`.

### Signature prefixes

`show SET SIGNATURE`, `frames SET REF` and `entries --signature` (and the API's
`/groups/{sig}`, `/frames/{item}` and `entries?signature=`) accept a group
signature or a unique prefix of it of at least 4 hex characters, resolved via
the set's groups table; documents echo the full signature. A prefix that
matches several groups is error `ambiguous` (exit 1; HTTP 409) with up to 5
sorted `"candidates"`; one that matches nothing is "not found" as for any
unknown signature. Prefixes shorter than 4 characters are not looked up.
`frames` tries entry ids (full ids only) before signatures.

### Filters

The filters of `groups` and `entries` are one request model each
(`service/params.py`: `GroupQuery`, `EntryQuery`), bound to these options and to
the query parameters of the web API, so both front ends parse them alike. A
blank value (`--since ""`, `?logger=`) means the filter is not set.

| Option | Semantics |
|---|---|
| `--level L[,L…]` | Exact level(s), case-insensitive: `ERROR`, `WARN,ERROR`. Names: `TRACE`, `DEBUG`, `INFO`, `WARN` (also `WARNING`), `ERROR`, `FATAL` and `UNKNOWN` (entries whose header could not be parsed: `parse_error` rows, level 0). Anything else (also `SEVERE`, `FINE`) → error `invalid_level` (`--level: unknown level 'SEVERE'; …`). The web API and `LogSet` use the same parser (`store/levels.py`). |
| `--min-level L` | This level or more severe (one name, as `--level`; `UNKNOWN` entries are never included). |
| `--logger X` | Logger name or package prefix: `c.a.jira` matches `c.a.jira.Foo`. Jira writes all but the last three parts of a logger as initials, so a prefix also matches those forms: `com.atlassian.jira` matches `c.a.jira.index.Builder` and `c.a.j.issue.index.Finder`. |
| `--since T` / `--until T` | ISO 8601 (`2026-10-29T09:00`, naive = UTC), a date (`2026-10-29` = midnight UTC), or a duration `Ns`/`Nm`/`Nh`/`Nd` **before the last entry of the set** (`2h` = the last two hours of the log, not of the wall clock). `since` inclusive, `until` exclusive. For `groups` a group is kept when it has an entry in the window: `last_seen >= since`, `first_seen < until`, and with both an entry in `[since, until)` (a group seen before and after the window but not inside it is left out); the group's `count` still covers the whole set. Anything else → error `invalid_value`. |
| `--with-exception` | `groups`, `entries`: only rows that carry an exception (`exception_type` not null), at any level — a DEBUG entry with a stack trace is found although `--min-level WARN` hides it. |
| `--exception TEXT` | `groups`, `entries`: case-insensitive substring of `exception_type` (the outermost exception of the chain; `Caused by:` types are not searched). |
| `--contains TEXT` | `entries`: case-insensitive substring of `raw`. `groups`: case-insensitive substring of the masked `template`. |
| `--limit N` | Maximum rows; `0` = all. |
| `--offset N` | `groups`, `entries`: skip N rows first (paging; default 0). |

## Commands

### `ingest PATH... [--format F] [--name N] [--frames N] [--overwrite] [--mine] [--force] [--node N] [--cluster C] [--capture ID] [--split auto|prefix|dir|none]`

Parse files/directories/ZIP files (directories: non-recursive `*.log*`; rotated
`x.log.N` read oldest first; a gzip file such as `x.log.3.gz` is read decompressed,
in its place among the rotations, and counts by its decompressed content: its
`size` and digest are those of `x.log.3`, so a set ingested once gzipped and once
plain is one set) into a log set, or one set per cluster node. A
directory (or ZIP) with no log file of its own that a format recognises is
searched two levels down for directories that hold some: a support ZIP's
`application-logs/` (not `tomcat-logs/`), `node1/application-logs/`, an
export's `logs/` beside `job.json`. A file from a ZIP is recorded (manifest
`sources`, `ingest.skipped`) as `<zip path>/<member>`: the extracted copy is
removed after the ingest. The id is derived from the file digests:
re-ingesting the same files prints the existing manifest
(`ingest.existing: true`) unless `--overwrite`. Format detection sniffs the
first 200 lines of each file (named files first) until a format recognises
one. Files found in a directory whose first 200 lines contain no line of that
format (the access, GC and JSON logs next to `atlassian-jira.log`) are skipped
and listed in `ingest.skipped` (and on stderr), as are side logs: a file
`<stem>-<name>.log*` next to a `<stem>.log*` that is read
(`atlassian-jira-security.log`, `-outgoing-mail.log`, `-slow-queries.log` beside
`atlassian-jira.log`; a part starting with a digit is a dated rotation, not a
side log). Files named explicitly are
read, except a file over 64 MiB none of whose first 200 lines is in the format
(a wrong file or `--format`): that fails with `not_in_format` (exit 2) unless
`--force`. `--frames N` adds the top N stack frames to signatures. `--mine`
runs Drain3 over the group templates (needs the `mine` extra) and stores
proposals in `merges.parquet`.

Input is bounded: a line over 1 MiB keeps its first 1 MiB (the rest up to the
next line break is skipped), and an entry keeps at most 4 Mi characters and
100,000 lines. The lines after the cap are counted but not stored, and the
entry's `parse_error` is `entry truncated: N lines, M bytes`, where N and M
describe the whole entry (M: UTF-8 bytes of its lines joined with `\n`). Such
entries count in `ingest.parse_errors`.

A file is read up to the size it had when it was hashed: lines a running
application appends meanwhile (a live `atlassian-jira.log`) are left for the
next ingest, so a set holds exactly the bytes its `sources` digests name.

```json
{"schema": "logset.v1", "id": "99ee29398877dcd3", "name": "prod", "format": "jira",
 "sources": [{"path", "size", "xxh3", "first_ts", "last_ts", "entries"}],
 "first_ts", "last_ts", "entry_count", "group_count", "created_at", "tool_version",
 "schema_version", "signature_version", "mask_rules_version", "signature_spec",
 "recipe_key": "jira@4|L+G+E+F0|sig2|mask4", "node": "NODE1", "cluster": "jira-test",
 "capture": null,
 "ingest": {"parsed_entries", "parse_errors", "elapsed_s", "existing", "skipped": [path],
            "warnings": [str]},
 "merges": [{"cluster_id", "signatures": [], "drain_template"}]}
```

`recipe_key` names everything that decides signatures: format and format
version, signature spec, signature and masking-rule versions, and a digest of
extra masking rules when there are any (`|rules:<xxh3>`). Two sets are
comparable (`diff`, `triage`) exactly when their recipe keys are equal.
Manifests written before `recipe_key` existed get it derived from their version
fields (format version 1). Lines before a file's first header become one
parse-error entry at the epoch; it is stored but does not count for
`first_ts`/`last_ts` (of the set or of its `sources`).

Jira entries are assembled with two rules (format version 2, `docs/design.md`
§4): a *banner*, an entry whose message is only decoration (ten or more
characters without a letter or digit, such as ScriptRunner's row of asterisks)
and that has no body, is dropped when the next entry of its file has the same
thread and logger and comes at most 5 ms later; that entry keeps its own id,
`raw` and header and gets the banner's raw line as `context.preamble`. An entry
whose header message is blank is grouped by its first body line that is not
blank or decoration (`Jira starting...`); its `message` stays empty.

A `--name` with `:`, `/` or `\` is refused (`invalid_value`): it would read as a
capture reference (`capture:ID`) or a path.

`ingest.warnings` lists requests that were not honoured, also printed on stderr:
re-ingesting existing files with another `--name` keeps the set's name and
warns `--name tonight ignored: set exists as 'night'` (use `--overwrite` to
rebuild it under the new name).

`merges` only with `--mine`. Exit: 0; 1 unknown format / missing drain3, a bad
ZIP, a `--split` that does not fit, `--node` with a split; 2 no files, format
not detected, a large named file not in the format (`not_in_format`), or zero
entries (manifest still printed); 3.

**ZIP files** are extracted into the data directory (`<data dir>/.spool/`) and removed
afterwards, also after an error or a cancel (what a killed ingest left is
deleted by the next write a day later); error messages name the ZIP, not the
spool. Only `job.json` and `*.log*` members are
extracted, directories inside the archive are kept, links and other special
entries are skipped. A member with an absolute path, a drive or `..` makes the
whole archive fail (`invalid_value`), as do encrypted members and more than
32 GiB extracted by one ingest (`MAX_ZIP_BYTES`).

**Cluster nodes.** `--split` decides whether the input is one set or one set
per node:

| `--split` | One set per node when |
|---|---|
| `auto` (default) | 1. a `job.json` (the cluster log export of the ScriptRunner helper, `{"jobId", "nodes": {"results": [{"node", "status"}]}}`) names the nodes and files are named `<NODE>_<log name>` after them; else 2. files named `<NODE>_<log name>` of at least two nodes share a log name (the node id holds a letter and ends at the last `_`: `NODE1_atlassian-jira.log.3`); else 3. the one directory given has no log files of its own but at least two sub-directories with log files a format recognises, in them or one level down (one node each: `node1/`, `node1/application-logs/`; `tomcat-logs/` is no node), or a single sub-directory holding two or more such directories that are not named like log folders (`case/logs/node1/`, `case/logs/node2/`; `application-logs*`, `tomcat-logs*`, `log*` are one node's folders). Otherwise: one set, as before (a single such sub-directory is that set: a support ZIP). A `job.json` is read in the directory given, beside its log directories, or one level up when it names the nodes of these files (`export/logs` given). |
| `prefix` | Files named `<NODE>_<log name>` (one node is enough). |
| `dir` | Each directory given is a node, or the sub-directories of the one directory given (the node id is the `<NODE>_` prefix all its files share, else the directory name). |
| `none` | Never: one set (`job.json` is not read). |

Files that belong to no node are left out with a warning; a node whose files are
not in the format gets no set. Rotated files are ordered per node.

- **Node id**: `--node` (only without a split; with one it is `invalid_value`),
  else the node names of `job.json`, else the file prefix, else the directory
  name.
- **Cluster**: `--cluster`, else the cluster of the newest earlier set of the
  same node (for a split: when the nodes' earlier sets agree), else none.
- **Capture id**: `--capture`, else the export's `jobId`, else (a split) the
  first 16 hex characters of xxh3-64 over the sorted member set ids. A single
  set gets a capture only from `--capture`.
- **Names** (split): `<prefix>_<node in lower case>`, prefix `--name`, else the
  cluster, else the first 8 characters of the capture id. A name another set has
  already gets `_2`, `_3`, … and a warning.
- **Node hints**: the format reads the first 65,536 lines of each node's newest
  file for lines in which a node names itself (Jira: `nodeId: X`, `on node X
  because`). A set labelled with another node gets a warning; a single set
  without a node only gets a warning that suggests `--node X` (labels stay
  explicit).
- **All or nothing**: a split that fails or is cancelled (Ctrl+C, a cancelled
  web app job) removes the sets it had added, so no capture is left with only
  some of its nodes; sets that existed before (the same files) stay.
- **Again**: re-ingesting the same files reuses the sets (`ingest.existing`)
  and gives them the node, cluster and capture they lack; a label they have
  with another value is kept, with a warning (change it with `label`).
  `--overwrite` keeps the labels it is not given.

A split prints `ingest-capture.v1` instead of `logset.v1`
([formats/ingest-capture.v1.md](formats/ingest-capture.v1.md)):

```jsonc
{"schema": "ingest-capture.v1",
 "capture": {"capture": "2026-11-05_05-44-10-107_ABC", "cluster": "jira-test",
             "first_ts", "last_ts", "entry_count",
             "nodes": [{"node": "NODE1", "id", "name": "jira-test_node1",
                        "first_ts", "last_ts", "entry_count"}]},   // all sets of the capture
 "sets": [<logset.v1 with "ingest", as above>, ...],              // this ingest's sets, by node
 "warnings": ["job.json: node NODE3 reported status FAILED: …", "NODE2: …"]}
```

`warnings` holds the split's notes, then each set's `ingest.warnings` prefixed
with its node. Exit 2 when no set got an entry.

### `sets [--brief]`

```json
{"schema": "sets.v1", "store": "…", "count": 1,
 "items": [<manifest without "schema"> + "summary": {"errors", "warnings", "error_groups"}
           + "stale": false]}
```

Newest first. `summary` counts the entries at ERROR/FATAL (`errors`), at WARN
(`warnings`) and the groups at ERROR/FATAL (`error_groups`), computed from the
group table as `GET /api/sets` does (`null` when a set cannot be read).
`stale` is true when the set's `recipe_key` differs from the recipe this
version signs its format and spec with (a format, signature or masking-rule
version changed since it was built): such a set is not comparable with new
ones, triage skips it as a baseline, and `upgrade` rebuilds it.
`node`, `cluster` and `capture` say which cluster node a set holds, the
instance it belongs to and the capture it was collected in (`null` when
unknown; manifests of schema 1.0 lack them and read as `null`).
`--brief` keeps only `id, name, first_ts, last_ts, entry_count, group_count,
summary, stale, node, capture` per item (no `sources`, no versions). Listing reads only the
manifests and two columns of each group table, without polars. Exit: 0 (also
empty); 3.

### `captures [--brief]`

The captures in the data directory: the sets that share a `capture` id (one cluster log
export, one `ingest` that split its input, or `label --capture`), newest
`last_ts` first. Reads the manifests only (no polars); the twin of
`GET /api/captures`.

```json
{"schema": "captures.v1", "count": 1,
 "items": [{"capture": "2026-11-05_05-44-10-107_ABC", "cluster": "jira-test",
            "first_ts": "…", "last_ts": "…", "entry_count": 1362000,
            "nodes": [{"node": "NODE1", "id": "…", "name": "jira-test_node1",
                       "first_ts": "…", "last_ts": "…", "entry_count": 615000}]}]}
```

`nodes` are the member sets sorted by node (a member without a node shows its
name); `cluster` is the members' (the most common one when they differ).
`--brief` keeps only `node`, `id` and `name` per node. Exit: 0 (also empty); 3.

### `capture REF [--window overlap|union] [--since] [--until] [--no-triage]`

The nodes of a capture side by side; the twin of `GET /api/captures/{ref}`.
`REF` is a capture reference or a set (see [Log-set references](#log-set-references)).

```json
{"schema": "capture.v1", "capture": "2026-11-05_05-44-10-107_ABC", "cluster": "jira-test",
 "window": {"mode": "overlap", "since": "…", "until": "…"},
 "nodes": [{"node": "NODE1", "set": {"id", "name"}, "first_ts", "last_ts",
            "coverage": {"since", "until"} | null, "gaps": [["since", "until"]],
            "entries", "errors", "warnings", "errors_per_hour", "warnings_per_hour",
            "top_error": {"signature", "template", "count"} | null,
            "verdict": "new_errors" | … | "no_baseline" | null,
            "baseline": {"id", "name", "reason"} | null, "events": {"job_run": 12}}],
 "skew": [{"a": "NODE1", "b": "NODE2", "offset_ms": 40, "pairs": 2}]}
```

`coverage` is the node's first to last entry within the window, `gaps` runs of
at least 15 minutes without entries inside it; rates are per hour of
coverage. Each node is triaged against **its own** previous set (as `triage`;
another member of the capture is never a baseline) and gets `verdict` and
`baseline`; `--no-triage` skips it (both `null`). It costs a few tens of
milliseconds per node (two nodes of about 700k entries: under 0.1 s), so it is
on by default. `skew` is a clock skew estimate from paired events, shown and
never applied. The table prints one row per node. Exit: 0; 1 bad option or
time, or the nodes' logs do not overlap (use `--window union`); 2 unknown
capture; 3 unknown set.

### `label SET [--node N] [--cluster C] [--capture ID]`

Sets the cluster node, cluster and capture of a stored set and prints its
manifest (`logset.v1`); the twin of `POST /api/sets/{ref}/label`. Only
`manifest.json` is rewritten (under the data directory's write lock): the id, the name
and the data stay. An option that is not given keeps its value, an empty one
(`--node ""`) clears it. Values are letters, digits, `_`, `.` and `-`, at most
128. Exit: 0; 1 no option or a bad value (`usage`, `invalid_value`); 3 unknown
set.

### `set SET`

One set: its manifest (`logset.v1`, as `ingest` prints it, without `ingest`)
plus `summary` as in `sets`; the twin of `GET /api/sets/{ref}`.

```json
{"schema": "logset.v1", "id": "…", "name": "prod", …, "summary": {"errors": 5, "warnings": 2, "error_groups": 3}}
```

Exit: 0; 3 unknown set.

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
{"schema": "groups.v1", "logset": "…", "count": 2, "total": 7, "offset": 0, "next_offset": 2, "truncated": true, "items": [
  {"signature", "coarse_signature", "template", "level", "level_name", "logger",
   "exception_type", "count", "first_seen", "last_seen", "user_count", "thread_count",
   "source_count", "top_users", "top_threads", "sources", "examples"}]}
```

Exit: 0 (also empty); 1 bad option, unknown column, `--brief` with `--columns`; 3.

### `show SET SIGNATURE [--limit 20] [--raw] [--max-raw N] [--columns C[,C…]]... [--brief]`

Entries of one group, ordered by time, with `variables` (the values masking
replaced, in order). `SIGNATURE` may be a unique prefix (see
[Signature prefixes](#signature-prefixes)). `--raw` prints the original log text of each entry,
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

`--columns` selects entry columns as for `entries`. `--brief` is the preset
`id, ts, level_name, thread, user, request_id, logger, message,
exception_type, signature, variables` (`entries --brief` plus `variables`)
and also cuts `group` to the `groups --brief` columns; without it a row
repeats the stack trace in `raw`, `body`, `exception_chain` and
`exception_message`. `--brief` with `--columns` is a usage error.

Exit: 0; 1 bad option, unknown column, `--brief` with `--columns`; 2 no
entries with this signature; 3.

### `entries SET|CAPTURE [--signature] [--coarse-signature] [--level] [--min-level] [--logger] [--user] [--thread] [--request-id] [--since] [--until] [--contains] [--with-exception] [--exception T] [--node N[,N…]] [--columns C[,C…]]... [--brief] [--limit 100] [--offset 0] [--raw] [--max-raw N] [--window overlap|union]`

`--user`/`--thread`/`--request-id` are exact (`--request-id` is the Jira
request id of the request context, e.g. `540x101x1`: all entries of one HTTP
request); `--coarse-signature` is exact too (the signature of `groups --coarse`); `--contains` is a case-insensitive substring of `raw`; `--columns` selects columns, comma-separated and/or repeated
(`--columns ts,level_name --columns message`; order kept, duplicates dropped).
An unknown column is error `invalid_value` naming it, with the valid columns
in `hint`. `--brief` is the preset `id, ts, level_name, thread, user,
request_id, logger, message, exception_type, signature` (not combinable with
`--columns`).
`--raw` prints the raw text like `show --raw`. `--max-raw` cuts
`raw`/`body` as described for `show` (default 4000 in `json`/`jsonl`).

```json
{"schema": "entries.v1", "logset": "…", "count": 10, "total": 42, "offset": 0,
 "next_offset": 10, "truncated": true, "items": [<entries.v1 row>]}
```

`next_offset` is where the next page starts (`offset + count`), `null` on the
last page, as in `GET /api/sets/{ref}/entries`.

A capture reference gives the **merged log**: the entries of all nodes
interleaved by time (then node), the twin of `GET /api/captures/{ref}/entries`.
`logset` is `capture:ID` and every row adds `node` and `set` (the node's set
id; `--columns` may name both, they are always there). `--since`/`--until`
set the window, else `--window` (overlap by default); the other filters,
paging, `--columns`, `--brief` and `--max-raw` work as for a set, and
`--signature` is resolved in the groups of any node; it may name several
groups, comma-separated (`--signature 83d1,a7f0`: the entries of any of them,
each a full signature or a unique prefix), and `NODE:SIGNATURE` keeps a group's
entries on that node only (`--signature N1:83d1,N2:a7f0`: both sides of a
co-burst; a node the capture lacks is `invalid_value`). `--node N1,N2` keeps the entries of
those nodes; the window stays that of all nodes, and a node the capture does not
have is error `invalid_value` (exit 1) naming its nodes in `hint`. `--raw`
prints the raw texts; with colour on, each rule names the node and
`file:line`. `--window`, `--node`, several signatures and `NODE:SIGNATURE` with a
set are usage errors.

<!-- generated:entries-columns by docs/formats/generate.py from store/schema.py -->
entries.v1 columns: `id`, `ts`, `tz_offset`, `level`, `level_name`, `thread`,
`logger`, `message`, `body`, `user`, `request_id`, `session`, `ip`, `url`,
`request_url`, `context`, `exception_type`, `exception_message`,
`exception_chain`, `signature`, `coarse_signature`, `template`, `variables`,
`source_path`, `source_line`, `source_offset`, `raw`, `parse_error`.
<!-- /generated -->

Exit: 0 (also empty); 1 bad option, unknown column, `--brief` with
`--columns`, `--window`, `--node` or several signatures with a set, a node not
in the capture; 2 unknown capture, or a signature in no node's groups; 3.

### `context SET ENTRY_ID [--before 5] [--after 5] [--any-thread] [--max-raw N] [--columns C[,C…]]... [--brief] [--all-nodes] [--window 30]`

The entries immediately around one entry in time order (ties in file order),
restricted to the anchor's thread unless `--any-thread`; the anchor itself is
included. Entry ids are the `id` column of `entries`/`show` or a group's
`examples`. `--max-raw`, `--columns` and `--brief` as for `show` (`--brief`:
`id, ts, level_name, thread, user, request_id, logger, message,
exception_type, signature, variables`).

```json
{"schema": "entries.v1", "logset": "…", "anchor": "<entry id>", "thread": "http-nio-8080-exec-1",
 "any_thread": false, "count": 11, "items": [<entries.v1 row>]}
```

At most `--before + 1 + --after` items. `jsonl`: one row per line, the anchor
row with an extra `"anchor": true` (also when `--columns` leaves out `id`).
The table marks the anchor row with `▶`.

`--all-nodes` answers "what did every node do at that moment?": the entries of
all nodes of the set's capture within `--window` seconds (default 30) before
and after the anchor, all threads, in time then node order; the twin of
`…/context?all_nodes=1&window=30`. `--before`, `--after` and `--any-thread`
do not apply. Rows add `node` and `set`, `any_thread` is `true`; at most 2000
rows (the nearest to the anchor), `total` and `truncated` say when there were
more. A set in no capture is `invalid_value` whose hint says how to label one.
With `--all-nodes`, `SET` may also be a capture (`capture:ID`, a bare capture
id, `cluster:NAME[@YYYY-MM-DD]`): the anchor's set is the node that has the
entry. `--window` is at most 2147483647 seconds (`invalid_value`).

Exit: 0; 1 bad option, unknown column, `--brief` with `--columns`,
`--all-nodes` for a set in no capture; 2 no entry with this id (`not_found`,
with a hint); 3.

### `frames SET REF [--limit 10] [--all]`

The exception chain of one entry with its application frames (see
[Application frames](#application-frames)). `REF` is an entry id or a group
signature (16 hex chars) or a unique signature prefix (see
[Signature prefixes](#signature-prefixes)); entry ids are tried first, and
for a signature the group's first example entry is used.

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
`app_packages` echoes `RAGNALOG_APP_PACKAGES` (`null` when unset). `jsonl`:
one exception per line. The table prints each exception with its frames as
text. Only the first exception chain of an entry is parsed (`design.md` §11),
and the chain ends at the first line that is not a frame.

Exit: 0; 1 bad option; 2 no entry or group `REF` (`not_found`, with a hint); 3.

### `query SET SQL`

DuckDB SQL over the views `entries`, `groups` and (when present) `merges`. One
statement only (a trailing `;` is fine): several are a `usage` error.
`ragnalog query --help` lists the same columns; `ragnalog schema NAME` adds
types and descriptions. The session time zone is UTC whatever the machine's
zone: timestamps in the result are UTC, as in `entries`, and
`date_trunc('day', ts)` buckets at UTC midnight. An `INTERVAL` result
(`max(ts) - min(ts)`) comes back as text, e.g. `"4 days 20:15:28.033"`
(`epoch(max(ts) - min(ts))` gives seconds).

<!-- generated:view-columns by docs/formats/generate.py from store/schema.py -->
Columns of the views:

- `entries`: `id`, `ts`, `tz_offset`, `level`, `level_name`, `thread`, `logger`,
  `message`, `body`, `user`, `request_id`, `session`, `ip`, `url`,
  `request_url`, `context`, `exception_type`, `exception_message`,
  `exception_chain`, `signature`, `coarse_signature`, `template`, `variables`,
  `source_path`, `source_line`, `source_offset`, `raw`, `parse_error`
- `groups`: `signature`, `coarse_signature`, `template`, `level`, `level_name`,
  `logger`, `exception_type`, `count`, `first_seen`, `last_seen`, `user_count`,
  `thread_count`, `source_count`, `top_users`, `top_threads`, `sources`,
  `examples`
- `merges`: `cluster_id`, `drain_template`, `signatures`
<!-- /generated -->

```json
{"schema": "query.v1", "logset": "…", "columns": ["level_name", "n"], "count": 4, "total": 4, "truncated": false, "items": [{"level_name": "WARN", "n": 18}]}
```

Errors are `sql_error` (exit 1). For an unknown column the `hint` lists
DuckDB's candidate columns (or, without candidates, the columns of the views);
for an unknown table it lists the available views.

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

### `timeline SET|CAPTURE [--bucket 1h] [--signature SIG] [--level] [--min-level] [--logger] [--user] [--thread] [--request-id] [--contains T] [--with-exception] [--exception T] [--coarse-signature] [--node] [--since] [--until] [--window overlap|union] [--by-node] [--by-level]`

Entries per time bucket (`<n>s|m|h|d`, aligned to the epoch in UTC). A set
gives `timeline.v1`, the twin of `GET /api/sets/{ref}/timeline` (it takes every filter of
`entries`, with the same names and meaning, so a chart can count what a filtered
listing shows). A capture reference,
or a set with `--by-node` (its capture; a set in no capture is one node), gives
`cluster-timeline.v1`, the twin of `GET /api/captures/{ref}/timeline`: per node
and bucket the entries by level, or with `--signature` those of one group,
over the window (`--window`, `--since`, `--until`). It takes the same filters of
`entries` as a set (the counts are those of the filtered listing), and `--node` keeps
those nodes only; `--signature` names one group (several, or `NODE:SIGNATURE`, is a
usage error; `--node` with a set is one too). `--by-level` (a set) adds each bucket's
entries per level, `levels`: `ERROR` (with FATAL), `WARN`, `INFO`, `DEBUG` (with TRACE)
and `OTHER` (unknown level), the series of the report's chart, which asks for them at bins
under an hour; a capture's buckets are by level already, so `--by-level` with one is a
usage error.

```json
{"schema": "timeline.v1", "logset": "…", "bucket": "1h", "count": 24, "items": [{"bucket", "count", "levels"?}]}
{"schema": "cluster-timeline.v1", "bucket": "1h", "buckets": ["2026-11-02T09:00:00Z", …],
 "nodes": {"NODE1": {"levels": {"ERROR": [n…], "WARN", "INFO", "DEBUG", "OTHER"},
                     "coverage": [first_bucket, last_bucket] | null}}, "signature"?: "…"}
```

The table prints one row per bucket (a capture: a column per node; blank
where the node has no logs; `--by-level`: a column per level). Exit: 0; 1 bad option, bucket, level or time,
a set-only filter (`--level`, `--thread` …) with a capture, `--window` with a set, `--by-level` with a capture, ambiguous signature prefix;
2 unknown capture or group; 3.

### `users SET [--signature SIG] [--level] [--min-level] [--logger] [--user] [--thread] [--request-id] [--contains T] [--with-exception] [--exception T] [--coarse-signature] [--since] [--until] [--limit 50] [--offset 0] [--top 3]`

Who logs what: the matching entries per user, most entries first (ties by name); the twin of
`GET /api/sets/{ref}/users`. It takes the filters of `entries`: `--signature` gives the users
of one group, `--since`/`--until` a window, `--user NAME --top 50` one user's groups. Each row
has the user's entries, how many are ERROR/FATAL and WARN, the distinct groups and threads, the
first and last timestamp and the user's `--top` largest groups (default 3, at most 1000).
`total` counts the users, `entries` the matching entries with a user and `without_user` those
without one (a blank or missing user); `--limit 0` lists every user.

```json
{"schema": "users.v1", "logset": "…", "count": 50, "total": 812, "offset": 0, "next_offset": 50,
 "truncated": true, "entries": 41922, "without_user": 1203,
 "items": [{"user", "entries", "errors", "warnings", "groups", "threads", "first_ts", "last_ts",
            "top": [{"signature", "count"}]}]}
```

The table prints one row per user with the largest group. Exit: 0; 1 bad option, level, time or
`--top`, ambiguous signature prefix; 3.

### `matrix REF [--level] [--min-level] [--logger] [--since] [--until] [--with-exception] [--exception T] [--contains T] [--window overlap|union] [--node N] [--spread all|only|skewed] [--sort count|skew|lead] [--skew-min 4.0] [--signature SIG] [--limit 50] [--offset 0]`

"Is it cluster-wide or one node's problem, and which node saw it first?": one
row per group with a count column per node; the twin of
`GET /api/captures/{ref}/matrix`. The filters are those of `groups`;
`--since`/`--until` set the window (else `--window`).

```json
{"schema": "matrix.v1", "window": {…}, "nodes": ["NODE1", "NODE2"], "count": 2, "total": 9,
 "offset": 0, "truncated": true,
 "items": [{"signature", "template", "level", "level_name", "logger", "exception_type", "total",
            "per_node": {"NODE1": {"count", "rate_per_hour", "first_seen", "last_seen"}},
            "spread": "all" | "only" | "skewed", "skew": 35.2 | null,
            "first_node": "NODE2", "lead_ms": 68400000 | null}]}
```

`spread`: `only` (on one node), `skewed` (missing from a node that has logs in
the window while on two or more, or the max/min of `count / node entries in
the window` is at least `--skew-min`), `all` otherwise. `lead_ms` is the gap
between the first two nodes' first entries. `--node` keeps groups present on
that node, `--spread` one spread, `--signature` one group (a unique prefix
works); `--sort count|skew|lead` (descending); `--limit` (0 = all) and
`--offset` page as in `groups`. Exit: 0 (also empty); 1 bad option, level,
time, node, or ambiguous signature prefix; 2 unknown capture or group; 3.

### `events REF [--window overlap|union] [--since] [--until] [--kind K]...`

What every node did, on one time axis: restarts, health-check failures, index
snapshots, restores and replays, re-indexes, cluster locks, cache replication
and peers, job runs (rules over the group templates, `cluster.md` §7 and
§11.5); the twin of `GET /api/captures/{ref}/events`.

```json
{"schema": "events.v1", "rules_version": 1, "count": 3,
 "items": [{"node", "ts", "last_ts", "kind", "subject", "subject_node", "signature",
            "entry_id", "count", "text"}],
 "pairs": [{"a": {"node", "kind"}, "b": {"node", "kind"}, "count", "of", "median_lag_ms",
            "lift", "examples": [["ts_a", "ts_b"]]}]}
```

`--kind` keeps these kinds (comma-separated and/or repeated; an unknown one is
`invalid_value` listing the kinds). `pairs` are kinds on different nodes that
follow each other within 60 s at least 3 times more often than by chance.
Exit: 0 (also empty); 1 bad option, kind or time; 2 unknown capture; 3.

### `interactions REF [--window overlap|union] [--since] [--until] [--delta 300]`

How the nodes affect each other; the twin of
`GET /api/captures/{ref}/interactions`: `mentions` (entries on one node naming
another node id, with the groups behind each edge), `co_bursts` (a group
bursting on one node followed by a burst of a group on another within
`--delta` seconds, with hits, median lag and lift), `concurrent_jobs` (one
job key run on several nodes in the same minute), `sessions` (a session seen
on two nodes within an hour) and `jobs` (job runs per node).

```json
{"schema": "interactions.v1", "delta_s": 300,
 "mentions": [{"from", "to", "count", "groups": ["sig"]}],
 "co_bursts": [{"a": {"node", "signature", "template", "groups"}, "b": {…}, "hits", "of",
                "median_lag_ms", "lift"}],
 "concurrent_jobs": [{"job", "nodes", "slots", "examples"}],
 "sessions": [{"nodes": ["NODE1", "NODE2"], "count"}], "jobs": [{"job", "runs": {"NODE1": 3}}]}
```

The table prints co-bursts as sentences. Exit: 0; 1 bad option or time; 2
unknown capture; 3.

### `diff SET_A SET_B [--min-level L | --level L] [--coarse] [--force] [--limit 50] [--rate]`

Compares stored group tables: `new` (only in B), `vanished` (only in A),
`changed` (both, different count; `ratio = count_b / count_a`). `--level` is an
alias of `--min-level` and means "this level and above" here (unlike `entries
--level`, which matches exactly); an invalid level names the flag you typed.

```json
{"schema": "diff.v1", "a": "<id>", "b": "<id>", "new": [row], "vanished": [row],
 "changed": [row], "unchanged_count": 1,
 "totals": {"new": 3, "vanished": 0, "changed": 5}, "truncated": false,
 "warnings": ["Set B (night) covers 30 min but set A (day) covers 6.9 h (13.7x shorter): …"]}
```

Each section holds at most `--limit` rows (default 50, `0` = all); `totals`
are the section sizes before the cut and `truncated` is true when any section
was cut. The table prints `vanished: none` (etc.) instead of an empty table.

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
mode the sentence is printed on stderr (`warning: …`) instead. Exit: 0; 1 sets signed differently (their `recipe_key`s differ; the
message names the parts, e.g. `signature_spec: L+G+E+F0 != L+G+E+F2`, and
suggests `upgrade` or `regroup`) without `--force`; 3.

### `triage SET [--baseline SET|auto|none] [--min-level WARN] [--grow 2.0] [--limit 50]`

"Are there new kinds of errors since the baseline?" in one document — the same
`triage.v1` as `GET /api/sets/SET/triage` of the web app, without its
`report_url` links. It compares the baseline with SET like
`diff BASELINE SET --min-level L` and sorts the result for a decision.

`--baseline none` (also blank) judges SET without a baseline (verdict
`no_baseline`), as `baseline=none` does in the API. `--baseline auto` (default)
ranks the other sets of the same format with
`store.triage.rank_baselines`, the rule the web app uses too. A candidate is
rejected, with `reason`:

1. `stale` / `recipe`: its `recipe_key` differs from SET's. `stale` when one
   of the two was built with an older recipe (the `hint` names
   `ragnalog upgrade SET`), `recipe` for another signature spec or extra rules.
2. `other_node`: both sets have a `node` and they differ. Each node is
   compared with its own past, never with its neighbour's logs.
3. `superset` / `subset`: its source digests (`sources[].xxh3`) contain all of
   SET's, or all of its files are part of SET. An overlapping snapshot of the
   same files is no baseline; partial overlap (rolling snapshots share most
   rotated files) is allowed and counted in `shared_sources`.
4. `later`: it ends after SET ends.
5. `tiny`: fewer than 1% of SET's entries; `span`: its span is more than 16x
   longer or shorter than SET's.

Of the rest it prefers a set that ends before SET starts, then (when SET has a
node) one of the same node over one without a node, then the span closest
to SET's (`|log(h_a/h_b)|`, rounded to 0.1 so spans within about 10% count as
equal), then the newest. When nothing is left the verdict is `no_baseline` and
`warnings` names every rejected set with its reason. An explicit `--baseline
SET` is used as given, with a warning when it is stale, a superset/subset of
SET's sources, or tiny (a set signed with another recipe still fails as in
`diff`).

```jsonc
{"schema": "triage.v1",
 "set": {"id", "name", "first_ts", "last_ts", "entries"},
 "baseline": {"id", "name", "first_ts", "last_ts", "resolved_by": "auto|param",
              "reason": "same recipe; ends before this set starts; span 8.0 h vs 7.9 h",
              "entries": 312000, "shared_sources": 0,
              "candidates": [{"id", "name", "reason": "superset", "detail", "hint"?}]} | null,
 "min_level": "WARN",
 "verdict": "new_errors | new_warnings | new_exceptions | clean | no_baseline",
 "summary": {"new", "new_by_level": {"ERROR": n}, "new_exceptions", "grew", "vanished",
             "changed", "unchanged"} | null,
 "coverage": {"hours_baseline": 6.88, "hours_set": 0.51, "ratio": 0.074},
 "warnings": ["The set covers 31 min but the baseline covers 6.9 h (13.5x shorter): …"],
 "new":            [<diff row> + rate_per_hour],   // ERROR first, then by count
 "new_exceptions": [<diff row> + rate_per_hour],   // any level, not in "new"
 "grew":           [<diff row> + ratio + rate_a + rate_b + rate_ratio],
 "vanished":       [<diff row>]}
```

- `new`: groups at or above `--min-level` that exist only in SET, most severe
  first, then by count.
- `new_exceptions`: new groups of **any** level whose `exception_type` is set
  and that are not already in `new` (a new DEBUG stack trace), same order.
  Empty when `--min-level` is the lowest level that matters (everything new is
  then in `new`).
- `grew`: groups in both sets whose hourly rate rose at least `--grow` times
  (`rate_a`, `rate_b` and `rate_ratio` as in `diff --rate`; rates normalise
  sets of different length), sorted by `rate_ratio` descending.
- `baseline.reason`: why the baseline was chosen, in one line;
  `baseline.entries` its entry count; `baseline.shared_sources` the source
  files it shares with SET; `baseline.candidates` up to 5 rejected sets
  (`--baseline auto` only), newest first.
- `set`/`baseline` timestamps are written as in the sets' manifests
  (`logset.v1`), row timestamps as in `diff`.
- `verdict`: `new_errors` (a new ERROR/FATAL group), `new_warnings` (other new
  groups), `new_exceptions` (`new` is empty but `new_exceptions` is not),
  `clean`, `no_baseline`.
- `coverage`: span of each set in hours and `ratio = hours_set /
  hours_baseline`. When the spans differ by more than 4x either way,
  `warnings` holds a sentence saying that `new`/`vanished` are unreliable (a
  nightly set lacks daytime groups). `warnings` also says which sets were
  rejected when there is no baseline, and why an explicit baseline is
  doubtful; otherwise it is `[]`.
- Lists hold at most `--limit` rows (`0` = all); `summary` counts all.
- `jsonl` prints the document on one line; the table prints the verdict, the
  summary and one table per list, with the warnings on stderr.

SET is one set: a capture reference is a `usage` error whose hint is
`ragnalog capture REF --triage`, which gives the verdict of every node
against its own previous set (`cluster.md` §5).

Exit: 0 whatever the verdict (also `no_baseline`); 1 bad option, invalid level,
`--grow` not > 1, a capture reference, or the sets were signed differently; 3
unknown set.

### `report SET|CAPTURE [--out FILE] [--compare SET_B] [--min-level L] [--examples 3] [--max-raw 4000] [--max-groups N] [--window overlap|union] [--open]`

Writes a single self-contained HTML file (CSS, JS and data inlined; no network
requests, works from `file://`, light and dark theme): overview tiles, a
per-hour timeline by level (drag or pick "last 1 h / 6 h / 24 h / 7 d" to count
the table within a time window), the
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
prints one line (`wrote <path> (…)`).

A capture reference writes the **cluster page** instead (`cluster.md` §8,
`report.md` §5), its `cluster-report.v1` payload embedded: node cards with each
node's verdict against its own previous set, a timeline band per node with its
events, the groups by node, the interactions and the example entries of all
nodes, within `--window` (overlap by default). `--out` defaults to
`<cluster>_<capture id>.html`; `--examples` counts per group and node;
`--max-groups` defaults to 500 for a capture (all for a set; 0 = all).
`--compare` and `--min-level` apply to sets only. The document adds
`"capture": "<id>"` and `"nodes": 2`.

Exit: 0; 1 bad option, invalid level, `--out` is a directory (`usage`), or the
sets were signed differently; 2 output directory does not exist, unknown
capture; 3.

### `parse PATH... [--format F] [--limit 0] [--force]`

Streams entries as JSON Lines to stdout without a data directory; `--output` is ignored.
Files are selected and bounded as by `ingest` (`--force` likewise).

```json
{"id", "format", "timestamp": "2026-10-29T02:19:41.415+01:00", "level": "ERROR", "thread",
 "logger", "message", "body", "exception_type",
 "exceptions": [{"type", "message", "frames": [{"location", "file", "line", "module"}], "omitted_frames"}],
 "context": {"user", "request_id", "session", "ip", "url", "request_url", "preamble"},
 "source": {"path", "line", "offset"}, "raw", "parse_error"}
```

`context.preamble` only on entries a banner was attached to (see `ingest`).

Exit: 0; 1 unknown format; 2 no files / not detected / `not_in_format` / no entries.

### `regroup SET [--frames N] [--no-exceptions]`

Re-signs the stored entries with another signature spec and rewrites the
groups (any `merges.parquet` is dropped). Each distinct (level, logger,
message, exception chain, and the body's lead line of a blank message) is
signed once and only the `signature`, `coarse_signature`, `template` and
`variables` columns of `entries.parquet` are replaced (712k entries: about 6 s).
A set built by an older version of its format is also re-assembled from its
stored entries, as `upgrade` describes. Prints the manifest (`logset.v1`, new
`signature_spec` and `recipe_key`). Diffs against sets with a different spec
then need `--force`. Exit: 0; 1; 3.

### `upgrade [SET...] [--all]`

Rebuilds stale sets (see `sets`) at the current recipe, each with its own
signature spec, by re-signing their entries as `regroup` does. Sets that are
current, or whose format is not registered, are skipped with a reason (an
unknown format is also printed on stderr).

Jira sets built before format version 2 (`recipe_key` `jira@1|…`) are stale:
run `ragnalog upgrade --all` once. Their entries are re-assembled from the
stored rows alone (the log files are not needed): banners are attached to the
entry they announce (`context.preamble`, see `ingest`), entries with a blank
message get their title from the body, and `entry_count`, the per-source counts
and time ranges and the groups follow. The result equals ingesting the same
files again. On the sample data directory (four sets, 2.1M entries) 122,538 banner
entries were attached and 49 stayed; each set took 2–6 s.

Sets built before Jira format version 3 (`jira@2|…`) are stale too. Version 3
splits the request context of a user whose name has a space (`Automation
Robot 1x2x3 <session> <ip> <url>`: before, the whole context was the user) and
keeps a suppressed exception's own `Caused by:` out of the main chain. `upgrade`
builds those entries again from their stored `raw` text (only rows with a
`Suppressed:` body or a user with a space and no request id are looked at).
Masking rules version 2 (`…|mask2`) masks an IP address that ends a sentence
(`from 10.0.0.1.`) whole; `upgrade` re-signs for it.
Masking rules version 4 (`…|mask4`) masks a date written by Java's
`Date.toString()` (`Tue Nov 24 00:00:00 CET 2026`) as one `<TS>`. Before, its
weekday and month names stayed in the template, so a message holding one got a
new signature every day and `triage` and `diff` reported it as a new group.
Sets built before are stale until `upgrade` re-signs them.

A set ingested from a ZIP before 0.2.0 named its files by the spool directory they
were extracted to (`<data dir>/.spool/<id>/<zip stem>/<member>`, removed after the
ingest). `upgrade` renames them to `<zip stem>.zip/<member>` in the entries, groups
and manifest, also for a set that is otherwise current, and adds
`"renamed_sources": N` to its item.

```json
{"schema": "upgrade.v1", "count": 1, "upgraded": 1,
 "items": [<manifest without "schema"> + "status": "upgraded|skipped",
           "reason": null | "current" | "format 'x' or signature spec … is unknown",
           "previous_recipe_key": "jira@1|L+G+E+F0|sig1|mask0"]}
```

Items follow the SET arguments (`--all`: the `sets` order). Exit: 0 (also when
all are skipped); 1 neither SET nor `--all`, or both; 3 unknown set.

### `formats [--sample FILE]`

```json
{"schema": "formats.v1", "count": 1, "items": [{"name": "jira", "description", "signature_spec", "mask_rules": [], "confidence"}]}
```

`confidence` (0–1) only with `--sample`; items are then sorted by it. Exit: 0; 1
`--sample` is a directory (`usage`); 2 sample file not found.

### `schema [entries|groups|merges|logset|NAME.v1]`

```json
{"schema": "schema.v1", "definitions": {"groups": {"schema": "groups.v1", "version": "1.0", "fields": [{"name", "type", "nullable", "description"}]}}}
```

A table name prints its columns; a document name (`triage.v1`, `error.v1`, …)
prints the document's JSON Schema (draft 2020-12, the same as
`docs/formats/NAME.schema.json`; rows are described by the table columns, all
optional). Without an argument: the four tables plus `"documents"`, the list of
document names. Table mode prints the same JSON indented. Exit: 0; 1 unknown
name (`usage`).

### `manifest`

`manifest.v1` ([docs/formats/manifest.v1.md](formats/manifest.v1.md)), about 10 KB
of compact JSON (its budget is 16 KB), for agents:

```json
{"schema": "manifest.v1", "tool": "ragnalog", "version": "0.6.1", "usage": "...", "notes": [str],
 "workflow": [{"run": "triage LOGSET", "purpose": "new kinds of errors since the previous set?"}, ...],
 "when": [{"run": "query LOGSET SQL", "purpose": "..."}],
 "commands": {"groups": {"usage": "LOGSET [--level str] ... [--limit int=50] ... [--brief]", "output": "groups.v1", "exit": [0, 1, 3]}, ...},
 "help": {"--limit": "Maximum number of rows; 0 = all", ...},
 "global_options": {"--output": "table|json|jsonl: Output mode", ...},
 "exit_codes": {"0": "ok", ...}, "documents": {"get": "ragnalog schema NAME ...", "names": [...]},
 "presets": {"groups": [...], "entries": [...], "show, context": [...]}, "env": {"RAGNALOG_STORE": "...", ...}}
```

Generated from the command definitions (usage with types and defaults, the
output document named in each `--help`, exit codes, parameter help), the
document Structs and the `--brief` presets; only the workflow, `when`, `notes`
and `env` are written by hand (`cli/manifest.py`). The documents are generated
into the package (`service/manifest.json`, by `docs/formats/generate.py`; a test
fails when it is out of date) and read from there. A command of the workflow
or `when` has no `summary` (its `purpose` says it). `help` gives each
parameter name once, as most commands word it; `ragnalog COMMAND --help` has
each command's own. Bare `ragnalog` prints this document when stdout is not
a terminal (with `--output table`, or on a terminal, it prints the text help);
the web app serves it at `GET /api/manifest`.

`manifest COMMAND` prints one command in full (`manifest-command.v1`): its
`--help` text without the parameter list (`description`, with the output shape
and exit codes), `usage`, `output`, `exit` and every parameter with `type`,
`default` and its whole `help`; `GET /api/manifest/{command}` is its twin. An
agent reads the manifest once, then `manifest COMMAND` for the commands it runs.
Exit: 0; 2 unknown COMMAND (`not_found`).

### `remove SET`

```json
{"schema": "remove.v1", "removed": "<id>", "name": "prod"}
```

A set whose `manifest.json` cannot be read is removed by its directory name
(`"name": null`); an unknown set is still an error. Exit: 0; 3.

### `serve [--host 127.0.0.1] [--port 8321] [--token T] [--max-upload-mb 2048] [--allowed-host NAMES] [--reload]`

Runs the web app over the data directory until interrupted (needs the `web` extra). Pages:
`/` (sets with triage verdicts and upload), `/sets/SET` (report, `?compare=auto|SET`),
`/sets/SET/triage`; JSON API under `/api` with the same documents as this CLI and
OpenAPI at `/docs`. `--token` (env `RAGNALOG_TOKEN`) requires
`Authorization: Bearer T` on `/api`; `--max-upload-mb` defaults to env
`RAGNALOG_MAX_UPLOAD_MB`, else 2048. `--reload` passes every setting to the
reloading process through these environment variables. At startup the newest
capture's cluster page payload is built in the background (its first open is a
cache hit). On a loopback `--host` only loopback `Host` names (and the
`--allowed-host` names, `RAGNALOG_ALLOWED_HOSTS`) are answered, and
requests that change something from a page of another site are refused (see
`docs/webapp.md`, Security). A `--host` off loopback without a token prints a
warning on stderr. Nothing is printed to stdout; the server logs to stderr. See `docs/webapp.md`. One server per data directory: a
second `serve` on a data directory that is being served fails with `store_error`.

Exit: 0 stopped; 1 the `web` extra is missing (`missing_dependency`) or bad option; 3
(also: the data directory is served already).

### `version`

```json
{"schema": "version.v1", "version": "0.6.1", "schema_version": "1.1", "signature_version": 2, "mask_rules_version": 4, "python": "3.14.2"}
```

### `batch`

Runs many commands in one process, to avoid the start-up cost of each call
(about 0.45 s, mostly imports). stdin is JSON Lines, one command per line as a
JSON array of its arguments, without `ragnalog` and without global options:

```sh
printf '%s\n' '["groups", "prod", "--brief", "--limit", "5"]' '["stats", "nope"]' '["version"]' \
  | ragnalog --store /data/store batch
```

Each line runs with the global options of the `batch` call (`--store`, `--quiet`,
…) and `--output json`, and writes exactly one JSON document to stdout, in input
order: the command's usual document, or `error.v1` when it fails (its message starts
with `line N:`) — including a
line that is not a JSON array of strings, one that sets a global option, asks for
`--help`/`--version`, or runs `batch`, `parse`, `serve` or a `--raw` listing (none
of which write one document). Blank lines are skipped. A failed line never stops
the batch; the exit code is 0 unless stdin cannot be read (not UTF-8: exit 1).
Diagnostics of the commands go to stderr as usual.

```json
{"schema": "groups.v1", "logset": "…", "count": 5, …}
{"schema": "error.v1", "error": {"code": "store_error", "reason": "set_not_found", …}}
{"schema": "version.v1", …}
```

## Recipes

```sh
# Upgrade comparison: what is new or louder after the upgrade?
ragnalog ingest logs-before/ --name before
ragnalog ingest logs-after/ --name after
ragnalog diff before after --min-level WARN --limit 20 --rate    # rates if spans differ

# Who triggers a group? (users and request URLs of one signature)
ragnalog query after "select user, count(*) n from entries
  where signature = '0123456789abcdef' group by 1 order by 2 desc"

# Hourly error rate
ragnalog query after "select date_trunc('hour', ts) h,
  count(*) filter (where level >= 50) errors, count(*) total,
  round(100.0 * errors / total, 1) pct from entries group by 1 order by 1"

# Exceptions at any level (DEBUG stack traces included), then one exception type
ragnalog groups after --with-exception --brief
ragnalog entries after --exception InternalException --brief

# What broke since the previous snapshot? (verdict, new types, growth, coverage)
ragnalog triage after --baseline before

# The last 2 hours of the log (relative to its last entry)
ragnalog groups after --since 2h --min-level ERROR
ragnalog entries after --since 2h --level ERROR --raw

# Clusters: the nodes of one export side by side
ragnalog ingest jira-logs_2026-11-05_EXPORT.zip --cluster jira-test    # one set per node
ragnalog captures --brief
ragnalog capture cluster:jira-test             # per node: coverage, errors/h, verdict, events
ragnalog matrix cluster:jira-test --spread only --min-level WARN    # groups on one node only
ragnalog matrix cluster:jira-test --sort lead --limit 10            # who saw it first
ragnalog events cluster:jira-test --kind start,index_replay,cluster_lock
ragnalog interactions cluster:jira-test         # mentions, co-bursts, concurrent jobs
ragnalog context jira-test_node1 ENTRY_ID --all-nodes    # every node, ±30 s
ragnalog report cluster:jira-test --open        # the cluster page
```
