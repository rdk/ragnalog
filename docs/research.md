# Library research

Date: 2026-10-29. All version/compatibility claims below were verified by
resolving and importing the packages in a uv-managed CPython 3.14.2 venv on
Windows, and by querying PyPI metadata; the throughput and clustering numbers
come from probes run against `logs_01/atlassian-jira.log.3` (21 MB, 250k lines).

## 1. Template mining ("same error, different variable parts")

| Library | Version / date | Verdict |
|---|---|---|
| [Drain3](https://github.com/logpai/Drain3) (`drain3` on [PyPI](https://pypi.org/project/drain3/)) | 0.9.11, 2022-07-17, sdist only, pure Python | Works on 3.14. Online Drain algorithm with masking, persistence (file/Kafka/Redis), `jsonpickle` dependency. Unmaintained for 3+ years. On the sample: 37k headers → 25 clusters in 0.3 s; correctly yields `Script field failed on issue: <*> field: Finished On`. |
| [logparser3](https://pypi.org/project/logparser3) (LogPAI toolkit) | 1.0.4, 2023-09-14 | Benchmark toolkit bundling Drain, Spell, IPLoM, LenMa, Brain… Designed for offline batch benchmarking on CSV; pins an old `regex`; heavy (pandas) and not an API you want to depend on. Useful as a reference for algorithms only. |
| [Nosparser](https://pypi.org/project/Nosparser) | niche | Not considered. |

Observations that drive the design:

- Drain operates on *one line*. Jira entries are dominated by multi-line
  payloads (215k continuation lines vs 37k headers in the probe file). Drain
  merges all 666 "exception thrown trying to dispatch event `<*>`" entries into
  one cluster regardless of the `Caused by:` root cause. Any grouping that
  matters for Jira has to look at the exception chain, which no library does
  for us.
- Drain's clusters are order-dependent and can drift between runs; cluster ids
  are not stable identifiers. That makes them unsuitable as the *primary* key
  for cross-snapshot comparison, but fine as a *merge proposal* layer.
- Drain's real value is catching variable tokens our rules did not anticipate.
  With a good masking layer in front of it (which Drain3 itself recommends), the
  remaining work it does is small.

**Decision:** primary grouping is deterministic — mask known variable tokens
(format-specific rules), canonicalise, hash together with level, logger and
the exception signature. Drain3 is an optional extra (`atlas-log-analyzer[mine]`)
used by a `mine` pass that proposes merging signatures whose templates Drain
puts in the same cluster. The Drain algorithm is small enough that if the
dependency becomes a problem we can vendor a ~300-line implementation.

## 2. Parsing

No maintained Python library parses log4j/logback multi-line entries well;
log shippers (Fluent Bit, Promtail, Logstash) all solve it with a "first-line
regex + continuation" state machine, which is what we implement.

Probe: a pure-Python generator (`open(path, 'rb')` → decode → `re.match` on a
compiled header regex, cheap `line[:2] == '20'` pre-check) processes the sample
at **~100 MB/s** (21 MB in 0.20 s). Parsing is therefore not the bottleneck;
object construction and serialisation are. Consequences:

- No Rust/C extension or polars-native string parsing needed for v1.
- Keep the hot loop lean: build entries lazily, avoid per-line `datetime`
  parsing where a cached parser or batch conversion can be used, and batch
  rows into Arrow/Parquet instead of per-row writes.
- Encoding: read bytes, decode UTF-8 with `replace`; Jira logs contain
  occasional non-UTF-8 bytes.

Header pitfalls found in the data (handled by anchoring the regex on
`LEVEL … [logger]` with lazy thread/context spans instead of whitespace
tokenisation):

- thread names with spaces: `RMI TCP Connection(123)-10.0.1.2`,
  `JFR Event Stream 1`, `bigbrassband-gitplugin-reindex-queue:thread - 1`,
  `ThreadPoolAsyncTaskExecutor::Thread 12`;
- user field `Anonymous user`, `anonymous`, `-`, real usernames;
- optional ` url: /path, /other` segment after the thread;
- request context `user reqid session ip url` only on HTTP threads;
- message bodies that begin with a timestamp (mail queue items) and would be
  mistaken for a header by a timestamp-only first-line rule.

## 3. Storage

| Option | Verdict |
|---|---|
| Parquet via [polars](https://pypi.org/project/polars) 2.1.0 (2026-10-25) / [pyarrow](https://pypi.org/project/pyarrow) 26.0.0 (cp314 wheels) | **Chosen.** Columnar, compressed (expect ~10× smaller than raw), zero-copy into polars/DuckDB, trivially served by a later web app. |
| [DuckDB](https://pypi.org/project/duckdb) 2.0.0 (2026-10-21, `cp312-abi3` wheel, imports on 3.14) | **Chosen for queries/diffs.** Reads Parquet directly; gives agents an `atlas-log query "<SQL>"` escape hatch. |
| SQLite | Simple but row-oriented; scans over millions of rows and string-heavy columns are slow and the file is much larger. |
| JSONL | Kept as the *streaming interchange* format (`parse --output jsonl`), not as the store. |

Note: polars 2.x is a fresh major (2.0.0 in Oct 2026). Pin `polars>=2,<3` and
keep storage code in one module (`store/`) so the dependency can be swapped
for pyarrow-only if needed.

## 4. Models and serialisation

| Option | Verdict |
|---|---|
| `dataclasses` (frozen, slots) | **Chosen for core models.** Zero dependencies, fastest construction in the hot loop, clean public API. |
| [msgspec](https://pypi.org/project/msgspec) 0.22.0 | Fastest JSON encode/decode; used for the CLI's JSON/JSONL output and for schema export. |
| [pydantic](https://pypi.org/project/pydantic) 2.14.1 | Deferred to the FastAPI layer where it belongs; too slow for per-entry construction. |
| [xxhash](https://pypi.org/project/xxhash) 4.0.2 | 64-bit non-cryptographic hashing for entry ids and signatures (stable, fast, short hex). |

## 5. CLI

| Option | Verdict |
|---|---|
| [cyclopts](https://cyclopts.readthedocs.io/) 5.3.2 (2026-10-27) | **Chosen.** Pure type-annotation driven, supports `Literal`/`Union`/dataclass parameters, own parser (no Click), good `--help` from docstrings — exactly what agents read. [Typer comparison](https://cyclopts.readthedocs.io/en/latest/vs_typer/README.html). |
| [typer](https://pypi.org/project/typer) 0.27.3 | Fine, but Click-based and weaker on complex types. |
| [rich](https://pypi.org/project/rich) | Human-mode tables only; never on stdout in machine mode. |

## 6. Related tools looked at for conventions

- Fluent Bit / Promtail multiline docs (first-line + continuation regexes):
  <https://docs.fluentbit.io/manual/pipeline/filters/multiline-stacktrace>,
  <https://oneuptime.com/blog/post/2026-01-21-promtail-multiline/view>
- DuckDB + polars integration guide: <https://duckdb.org/docs/current/guides/python/polars>
- A Python log4j parser pattern (regexes for frames / `Caused by` / `... N more`):
  <https://glama.ai/mcp/servers/@Fato07/log-analyzer-mcp/blob/bcf0b3804b5dc52ff80b8a3439bb78e05a7b7b56/codesdevs_log_analyzer/parsers/java.py>
