# Design

Status: implemented 2026-10-29 (v0.1). See `requirements.md` for the why,
`research.md` for the library choices and section 11 for where the
implementation deviates from the original proposal.

## 1. Layout

```
atlas-log-analyzer/
├── pyproject.toml                 uv, Python 3.14, src layout, script `atlas-log`
├── docs/                          requirements, research, design, format specs
│   └── formats/                   entries.v1.md, groups.v1.md, logset.v1.md
├── src/atlas_log_analyzer/
│   ├── core/                      pure library — no I/O policy, no CLI, no web
│   │   ├── models.py              LogEntry, LogLevel, ExceptionInfo, Frame, SourceRef
│   │   ├── formats/               LogFormat protocol + registry
│   │   │   ├── base.py
│   │   │   ├── registry.py        get/detect/register formats
│   │   │   └── jira.py            Jira DC `atlassian-jira.log`
│   │   ├── java.py                Java stack-trace / Caused-by chain parsing (shared)
│   │   ├── reader.py              discover_sources (rotation order, digests), bytes → lines
│   │   ├── parser.py              lines → LogEntry (multi-line assembly)
│   │   ├── masking.py             MaskRule, Masker: message → (template, variables)
│   │   ├── signature.py           entry → Signature (hash + canonical text)
│   │   ├── grouping.py            SignedEntry, Group, Grouper: entries → group stats (streaming)
│   │   └── mining.py              optional Drain3 adapter proposing merges
│   ├── store/                     persistence of log sets
│   │   ├── schema.py              Arrow schemas + SCHEMA_VERSION
│   │   ├── logset.py              manifest, directory conventions, LogSet read API
│   │   │                          (groups/entries/counts/group/histogram/stats/sql)
│   │   ├── store.py               Store: list/open (typed errors), ingest, regroup, remove
│   │   ├── pipeline.py            ingest_paths/ingest_sources: discover → parse → sign → store
│   │   ├── writer.py              batches of entries/groups/merges → Parquet; Parquet → entries
│   │   └── query.py               diff() of two log sets
│   ├── report/                    self-contained HTML report (payload.py, render.py, assets/)
│   ├── cli/
│   │   ├── app.py                 cyclopts app, one function per command
│   │   └── output.py              json / jsonl / table rendering, exit codes
│   └── webapp/                    FastAPI app (docs/webapp.md); depends on core, store, report
└── tests/
    ├── fixtures/jira/             masked excerpts of real logs
    └── ...
```

Dependency direction: `cli` → `store` → `core`; `webapp` → `store` → `core`.
`core` depends only on the standard library (+ `xxhash`). `store` adds
`polars`/`pyarrow`/`duckdb`. `mining` is an optional extra (`drain3`).

## 2. Core models (`core/models.py`)

Frozen, slotted dataclasses. Everything is immutable and hashable so entries
can be deduplicated, cached and passed across threads/processes.

```python
class LogLevel(IntEnum): TRACE DEBUG INFO WARN ERROR FATAL UNKNOWN

@dataclass(frozen=True, slots=True)
class SourceRef:        # provenance
    path: str; line: int; offset: int           # 1-based line, byte offset of header

@dataclass(frozen=True, slots=True)
class Frame:
    location: str       # "com.foo.Bar.baz" (class.method)
    file: str | None; line: int | None
    module: str | None  # "java.base/" prefix if present

@dataclass(frozen=True, slots=True)
class ExceptionInfo:    # one element of a Caused-by chain
    type: str; message: str | None
    frames: tuple[Frame, ...]; omitted_frames: int      # "... N more"

@dataclass(frozen=True, slots=True)
class LogEntry:
    id: str                         # xxh3-64 hex of (format, ts, thread, logger, raw)
    format: str                     # registry key, e.g. "jira"
    timestamp: datetime             # tz-aware
    level: LogLevel
    thread: str
    logger: str
    message: str                    # first line of message
    body: str                       # full continuation text (may be "")
    exceptions: tuple[ExceptionInfo, ...]   # parsed chain, outermost first
    context: Mapping[str, str]      # format-specific: user, request_id, session, ip, url, request_url …
    source: SourceRef
    raw: str                        # exact original text (header + continuation)
    parse_error: str | None         # set when header was not recognised
```

`context` is an immutable mapping so new formats can add fields without
changing the schema; well-known keys are documented constants in
`core/formats/base.py` (`CTX_USER`, `CTX_REQUEST_ID`, …) and get their own
Parquet columns; the rest goes to a `context` map column.

## 3. Formats (`core/formats/`)

```python
class LogFormat(Protocol):
    name: str                       # registry key
    def is_header(self, line: str) -> bool            # cheap pre-check
    def parse_header(self, line: str) -> HeaderMatch | None
    def finish(self, header: HeaderMatch, continuation: list[str], source: SourceRef) -> LogEntry
    mask_rules: Sequence[MaskRule]  # format-specific variable tokens
    signature_spec: SignatureSpec   # what participates in the signature
```

`core/formats/jira.py` implements the Jira DC layout:

