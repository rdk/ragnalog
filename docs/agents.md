# ragnalog for agents

How a coding or ops agent drives `ragnalog`: the workflow, the JSON conventions and a
cookbook of common questions. The command reference is [`cli.md`](cli.md); every document's
JSON Schema is in [`formats/`](formats/).

## Workflow

Start with `ragnalog manifest` (bare `ragnalog` prints the same when stdout is
not a terminal): about 10 KB of JSON with this workflow, every command's usage,
output document and exit codes, one-line help per parameter, the `--brief`
presets and where the document schemas are (`ragnalog schema triage.v1`).
`ragnalog manifest COMMAND` gives one command in full (every parameter with its
type, default and help) when you are about to run it.

```sh
ragnalog manifest                                           # 0. how to use the tool (manifest.v1)
ragnalog ingest LOGS/ --name today                          # 1. build the set (JSON manifest)
ragnalog triage today                                       # 2. verdict vs the previous set
ragnalog groups today --min-level ERROR --limit 20 --brief # 3. groups: check "total"/"truncated"
ragnalog groups today --with-exception --brief              #    stack traces at any level
ragnalog show today SIGNATURE --brief --limit 5             # 4. drill into one group
ragnalog frames today SIGNATURE                             #    its stack trace, plugin frames first
ragnalog context today ENTRY_ID --before 10 --brief         #    what the thread did around it
ragnalog entries today --request-id REQUEST_ID --brief      #    the whole HTTP request
ragnalog entries today --signature SIGNATURE --since 2h --raw    #    the raw text
ragnalog diff yesterday today --rate --limit 20             # 5. compare with another set
```

Repeat 3–4; on an error, read `error.hint` for the next step. `SIGNATURE`
may be a unique prefix (4+ hex characters); `ragnalog sets --brief` lists the
sets with their error and warning counts, `ragnalog set SET` shows one.

Every call pays the start-up of the CLI (about 0.45 s). For several commands in
a row, `ragnalog batch` reads one JSON array of arguments per line on stdin
and writes one JSON document per line (`error.v1` for a failed line, which
never stops the batch):

```sh
printf '%s\n' '["groups", "today", "--brief", "--limit", "5"]' '["show", "today", "83d1"]' \
  | ragnalog --store "$RAGNALOG_STORE" batch
```

## Conventions

- Set `RAGNALOG_STORE` (or pass `--store DIR`) to a data directory (the folder that keeps the log sets) outside any
  repository; the implicit `./.ragnalog` is refused inside a git work tree.
- No data directory here? Point the CLI at a ragnalog web app instead:
  `RAGNALOG_SERVER=https://logs.example.org RAGNALOG_TOKEN=...` and the same commands
  (reads, `query`, `triage`, `batch`, ...) print the same documents from the server's sets
  (`cli.md`, "Remote mode").
- Output is JSON whenever stdout is not a terminal; force it with
  `--output json` (one document) or `--output jsonl` (one row per line).
- Every document has `"schema": "<name>.v1"`; listings put rows in `"items"`.
- `groups` and `entries` page with `--limit N --offset M`: `"truncated": true`
  means more rows follow; fetch the next page with `--offset <offset + count>`.
- Keep rows small: `--brief` (a documented column preset) or
  `--columns a,b,c` on `groups`, `entries`, `show` and `context`.
- Level filters hide DEBUG stack traces; `--with-exception` (any level with an
  exception) and `--exception TEXT` (substring of `exception_type`) do not.
- `frames SET SIGNATURE|ENTRY_ID` reduces a stack trace to its application
  frames (JDK and Jira platform packages skipped; set `RAGNALOG_APP_PACKAGES=
  com.acme.,org.example.` to name your own packages instead).
- Errors: `{"schema": "error.v1", "error": {"code": ..., "message": ...}}` on
  stdout (`"reason"`, when present, is the same in the CLI and the web API, e.g.
  `set_not_found`); exit codes `0` ok, `1` usage, `2` nothing found / parse failure,
  `3` store error (data directory). Diagnostics go to stderr (`--quiet` silences them).
- Long entries are cut in JSON: `raw` and `body` keep 4000 characters
  (`--max-raw N`, `0` = all); cut rows carry `"body_truncated": true` and
  `"raw_chars"`. `--raw` (plain text) is never cut unless `--max-raw` is given.
