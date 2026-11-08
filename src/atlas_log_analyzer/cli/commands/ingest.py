"""Commands that read log files or change sets: ingest, label, parse, regroup, upgrade."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from itertools import islice
from pathlib import Path
from typing import TYPE_CHECKING, Annotated, Any

from cyclopts import Parameter

from atlas_log_analyzer.cli import context as ctx
from atlas_log_analyzer.cli import params, render
from atlas_log_analyzer.cli.output import (
    ExitCode,
    OpError,
    entry_to_dict,
)
from atlas_log_analyzer.core.parser import parse_sources
from atlas_log_analyzer.service import ingest as service_ingest
from atlas_log_analyzer.service.params import IngestTarget

# polars, DuckDB, rich and the ingest pipeline are imported by the code that needs them
if TYPE_CHECKING:
    from atlas_log_analyzer.store.pipeline import IngestProgress, SourceSelection

__all__ = ["ingest", "label", "parse", "regroup", "select_sources", "upgrade"]


def ingest(
    *paths: Path,
    format: str | None = None,
    name: str | None = None,
    frames: int | None = None,
    overwrite: bool = False,
    mine: bool = False,
    force: bool = False,
    target: Annotated[IngestTarget, Parameter(name="*")] = params.ANY_TARGET,
) -> int:
    """Parse log files into a stored log set (one per cluster node) and print its manifest.

    PATHs are files, directories or ZIP files (directories are scanned
    non-recursively for *.log*; rotated files x.log.N (also gzipped, x.log.N.gz)
    are read oldest first; a
    ZIP is extracted into the store, job.json and *.log* files only, at most
    32 GiB, and removed afterwards). Files found in a directory that the format
    does not recognise in their first 200 lines (access, GC or JSON logs next
    to atlassian-jira.log) are skipped and listed in "ingest.skipped", and so
    are side logs next to their main log (atlassian-jira-security.log,
    -outgoing-mail.log, -slow-queries.log beside atlassian-jira.log); files
    named explicitly are read, except one over 64 MiB that the format does not
    recognise at all (error "not_in_format"; --force reads it). Lines over 1 MiB
    are cut, and an entry keeps at most 4 Mi characters and 100,000 lines
    ("parse_error": "entry truncated: N lines, M bytes"). The log-set id is
    derived from the file digests, so ingesting the same files again is a no-op
    that prints the existing manifest (use --overwrite to rebuild it). Progress
    goes to stderr.

    Cluster logs become one set per node (--split auto): a cluster log export
    (a ZIP or folder with job.json and NODE_atlassian-jira.log* files), files
    named NODE_atlassian-jira.log* of two or more nodes, or one directory whose
    sub-directories (two or more) hold one node's logs each. --split prefix|dir
    forces a kind, --split none keeps one set. Each node's set is named
    PREFIX_node (PREFIX: --name, else --cluster, else the first 8 characters of
    the capture id) and labelled with its node, the cluster (--cluster, else
    that of the node's newest earlier set) and the capture id (--capture, else
    the export's jobId, else a hash of the set ids). A single set gets --node,
    --cluster (default: the node's earlier cluster) and --capture; when its log
    names its node, a warning suggests --node.

    Output (json): one set: the manifest, schema "logset.v1":
      {"schema": "logset.v1", "id", "name", "format",
       "sources": [{"path", "size", "xxh3", "first_ts", "last_ts", "entries"}],
       "first_ts", "last_ts", "entry_count", "group_count", "created_at",
       "tool_version", "schema_version", "signature_version",
       "mask_rules_version", "signature_spec", "recipe_key",
       "node", "cluster", "capture",
       "ingest": {"parsed_entries", "parse_errors", "elapsed_s", "existing",
                  "skipped": [path, ...], "warnings": [str, ...]},
       "merges": [{"cluster_id", "signatures": [str], "drain_template"}]}
    A split: {"schema": "ingest-capture.v1",
      "capture": {"capture", "cluster", "first_ts", "last_ts", "entry_count",
                  "nodes": [{"node", "id", "name", "first_ts", "last_ts",
                             "entry_count"}]},
      "sets": [<logset.v1 with "ingest" as above>, ...], "warnings": [str]}
    "merges" is present only with --mine; the proposals are also stored in
    merges.parquet (the "merges" view of `query`).
    "ingest.existing" is true when the set already existed and was reused (it
    gets the node, cluster and capture it lacks); "ingest.warnings" then says
    when --name was ignored (also on stderr).

    Without --store and ATLAS_LOG_STORE the store is ./.atlas-log; inside a git
    work tree it is not created (error "store_error": real logs could be
    committed), but an existing ./.atlas-log is used.

    Exit codes: 0 ok; 1 unknown --format, bad option, bad ZIP, a --split that
    does not fit the input, or --node with a split; 2 no files found, format
    not detected, a large named file not in the format, or no entries parsed
    (manifest still printed); 3 store error (including a refused default store
    inside a git work tree).

    Args:
        paths: Log files, directories or ZIPs.
        format: Format name (see `formats`); default: detected from the first 200 lines
            of the first file a format recognises (named files first).
        name: Human-readable alias for the set, usable wherever SET is expected; with a
            split, the prefix of the sets' names.
        frames: Add the top N stack frames to signatures (finer groups); default 0.
        overwrite: Rebuild the set if one with the same id already exists.
        mine: Propose group merges with Drain3 (needs extra "mine": uv sync --extra mine).
        force: Read a named file over 64 MiB even if none of its first 200 lines is
            in the format.
        target: Node, cluster, capture and split (the options --node to --split).

    """
    out = ctx.out()
    if not paths:
        raise OpError("usage", "at least one PATH is required")
    store = ctx.store(create=True)

    def progress(p: IngestProgress) -> None:
        if p.entries % 100_000 == 0:
            out.info(f"  {p.entries:,} entries")

    doc = service_ingest.ingest_paths(
        store,
        paths,
        fmt=format,
        name=name,
        frames=frames,
        overwrite=overwrite,
        mine=mine,
        force=force,
        target=target,
        progress=progress,
        announce=out.info,
        front="cli",
    )
    if doc["schema"] == "ingest-capture.v1":
        return _capture_result(doc)
    _set_result(doc)
    out.document(doc, items=None, title=f"log set {doc['id']}")
    if out.mode == "table" and doc["entry_count"]:
        out.info(f"next: atlas-log triage {doc['name'] or doc['id']}")
    if doc["entry_count"] == 0:
        out.warn("no entries were parsed")
        return int(ExitCode.NOT_FOUND)
    return 0


def _set_result(doc: dict[str, Any], *, of_capture: bool = False) -> None:
    """Report one set's ingest on stderr (a capture prints the warnings once, prefixed)."""
    out, info = ctx.out(), doc["ingest"]
    who = f"{doc['node']}: " if of_capture and doc.get("node") else ""
    if info["existing"]:
        out.info(f"{who}log set {doc['id']} already exists; use --overwrite to rebuild it")
    else:
        # the set's entries: parsed ones less the duplicates shared by several files
        dup = info["parsed_entries"] - doc["entry_count"]
        out.info(
            f"{who}ingested {doc['entry_count']:,} entries into {doc['id']} "
            f"({doc['group_count']:,} groups) in {info['elapsed_s']:.1f} s"
            + (f"; {dup:,} duplicate entries skipped" if dup > 0 else "")
        )
    if not of_capture:
        for warning in info["warnings"]:
            out.warn(warning)
    if info["parse_errors"]:
        out.warn(f"{who}{info['parse_errors']:,} entries could not be parsed (parse_error is set)")
    if "merges" in doc:
        out.info(f"{who}{len(doc['merges'])} merge proposal(s) written to merges.parquet")


def _capture_result(doc: dict[str, Any]) -> int:
    """Report a split ingest (``ingest-capture.v1``) and print it; return the exit code."""
    out = ctx.out()
    for item in doc["sets"]:
        _set_result(item, of_capture=True)
    for warning in doc["warnings"]:
        out.warn(warning)
    capture = doc["capture"]["capture"]
    out.document(doc, items="sets", columns=render.CAPTURE_SET_COLUMNS, title=f"capture {capture}")
    if out.mode == "table":
        out.info("next: atlas-log triage SET for each node; atlas-log captures")
    if all(item["entry_count"] == 0 for item in doc["sets"]):
        out.warn("no entries were parsed")
        return int(ExitCode.NOT_FOUND)
    return 0


def label(
    logset: str,
    /,
    *,
    node: str | None = None,
    cluster: str | None = None,
    capture: str | None = None,
) -> None:
    """Label a log set (its cluster node, cluster and capture).

    Rewrites only the set's manifest: its id, name and data stay as they are.
    An option that is not given keeps its value; an empty value (--node "")
    clears it. Values are letters, digits, "_", "." and "-" (at most 128).
    Sets that share a capture id are listed together by `captures`; triage
    compares a set that has a node only with earlier sets of the same node.

    Output (json): the manifest, {"schema": "logset.v1", ...} (see `ingest`).

    Exit codes: 0 ok; 1 no option given or a bad value; 3 unknown set or store
    error.

    Args:
        logset: Log set id, unique id prefix or name.
        node: Node (the cluster node id the logs come from, e.g. NODE1).
        cluster: Cluster (the name of the Jira instance of the node, e.g. jira-test).
        capture: Capture (its id: sets with the same capture are viewed together).

    """
    doc = service_ingest.label(
        ctx.store(), logset, node=node, cluster=cluster, capture=capture, front="cli"
    )
    ctx.out().document(doc, items=None, title=f"log set {doc['id']}")


def parse(*paths: Path, format: str | None = None, limit: int = 0, force: bool = False) -> int:
    """Parse log files and stream the entries as JSON Lines to stdout (no store).

    Always writes JSONL, one entry per line, regardless of --output:
      {"id", "format", "timestamp" (ISO 8601 with offset), "level" (name),
       "thread", "logger", "message", "body", "exception_type",
       "exceptions": [{"type", "message", "frames": [{"location", "file",
                       "line", "module"}], "omitted_frames"}],
       "context": {"user", "request_id", "session", "ip", "url", "request_url", ...},
       "source": {"path", "line", "offset"}, "raw", "parse_error"}
    Entries whose header could not be parsed have "parse_error" set. Files
    found in a directory that are not in the format are skipped, and a large
    named file not in the format is refused (as `ingest`).

    Exit codes: 0 ok; 1 unknown --format; 2 no files, format not detected, a
    large named file not in the format, or no entries.

    Args:
        paths: Log files and/or directories.
        format: Format name; detected from the first 200 lines when omitted.
        limit: Stop after this many entries; 0 = all.
        force: Read a named file over 64 MiB even if it is not in the format.

    """
    import msgspec

    lim = params.limit(limit)
    selection = select_sources(paths, format, force=force)
    sources, fmt = selection.sources, selection.fmt
    encoder = msgspec.json.Encoder()
    stream = ctx.out().out
    count = 0
    batch: list[bytes] = []
    entries_iter: Iterable[Any] = parse_sources(sources, fmt)
    if lim is not None:
        entries_iter = islice(entries_iter, lim)
    for entry in entries_iter:
        batch.append(encoder.encode(entry_to_dict(entry)))
        count += 1
        if len(batch) >= 1000:
            stream.write(b"\n".join(batch).decode("utf-8") + "\n")
            batch.clear()
    if batch:
        stream.write(b"\n".join(batch).decode("utf-8") + "\n")
    stream.flush()
    if count == 0:
        raise OpError("no_entries", "no entries were parsed")
    ctx.out().info(f"parsed {count:,} entries")
    return 0


def regroup(logset: str, /, *, frames: int = 0, no_exceptions: bool = False) -> None:
    """Recompute the groups of a stored log set with another signature spec.

    Re-signs the stored entries (each distinct level, logger, message and
    exception chain once) and rewrites the signature columns of
    entries.parquet and groups.parquet; every other column is kept.
    Signatures change, so diffs against sets built with another spec need
    --force. Prints the updated manifest (new signature_spec and recipe_key).

    Output (json): the manifest, {"schema": "logset.v1", ...} (see `ingest`).

    Exit codes: 0 ok; 1 bad option; 3 unknown set or store error.

    Args:
        logset: Log set id, unique id prefix or name.
        frames: Include the top N stack frames of each exception in the signature.
        no_exceptions: Leave the exception chain out of the signature (like --coarse).

    """
    doc = service_ingest.regroup(
        ctx.store(), logset, frames=frames, no_exceptions=no_exceptions, front="cli"
    )
    ctx.out().document(doc, items=None, title=f"log set {doc['id']}")


def upgrade(*logsets: str, all: bool = False) -> None:
    """Rebuild stale log sets at the current recipe, each with its own spec.

    A set is stale ("stale": true in `sets`) when its recipe_key (format
    version, signature spec, signature and masking-rule versions) differs from
    the recipe this version signs its format and spec with; stale sets are not
    comparable with new ones, and triage skips them as baselines. upgrade
    re-signs the stored entries as `regroup` does, keeping each set's
    signature spec. A set built by an older version of its format is also
    re-assembled from its stored entries (Jira sets of format version 1:
    banner lines join the entry they announce); the log files are not read.
    Sets that are current, or whose format is not registered, are skipped
    with a reason. A set ingested from a ZIP by an older version names its
    files by the spool directory they were extracted to (removed since):
    upgrade renames them to <zip>/<member>, stale or not.

    Output (json):
      {"schema": "upgrade.v1", "count": int, "upgraded": int,
       "items": [<logset.v1 manifest without "schema"> +
                 {"status": "upgraded" | "skipped", "reason": str | null,
                  "previous_recipe_key": str, "renamed_sources"?: int}, ...]}
    "reason" is null for an upgraded set, "current" for a set already at the
    current recipe, or names the unknown format or spec. Items follow the
    order of SET arguments (with --all: the `sets` order, newest first).

    Exit codes: 0 ok (also when everything was skipped); 1 no SET and no
    --all; 3 unknown set or store error.

    Args:
        logsets: Log sets to upgrade (id, unique id prefix or name).
        all: Upgrade every set in the store.

    """
    doc = service_ingest.upgrade(ctx.store(), logsets, all_sets=all, front="cli")
    for item in doc["items"]:
        if item["status"] == "skipped" and item["reason"] != "current":
            ctx.out().warn(f"{item['id']}: skipped, {item['reason']}")
    ctx.out().document(doc, columns=render.UPGRADE_COLUMNS, title="upgrade")


def select_sources(
    paths: Sequence[Path], name: str | None, *, force: bool = False
) -> SourceSelection:
    """Files and format of `ingest`/`parse`; foreign files found in directories are skipped."""
    selection = service_ingest.select(paths, name, force=force, front="cli")
    for note in selection.skipped_notes():  # foreign files, side logs: one line each
        ctx.out().info(note)
    return selection