```
<ts> <thread[ url: <urls>]> <LEVEL> [<user>[ <reqid> <session> <ip> <url>]] [<logger>] <message>
```

with a single anchored regex (lazy `thread`/`context` spans, `LEVEL` and
`[logger]` as anchors), then a second small regex for the request context.
Exception chains are parsed by a shared `core/java.py` helper (`at …`,
`Caused by:`, `... N more`, `Suppressed:`), which `log4j.py` will reuse.

Registry: `formats.get("jira")`, `formats.detect(first_lines)`, entry points
group `atlas_log_analyzer.formats` so external packages can add formats.

## 4. Parsing (`core/reader.py`, `core/parser.py`)

- `iter_sources(paths) -> list[SourceFile]`: expands dirs, recognises
  rotation (`x.log`, `x.log.1` … oldest first by numeric suffix, falling back
  to mtime), computes size + xxh3 of each file for the manifest.
- `iter_lines(source) -> Iterator[Line]`: binary read, UTF-8 `replace`
  decode, yields `(line_no, offset, text)`.
- `parse(source, fmt) -> Iterator[LogEntry]`: first-line/continuation state
  machine. A line that looks like a header but fails `parse_header` becomes an
  entry with `parse_error` rather than being dropped. Leading continuation
  lines before the first header become one `parse_error` entry.
- `parse_many(sources, fmt)` chains sources in rotation order.

Throughput target: ≥ 50 MB/s for parsing alone on the sample data; ingest to
Parquet ≥ 15 MB/s.

## 5. Masking and signatures (`core/masking.py`, `core/signature.py`)

A `MaskRule(name, pattern, replacement)` replaces a variable token with a
placeholder such as `<ISSUE>`, `<NUM>`, `<IP>`, `<HEX>`, `<UUID>`, `<USER>`,
`<DURATION>`, `<TS>`, `<PATH>` (`<*>` is the generic placeholder). Rules are
ordered; format-specific rules run first, then generic ones. `Masker.apply()`
returns the template and the list of extracted variables, so the variables are
kept per entry (`variables` column) for later queries ("which issues failed?").

`SignatureSpec` says what goes into the hash:

| Component | Default | Note |
|---|---|---|
| format name | yes | |
| level | yes | |
| logger | yes | |
| masked message | yes | first line only |
| exception chain | types + masked messages | not frames |
| top app frames | 0 | configurable `frames=N` for a finer signature |

`Signature = (hash: str, template: str, parts: tuple[str, ...])`. The hash is
xxh3-64 hex (16 chars) of the canonical parts joined with `\x1f`, so it is
stable across runs, files, machines and versions of the tool as long as the
rule set is unchanged. The rule-set version is recorded in the manifest
(`signature_version`); a diff between sets with different signature versions
is refused unless `--force`.

Two signature granularities are stored per entry: `signature` (default spec)
and `coarse_signature` (format + level + logger + masked message only) so that
users can group either way without re-ingesting.

## 6. Grouping (`core/grouping.py`)

`Grouper` is a streaming aggregator keyed by signature hash:

```python
@dataclass
class Group:
    signature: str; coarse_signature: str; template: str
    level: LogLevel; logger: str; exception_type: str | None
    count: int; first_seen: datetime; last_seen: datetime
    users: Counter[str]; threads: Counter[str]; sources: Counter[str]
    examples: tuple[str, ...]        # up to N entry ids
```

Groups are computed at ingest and written to `groups.parquet`; they can also
be recomputed from `entries.parquet` with a different spec (`atlas-log
regroup`). The optional `mining.py` runs Drain3 over the distinct *templates*
(not the raw entries — far fewer rows) and emits `MergeProposal(cluster_id,
signatures, drain_template)` records that are stored in `merges.parquet` and
can be applied as a view ("merged groups") without changing the primary keys.

## 7. Storage format (`store/`)

A *log set* is a directory:

```
<store>/<logset-id>/
├── manifest.json        logset.v1: id, name, format, sources[{path,size,xxh3,first_ts,last_ts}],
│                        time_range, entry_count, tool_version, schema_version, signature_version, created_at
├── entries.parquet      entries.v1 (one row per entry, zstd, row groups ~64k)
├── groups.parquet       groups.v1 (one row per signature)
└── merges.parquet       merges.v1 (optional, from `mine`)
```

`logset-id` defaults to xxh3 of the sorted source hashes, so re-ingesting the
same files is idempotent; `--name` gives a human alias stored in the manifest.

`entries.v1` columns: `id, ts (timestamp[ms, UTC]), tz_offset (i16 minutes),
level (u8 + dictionary), thread, logger, message, body, user, request_id,
session, ip, url, request_url, context (map<str,str>), exception_type,
exception_message, exception_chain (list<struct>), signature,
coarse_signature, template, variables (list<str>), source_path, source_line,
source_offset, raw, parse_error`.

Schemas are declared once in `store/schema.py` as pyarrow schemas and
exported by `atlas-log schema` as JSON for agents. `SCHEMA_VERSION` bumps on
breaking changes; readers refuse newer majors.

Dedup: when a set is built from files that overlap (snapshots), entries with
an identical `id` are written once. Cross-set comparison never needs the raw
logs — only `groups.parquet` of both sets.

