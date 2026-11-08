"""Commands about the tool and the store.

``formats``, ``schema``, ``manifest``, ``remove``, ``serve``, ``version`` and ``batch``.
"""

from __future__ import annotations

import io
import json
import os
import sys
from pathlib import Path

from atlas_log_analyzer.cli import context as ctx
from atlas_log_analyzer.cli.output import (
    OpError,
    Output,
    encode_json,
)
from atlas_log_analyzer.config import Settings
from atlas_log_analyzer.service import ingest as service_ingest
from atlas_log_analyzer.service import meta

# polars, DuckDB, rich and the ingest pipeline are imported by the code that needs them.

__all__ = ["batch", "emit_manifest", "formats_", "manifest", "remove", "schema", "serve", "version"]


def formats_(*, sample: Path | None = None) -> None:
    """List the supported log formats.

    Output (json):
      {"schema": "formats.v1", "count": int,
       "items": [{"name", "description", "signature_spec", "mask_rules": [str]}]}
    With --sample FILE each item also has "confidence" (0..1) from sniffing the
    first 200 lines of FILE, and items are sorted by it.

    Exit codes: 0 ok; 1 --sample is a directory; 2 sample file not found.

    Args:
        sample: A log file to test every format against.

    """
    doc = meta.formats(sample)
    ctx.out().document(
        doc, columns=("name", "description", "confidence", "signature_spec"), title="formats"
    )


def schema(name: str | None = None, /) -> None:
    """Print the JSON description of a stored table or the JSON Schema of a document.

    "entries", "groups" and "merges" are the Parquet tables (also the views
    available to `query`); "logset" is manifest.json. A document name such
    as "triage.v1" prints the JSON Schema (draft 2020-12) of that document,
    as in docs/formats/<name>.schema.json; rows are described by the tables'
    columns, all optional. Without NAME the four tables are printed and
    "documents" lists the document names.

    Output (json):
      {"schema": "schema.v1", "definitions": {<name>: <schema description>, ...},
       "documents": [str, ...]}
    "documents" only without NAME. In table mode the same document is
    pretty-printed as JSON.

    Exit codes: 0 ok; 1 unknown NAME.

    Args:
        name: A table (entries, groups, merges, logset) or a document, e.g. triage.v1.

    """
    doc = meta.schema(name, front="cli")
    out = ctx.out()
    if out.mode == "table":
        out.write_line(encode_json(doc, indent=2))
        return
    out.document(doc, items=None)


def manifest(command: str | None = None, /) -> None:
    """Print how an agent uses atlas-log: workflow, commands and documents (about 10 KB).

    The workflow in order with a one-line purpose per step, when to use the
    other commands, every command (usage with types and defaults, output
    document, exit codes), one-line help per parameter, the documents
    (`atlas-log schema NAME`), the --brief presets, the environment variables
    and the global options. Bare `atlas-log` prints the same document when
    stdout is not a terminal; GET /api/manifest of the web app too.
    `manifest COMMAND` prints one command in full instead: its --help text and
    every parameter with type, default and help (GET /api/manifest/COMMAND).

    Output (json):
      {"schema": "manifest.v1", "tool", "version", "usage", "notes",
       "workflow": [{"run", "purpose"}], "when": [{"run", "purpose"}],
       "commands": {<name>: {"summary", "usage", "output", "exit": [int]}},
       "help": {<parameter>: str}, "global_options", "exit_codes",
       "documents": {"get", "names"}, "presets", "env"}
      {"schema": "manifest-command.v1", "command", "version", "usage",
       "description", "output", "exit": [int],
       "params": [{"name", "type", "default", "help"}]}
    In table mode the same document is pretty-printed as JSON.

    Exit codes: 0 ok; 2 unknown COMMAND.

    Args:
        command: One command to describe in full (default: the whole manifest).

    """
    emit_manifest(ctx.out(), command)