- `ragnalog schema` describes the stored tables (`entries`, `groups`,
  `merges`, `logset`) field by field; `ragnalog schema NAME.v1` prints the JSON
  Schema of a document (`triage.v1`, `error.v1`, …; also
  `docs/formats/NAME.v1.schema.json`); `ragnalog COMMAND --help` documents the
  JSON shape and exit codes of each command.

```sh
ragnalog --output json ingest atlassian-jira.log --name today
ragnalog groups today --min-level ERROR --limit 20 | jq '.items[] | {signature, count, template}'
ragnalog query today "select level_name, count(*) n from entries group by 1 order by 2 desc"
ragnalog query today "select unnest(variables) v, count(*) n from entries
                       where signature = '83d1828c6afb2ead' group by 1 order by 2 desc"
ragnalog query today "select hour(ts) h, count(*) from entries where level >= 50 group by 1 order by 1"
ragnalog parse atlassian-jira.log --limit 100       # JSONL entries, no store
```

`query --help` lists the columns of the `entries`, `groups` and `merges`
views; a misspelt column gets the candidate columns in `error.hint`.

## Cookbook

Five common questions and the commands that answer them. `before`/`after` are
set names given at `ingest --name`; `SIG` is a 16-hex group signature from
`groups` (or a unique prefix of 4+ characters), `ENTRY` an entry id from
`entries`/`show`, `REQ` a Jira request id.

**1. What broke after the deploy?**

```sh
ragnalog triage after --baseline before             # verdict, new types, new exceptions, growth
ragnalog diff before after --min-level WARN --rate --limit 20    # top 20 per section, changed by hourly rate
```

Without `--baseline`, triage picks one itself: another set of the same format
and recipe that is not a superset or subset of this one's files, not later,
not tiny and of comparable span (`baseline.reason` says why it was chosen,
`baseline.candidates` which sets were not and why). After a tool update that
changes signatures, `ragnalog sets` marks older sets `"stale": true`;
`ragnalog upgrade --all` rebuilds them (about 5 s per 700k entries).

Read `verdict`, then `new` and `new_exceptions` (stack traces below the level
filter). If `warnings` is not empty the two sets cover very different spans
and `new`/`vanished` are unreliable: compare sets of similar length.

**2. What is in this stack trace?**

```sh
ragnalog frames after SIG                           # the chain with application frames first
ragnalog frames after SIG --all                     # every frame, with an "app" flag
ragnalog show after SIG --raw --limit 1 --max-raw 0    # the full original text of one entry
```

**3. Which request or thread triggered it?**

```sh
ragnalog entries after --signature SIG --columns id,ts,thread,user,request_id --limit 5
ragnalog entries after --request-id REQ --brief     # everything that request logged
ragnalog context after ENTRY --before 20            # what the thread did just before
ragnalog context after ENTRY --any-thread --before 5 --after 5    # what else happened then
```

**4. Is it gone after the fix?**

```sh
ragnalog ingest logs-after-fix/ --name fixed
ragnalog triage fixed --baseline after              # SIG should be in "vanished"
ragnalog diff after fixed --rate --limit 0 | jq '[.vanished[], .changed[]] | map(select(.signature == "SIG"))'
ragnalog groups fixed --exception NullPointer --brief   # any group with that exception left?
```

Let the fixed set cover a comparable span (see `coverage` / `warnings`);
a group that only occurs at night is "vanished" from a daytime-only set.

**5. Which issues or users are affected?**

```sh
ragnalog show after SIG --limit 20 | jq '.items[] | {ts, user, variables}'
ragnalog query after "select unnest(variables) v, count(*) n from entries
                       where signature = 'SIG' group by 1 order by 2 desc"
ragnalog query after "select user, count(*) n from entries
                       where signature = 'SIG' group by 1 order by 2 desc"
ragnalog groups after --contains 'issue' --columns signature,count,top_users
```

`show` returns each entry's `variables` (the values masking replaced: issue
keys, numbers, ids); the first `query` counts them over the whole group, the
second counts users; `top_users` in `groups` is the top-10 list per group.

The data directory's Parquet files (layout in the [README](../README.md#how-it-works)) can also be
read directly with DuckDB, polars or pandas.