## 8. CLI (`atlas-log`)

Global options: `--output table|json|jsonl` (default `table` on a TTY, `json`
otherwise), `--quiet`, `--store DIR` (env `ATLAS_LOG_STORE`, default
`./.atlas-log`).

| Command | Purpose |
|---|---|
| `ingest PATH... [--format jira] [--name N] [--mine]` | Build or update a log set; prints manifest. |
| `sets` | List log sets in the store. |
| `groups SET [--level ERROR] [--logger X] [--since/--until] [--sort count/last_seen] [--limit N] [--coarse]` | Group table. |
| `show SET SIGNATURE [--limit N] [--raw]` | Entries of one group, with variables. |
| `entries SET [filters…] [--limit N]` | Entry listing with the same filters. |
| `query SET "SQL"` | DuckDB SQL over `entries`/`groups` views. |
| `stats SET` | Counts per level/logger/hour, time range, sources. |
| `diff SET_A SET_B [--level ERROR]` | New / vanished / changed-count signatures. |
| `parse PATH... [--format jira]` | One-shot stream to JSONL, no store. |
| `regroup SET [--frames N]` | Recompute groups with another signature spec. |
| `formats`, `schema [entries/groups/logset]` | Introspection for agents. |

Exit codes: 0 ok, 1 usage error, 2 nothing found / parse failure, 3 store
error. JSON errors are `{"error": {"code": ..., "message": ...}}` on stdout;
diagnostics on stderr. Every JSON document carries `"schema": "<name>.v1"`.

MCP later: each command maps 1:1 onto a tool with the same JSON schema; the
CLI functions are thin wrappers over `store`/`core` calls that the MCP server
will reuse directly.

## 9. Web app

`src/atlas_log_analyzer/webapp/` (optional extra `[web]`, `atlas-log serve`)
exposes the store over FastAPI with the CLI's JSON documents; see
`docs/webapp.md`. Nothing in `core`/`store`/`report` may import from `cli` or
`webapp`.

The web app uses only the stable read API: `Store.list()`, `Store.open(ref)`
(raises `LogSetNotFound` / `AmbiguousLogSet`, both `StoreError`; manifests are
cached per `Store` instance and re-read when their file changes) and on `LogSet`:
`groups(..., limit, offset)`, `count_groups(...)`, `group(signature, coarse=)`,
`entries(..., limit, offset)`, `count_entries(...)`, `entry(id)`,
`histogram(bucket, ...)` (epoch-aligned integer buckets, no tz database),
`stats()` and `sql(query, max_rows=)` (file access limited to the set's Parquet
files, configuration locked). `level`/`min_level` accept a `LogLevel`, a name,
a comma-separated string or an iterable. Uploads go through
`store.pipeline.ingest_paths(..., progress=)`, which reports `IngestProgress`.

## 10. Quality

- ruff (lint + format), mypy strict on `core` and `store`, pytest with masked
  fixtures (`tests/fixtures/jira/*.log`) and a throughput smoke test.
- GitHub Actions: lint, type-check, tests on `windows-latest` and
  `ubuntu-latest`, Python 3.14.

## 11. Implementation notes and deviations (v0.1)

- **Jira header context.** Besides the request context, Jira writes a
  scheduled-service name after the user (`anonymous    Mail Queue Service`);
  it is stored under the context key `service` (`CTX_SERVICE` in
  `formats/jira.py`). `url:` segments may carry `; user: X`, which is stripped;
  the IP field may be a comma-separated list; `-` means no user.
- **`is_header` is the full regex.** Message bodies can start with a timestamp,
  so a timestamp-only pre-check would create false headers. The full match is
  still ~40 MB/s on the sample data; parse + sign + group runs at ~16 MB/s and
  ingest to Parquet end to end at ~10 MB/s (712k entries from 191 MB in 18 s).
- **Signatures.** Hashes are over UTF-8 bytes (`xxhash` 4 no longer hashes
  `str`). When an exception message's first line ends with `:` (Groovy
  `startup failed:`), the next non-empty line joins the signature so different
  compile errors stay apart. `Suppressed:` blocks are skipped; only the first
  exception chain in a body is parsed.
- **Masking.** A generic `user` rule masks the identifier after
  `uid:`/`user=`/`userName:`/`sAMAccountName:`/`login:`; without it a custom
  LDAP-sync plugin produced 4,000 single-user groups. `Masker` caches results
  (`cache_size`).
- **Store.** `regroup` rewrites `entries.parquet` as well as `groups.parquet`
  (signature, template and variables change) and drops `merges.parquet`.
  `ingest` recomputes groups from the stored entries if the grouper's counts do
  not add up to the deduplicated entry count. `ingest` with an existing id
  returns the existing set without consuming the input iterator.
- **CLI.** Extra commands `remove` and `version`; `formats --sample FILE`
  reports sniff confidence per format; `parse` always emits JSONL; an empty
  listing exits 0.
- **Windows.** `tzdata` is a dependency on `win32`; polars/pyarrow cannot
  convert UTC timestamps to Python objects without it.