def emit_manifest(out: Output, command: str | None = None) -> None:
    """Write ``manifest.v1``, or ``manifest-command.v1`` of ``command``."""
    from atlas_log_analyzer.service import manifest as service_manifest

    if command is None:
        full = service_manifest.manifest_document()
    else:
        full = service_manifest.command_document(command)
    if out.mode == "table":
        out.write_line(encode_json(full, indent=2))
        return
    schema = str(full["schema"])
    out.emit(schema, {k: v for k, v in full.items() if k != "schema"}, items=None)


def remove(logset: str, /) -> None:
    """Delete a log set from the store (its directory and all files).

    Output (json): {"schema": "remove.v1", "removed": id, "name": str | null}

    Exit codes: 0 ok; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.

    """
    doc = service_ingest.remove(ctx.store(), logset, front="cli")
    ctx.out().info(f"removed log set {doc['removed']}")
    ctx.out().document(doc, items=None)


def serve(
    *,
    host: str = "127.0.0.1",
    port: int = 8321,
    token: str | None = None,
    max_upload_mb: int | None = None,
    allowed_host: str | None = None,
    reload: bool = False,
) -> None:
    """Run the web app over the store: browse, compare and triage sets, upload new logs.

    Pages: / (sets with triage verdicts and upload), /sets/SET (interactive
    report, ?compare=auto|SET), /sets/SET/triage. JSON API under /api with
    the same documents as this CLI (OpenAPI at /docs). Needs the `web` extra.

    Output: server log on stderr until interrupted (Ctrl+C); nothing on stdout
    ("schema": none).

    Exit codes: 0 stopped normally; 1 the `web` extra is not installed or bad
    option; 3 store error (including the default ./.atlas-log not existing inside
    a git work tree: pass --store DIR or set ATLAS_LOG_STORE, and another server
    already using the store: one server per store).

    Args:
        host: Interface to bind; use 0.0.0.0 to serve the network (set --token then).
        port: TCP port.
        token: Require `Authorization: Bearer TOKEN` on /api (env ATLAS_LOG_TOKEN).
        max_upload_mb: Largest accepted upload in MiB (env ATLAS_LOG_MAX_UPLOAD_MB;
            default 2048).
        allowed_host: Comma-separated host names a loopback server also answers, e.g.
            the public name behind a reverse proxy that passes Host through (env
            ATLAS_LOG_ALLOWED_HOSTS; on loopback every other name is refused).
        reload: Restart on code changes (development); the settings above reach the
            reloading process through the ATLAS_LOG_* environment variables.

    """
    try:
        import uvicorn

        from atlas_log_analyzer.webapp import create_app
    except ImportError as exc:
        raise OpError(
            "missing_dependency",
            f"the web app needs the 'web' extra ({exc.name or exc}); "
            "install it with: uv sync --extra web  (or pip install 'atlas-log-analyzer[web]')",
        ) from exc
    if not 0 < port < 65536:
        raise OpError("invalid_value", "--port must be between 1 and 65535")
    if max_upload_mb is not None and max_upload_mb < 1:
        raise OpError("invalid_value", "--max-upload-mb must be >= 1")
    store = ctx.store(create=True)  # uploads create the store directory
    flags = {
        "store": store.root,
        "max_upload_mb": max_upload_mb,
        "token": token,
        "allowed_hosts": allowed_host,
    }
    settings = Settings.load(flags, os.environ)
    from atlas_log_analyzer.webapp.state import lock_server

    if (probe := lock_server(settings.store)) is not None:  # a clear error if already served
        probe.release()
    ctx.out().info(f"serving {settings.store.as_posix()} at http://{host}:{port}/")
    from atlas_log_analyzer.webapp.app import served_hosts

    if served_hosts(host, None) is None and not settings.token:
        ctx.out().warn(
            f"{host} is reachable from the network and no --token is set: anyone who can "
            "reach this port can read, upload and delete log sets (pass --token, or set "
            "ATLAS_LOG_TOKEN)"
        )
    if reload:
        # the reloading child builds its settings from the environment alone
        os.environ.update(settings.to_env())
        uvicorn.run(
            "atlas_log_analyzer.webapp.app:create_app",
            factory=True,
            host=host,
            port=port,
            reload=True,
        )
        return
    hosts = served_hosts(host, settings.allowed_hosts)
    app = create_app(settings, warm=True, hosts=hosts)
    uvicorn.run(app, host=host, port=port, log_level="info")


