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
│   │   ├── query.py               diff() of two log sets, span/coverage helpers
│   │   └── triage.py              triage.v1 (verdict, new/grew/vanished) and the auto baseline
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
`context(id, before=, after=, same_thread=)` (entries around one entry),
`histogram(bucket, ...)` (epoch-aligned integer buckets, no tz database),
`stats()` and `sql(query, max_rows=)` (file access limited to the set's Parquet
files, configuration locked). `level`/`min_level` accept a `LogLevel`, a name,
a comma-separated string or an iterable. Group and entry filters also take
`with_exception=`/`exception=` (and `contains=` on groups, a template
substring); entry filters take `request_id=`. Uploads go through
`store.pipeline.ingest_paths(..., progress=)`, which reports `IngestProgress`.

## 10. Quality

- ruff (lint + format), mypy strict on `core` and `store`, pytest with masked
  fixtures (`tests/fixtures/jira/*.log`) and a throughput smoke test.
- GitHub Actions: lint, type-check, tests on `windows-latest` and
  `ubuntu-latest`, Python 3.14.
- **Golden signatures** (`tests/golden/signatures.json`): per fixture file, an
  xxh3 of its sorted `(signature, coarse_signature)` pairs and the entry count,
  keyed by `SIGNATURE_VERSION`, `MASK_RULES_VERSION` and the format's spec. A
  change that moves a signature fails; if intended, bump a version and run
  `uv run pytest --update-golden`.
- **Format conformance kit** (`tests/core/test_conformance.py`), run for every
  registered format over `tests/fixtures/<format>/`: joined `raw` texts give back
  the file (minus `\r` before `\n` and the final newline), ids are unique,
  `source_line`/`source_offset` point at the header line, parsing is
  deterministic, `raw == header + "\n" + body`, `sniff` ≥ 0.5 on the format's
  own fixtures and 0 on foreign text (`tests/fixtures/foreign/`). Jira's `sniff`
  is the share of headers among lines that could start an entry: indented frames,
  `Caused by:` and exception lines don't count against it, and no header scores 0.
- **Startup guard** (`tests/cli/test_startup.py`): a JSON read command loads no
  DuckDB, `pyarrow.compute`, `rich.table`, ingest pipeline or Parquet writer;
  `version` and `formats` load no polars. `store/__init__.py` resolves its names
  on first use (PEP 562), and the version is a literal (`_version.py`).

### CLI and API parity

`tests/contract/test_parity.py` is the written contract between
`atlas-log --output json` and `/api`: its `CASES` table pairs a command line with
a request; both run against one store and must return the same document once
volatile keys (`created_at`, `elapsed_s`, `tool_version`, `report_url`) are
dropped. Differences by design are listed per case in `allowed`, each with its
reason (the API names only the store directory and adds per-set `summary`; `show`
is `entries.v1` while the API's group page gets `group.v1` with a histogram; a cut
text says `use --max-raw 0` or `use max_raw=0`). `HTTP_CAPS` lists the page caps
only the API has. Errors must agree on `error.code`, with the documented CLI exit
code and HTTP status. Known drifts are `xfail(strict=True)` cases: fixing one
makes its case fail until the marker is removed.

## 11. Implementation notes and deviations (v0.1)

- **Jira header context.** Besides the request context, Jira writes a
  scheduled-service name after the user (`anonymous    Mail Queue Service`);
  it is stored under the context key `service` (`CTX_SERVICE` in
  `formats/jira.py`). `ServiceRunner` in the user slot is the scheduler, not a
  user: it becomes the `service` (or the service name after it) and no user is
  set, so it does not show up in `top_users`. `url:` segments may carry
  `; user: X`, which is stripped; the IP field may be a comma-separated list;
  `-` means no user.
- **`is_header` is the full header match.** Message bodies can start with a
  timestamp, so a timestamp-only pre-check would create false headers.
  `HEADER_RE` defines a header; `match_header` returns the same groups in
  linear time (the regex backtracks quadratically on long timestamp-led lines
  that are no header: 36 s for 200 KB). It is checked against the regex on all
  780k sample lines and on random lines (tests). Parse + sign + group runs at
  ~16 MB/s and ingest to Parquet end to end at ~10 MB/s (712k entries from
  191 MB in 18 s).
- **Signatures.** Hashes are over UTF-8 bytes (`xxhash` 4 no longer hashes
  `str`). When an exception message's first line ends with `:` (Groovy
  `startup failed:`), the next non-empty line joins the signature so different
  compile errors stay apart. `Suppressed:` blocks are skipped; only the first
  exception chain in a body is parsed. Mangled frame lines (characters lost
  when a log is copied out of a terminal or chat: an indented `at …` that does
  not parse, or a line ending like `…(Foo.java:64)`) are skipped instead of
  ending the chain; on the 780k sample entries this changed no chain.
- **Masking.** A generic `user` rule masks the identifier after
  `uid:`/`user=`/`userName:`/`sAMAccountName:`/`login:`; without it a custom
  LDAP-sync plugin produced 4,000 single-user groups. `Masker` caches results
  (`cache_size`).
- **Store.** `regroup` rewrites `entries.parquet` as well as `groups.parquet`
  (signature, template and variables change) and drops `merges.parquet`.
  The ingest pipeline drops repeated entry ids before the grouper
  (`unique_entries`) and writes with `dedupe=False`, so one id set is held and
  group counts always match; `Store.ingest` still recomputes groups from the
  stored entries if a caller's grouper counts do not add up (that path doubled
  peak memory: 1.3 GB instead of 0.45 GB for the 191 MB sample). Row groups are
  flushed every 65,536 rows or 32 M characters of `raw` + `body`, whichever
  comes first. `ingest` with an existing id returns the existing set without
  consuming the input iterator.
- **Store concurrency.** A `Store` may be shared by threads: a small lock
  guards the manifest cache (manifests load outside it), and a write lock is
  held only for the name check and the rename that installs a set, so a long
  ingest blocks no reader. The name is checked before parsing and again under
  the lock; a set with the same id installed meanwhile is returned instead of
  replaced. The write lock is also an OS file lock on `<store>/.write.lock`
  (`msvcrt.locking` / `fcntl.flock`, `store/locks.py`), which the OS releases
  when a process dies, so a CLI ingest next to `serve` cannot take the same
  name at the same moment. Renames and deletes of set directories retry on
  `PermissionError` (Windows refuses them while a reader has a file open);
  `remove` renames the set to `.trash-*` first, so it disappears at once and a
  failure leaves it intact. The web app holds `<store>/.server.lock` (one
  server per store).
- **CLI.** Extra commands `remove` and `version`; `formats --sample FILE`
  reports sniff confidence per format; `parse` always emits JSONL; an empty
  listing exits 0. Added for agents: `triage` (the web app's `triage.v1`,
  built in `store/triage.py`), `context` (entries around one entry),
  `frames` (application frames of a stack trace; library prefixes in
  `core/java.py`), paging (`--offset`), `--brief`/`--columns`, `--max-raw`,
  exception and request filters, `diff --rate` and coverage warnings. The
  implicit `./.atlas-log` is not created inside a git work tree.
- **Windows.** `tzdata` is a dependency on `win32`; polars/pyarrow cannot
  convert UTC timestamps to Python objects without it.