def version() -> None:
    """Print tool, schema and signature versions.

    Output (json):
      {"schema": "version.v1", "version": str, "schema_version": str,
       "signature_version": int, "mask_rules_version": int, "python": str}

    Exit codes: 0 ok.
    """
    ctx.out().document(meta.version(), items=None, title="atlas-log")


BATCH_REFUSED = frozenset({"batch", "parse", "serve"})


def batch() -> int:
    """Run many commands in one process: argv arrays in, one JSON document per line out.

    Reads JSON Lines from stdin, one command per line as a JSON array of its
    arguments without "atlas-log" and without global options, e.g.
      ["groups", "prod", "--brief", "--limit", "5"]
      ["show", "prod", "0a1b2c3d"]
    Each runs in this process with the global --store (and the other global
    options) and --output json, and writes exactly one JSON document per line
    to stdout, in input order: its usual document, or error.v1 when it fails
    (a line that is not a JSON array of strings, a global option, --help, or
    batch/parse/serve, which do not write one document, are usage errors).
    Blank lines are skipped. A failed line never stops the batch.
    Use it to avoid the start-up cost of the CLI (about 0.45 s per call) when
    an agent runs several commands.

    Output: one document per input line, each with its own "schema"
    ("groups.v1", "entries.v1", ..., "error.v1"); diagnostics on stderr.

    Exit codes: 0 when the input was read, whatever each line returned; 1 the
    input could not be read (not UTF-8, or a read error).
    """
    outer = ctx.current()
    stdin = sys.stdin
    if isinstance(stdin, io.TextIOWrapper):
        stdin.reconfigure(encoding="utf-8", errors="strict")
    try:
        for number, line in enumerate(stdin, start=1):
            if line.strip():
                outer.output.write_line(_batch_line(outer, number, line))
                outer.output.out.flush()
    except (OSError, UnicodeDecodeError) as exc:
        raise OpError("usage", f"cannot read the batch input: {exc}") from exc
    return 0


def _batch_line(outer: ctx.Context, number: int, line: str) -> str:
    """Run one ``batch`` line; return its one-line JSON document (``error.v1`` on failure)."""

    def refused(message: str) -> str:
        return encode_json(OpError("usage", f"line {number}: {message}").envelope())

    try:
        argv = json.loads(line)
    except json.JSONDecodeError as exc:
        return refused(f"not JSON ({exc})")
    if not argv or not isinstance(argv, list) or not all(isinstance(a, str) for a in argv):
        return refused("expected a JSON array of strings: the arguments of one command")
    if argv[0] in BATCH_REFUSED:
        return refused(f"{argv[0]} does not write one JSON document and cannot run in batch")
    if any(a in ("--help", "-h", "--version") for a in argv):
        return refused("--help and --version are not available in batch")
    if argv[0].startswith("-"):
        return refused(
            f"a line starts with its command, not a global option ({argv[0]}): give global "
            "options to batch itself (atlas-log --store DIR batch)"
        )
    buffer = io.StringIO()
    o = outer.output
    inner = Output(
        "json",
        quiet=o.quiet,
        width=o.width,
        color_mode="never",
        app_packages=o.app_packages,
        stdout=buffer,
        stderr=o.err,
    )
    from atlas_log_analyzer.cli.app import dispatch

    with ctx.use(ctx.Context(inner, outer.settings)) as here:
        dispatch(argv, here, unexpected=_internal_error)
    text = buffer.getvalue().strip()
    if not _one_document(text):  # e.g. --raw text
        return refused(f"{argv[0]} did not write one JSON document")
    return text


def _one_document(text: str) -> bool:
    if not text or len(text.splitlines()) > 1:
        return False
    try:
        doc = json.loads(text)
    except json.JSONDecodeError:
        return False
    return isinstance(doc, dict) and "schema" in doc


def _internal_error(exc: Exception) -> OpError:
    return OpError("internal_error", f"{type(exc).__name__}: {exc} (set ATLAS_LOG_DEBUG=1)")
