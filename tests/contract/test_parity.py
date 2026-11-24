"""CLI ↔ API parity: ``ragnalog --output json`` and ``/api`` return the same documents.

Each case runs one CLI command in-process and the matching API request against
the same store and compares the two documents after dropping volatile keys and
the case's allowed differences. :data:`CASES` is the written parity contract
(see docs/design.md, "CLI and API parity"): an entry in ``allowed`` is a
difference by design and says why; a known drift is a case marked
``xfail(strict=True)``, so fixing it makes the case fail until the marker is removed.

Errors compare the shared ``error.reason`` of both sides (``error.code`` when there
is none: the codes predate the reasons and may differ, see
``service/errors.py``) plus the documented CLI exit code and HTTP status of the case.
"""

from __future__ import annotations

import dataclasses
import json
import shutil
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from ragnalog.cli.app import run
from ragnalog.store.logset import LogSetManifest
from ragnalog.store.pipeline import ingest_paths
from ragnalog.store.store import Store
from ragnalog.webapp import Settings, create_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jira"
CLUSTER = Path(__file__).resolve().parents[1] / "fixtures" / "cluster"

VOLATILE = frozenset({"created_at", "elapsed_s", "tool_version", "report_url"})
"""Keys dropped at any depth: they differ between runs, not between front ends."""

CLI_MARKER, API_MARKER = "use --max-raw 0", "use max_raw=0"
"""How a cut ``raw``/``body`` says to get the full text; each names its own option."""


@dataclass(frozen=True)
class Case:
    """One CLI command and the API request that must return the same document."""

    id: str
    cli: Sequence[str]
    api: str
    allowed: frozenset[str] = frozenset()
    """Key paths that may differ (``a.b``; ``items[].x`` = key ``x`` of every item)."""
    exit: int | None = None
    """Error case: the CLI exit code (its ``reason``, else ``code``, must equal the API's)."""
    status: int | None = None
    """Error case: the HTTP status."""
    reason: str | None = None
    """Error case: the expected ``error.reason``."""
    masked: frozenset[str] = frozenset()
    """Key paths of a known drift that another (xfail) case pins down."""
    drift: str | None = None
    """Known drift: the case is ``xfail(strict=True)`` with this reason."""


# The parity contract. Placeholders: {edge} {sample} = set ids, {sig} = an ERROR group
# signature of "edge" with an exception, {entry} = an entry id of that group.
CASES: list[Case] = [
    # --- listings and metadata
    Case(
        "sets",
        ["sets"],
        "/api/sets",
        # "store": the CLI prints the store path, the API only its name (no server paths);
        # "summary": the API adds error/warning counts per set for the index page.
        frozenset({"store", "items[].summary"}),
    ),
    Case(
        "set",
        ["set", "edge"],
        "/api/sets/edge",
        frozenset({"summary"}),  # as in "sets"
    ),
    Case("captures", ["captures"], "/api/captures"),
    Case("captures-brief", ["captures", "--brief"], "/api/captures?brief=true"),
    Case("sets-brief", ["sets", "--brief"], "/api/sets?brief=true", frozenset({"store"})),
    Case("version", ["version"], "/api/version"),
    Case("formats", ["formats"], "/api/formats"),
    Case("stats", ["stats", "{edge}"], "/api/sets/{edge}/stats"),  # by id
    # --- groups
    Case("groups", ["groups", "edge"], "/api/sets/edge/groups"),
    Case("groups-all", ["groups", "edge", "--limit", "0"], "/api/sets/edge/groups?limit=0"),
    Case(
        "groups-page",
        ["groups", "sample", "--limit", "3", "--offset", "2", "--sort", "last_seen"],
        "/api/sets/sample/groups?limit=3&offset=2&sort=last_seen",
    ),
    Case(
        "groups-level",
        ["groups", "sample", "--level", "ERROR,WARN"],
        "/api/sets/sample/groups?level=ERROR,WARN",
    ),
    Case(
        "groups-min-level-logger",
        ["groups", "sample", "--min-level", "WARN", "--logger", "c.a"],
        "/api/sets/sample/groups?min_level=WARN&logger=c.a",
    ),
    Case(
        "groups-exception",
        ["groups", "sample", "--with-exception", "--exception", "exception"],
        "/api/sets/sample/groups?with_exception=true&exception=exception",
    ),
    Case(
        "groups-contains",
        ["groups", "sample", "--contains", "failed"],
        "/api/sets/sample/groups?contains=failed",
    ),
    Case(
        "groups-since-until",
        ["groups", "sample", "--since", "2026-10-26", "--until", "2026-10-28T12:00:00+01:00"],
        "/api/sets/sample/groups?since=2026-10-26&until=2026-10-28T12:00:00%2B01:00",
    ),
    Case(
        "groups-relative-since",
        ["groups", "sample", "--since", "2d"],
        "/api/sets/sample/groups?since=2d",
    ),
    Case("groups-coarse", ["groups", "sample", "--coarse"], "/api/sets/sample/groups?coarse=true"),
    Case(
        "groups-columns",
        ["groups", "sample", "--columns", "signature,count", "--columns", "template"],
        "/api/sets/sample/groups?columns=signature,count&columns=template",
    ),
    Case("groups-brief", ["groups", "sample", "--brief"], "/api/sets/sample/groups?brief=true"),
    Case(
        "group",
        ["show", "edge", "{sig}"],
        "/api/sets/edge/groups/{sig}",
        # `show` is entries.v1 of one group (echoing "signature"); the API's group.v1
        # adds the group's hourly "histogram" for the page.
        frozenset({"schema", "signature", "histogram"}),
    ),
    Case(
        "group-max-raw",
        ["show", "edge", "{sig}", "--limit", "1", "--max-raw", "40"],
        "/api/sets/edge/groups/{sig}?limit=1&max_raw=40",
        frozenset({"schema", "signature", "histogram"}),
    ),
    # --- entries
    Case(
        "entries-next-offset", ["entries", "edge", "--limit", "5"], "/api/sets/edge/entries?limit=5"
    ),
    Case("entries", ["entries", "edge"], "/api/sets/edge/entries"),
    Case(
        "entries-filters",
        ["entries", "sample", "--min-level", "WARN", "--with-exception", "--limit", "5"],
        "/api/sets/sample/entries?min_level=WARN&with_exception=true&limit=5",
    ),
    Case(
        "entries-signature-contains",
        ["entries", "edge", "--signature", "{sig}", "--contains", "script"],
        "/api/sets/edge/entries?signature={sig}&contains=script",
    ),
    Case(
        "timeline-entry-filters",
        [
            "timeline",
            "sample",
            "--bucket",
            "1d",
            "--with-exception",
            "--contains",
            "at ",
            "--logger",
            "com",
        ],
        "/api/sets/sample/timeline?bucket=1d&with_exception=true&contains=at%20&logger=com",
    ),
    Case("users", ["users", "sample"], "/api/sets/sample/users?limit=50"),
    Case(
        "users-top",
        ["users", "sample", "--top", "10", "--limit", "2"],
        "/api/sets/sample/users?top=10&limit=2",
    ),
    Case(
        "users-group-window",
        ["users", "sample", "--signature", "{sig}", "--min-level", "WARN", "--limit", "0"],
        "/api/sets/sample/users?signature={sig}&min_level=WARN&limit=0",
    ),
    Case(
        "timeline-by-level",
        ["timeline", "sample", "--bucket", "15m", "--by-level", "--min-level", "WARN"],
        "/api/sets/sample/timeline?bucket=15m&by_level=true&min_level=WARN",
    ),
    Case(
        "entries-columns",
        ["entries", "sample", "--columns", "id,ts,level_name", "--limit", "5", "--offset", "3"],
        "/api/sets/sample/entries?columns=id,ts,level_name&limit=5&offset=3",
    ),
    Case(
        "entries-brief",
        ["entries", "sample", "--brief", "--limit", "5"],
        "/api/sets/sample/entries?brief=true&limit=5",
    ),
    Case(
        "entries-max-raw",
        ["entries", "edge", "--max-raw", "60", "--limit", "5"],
        "/api/sets/edge/entries?max_raw=60&limit=5",
    ),
    Case(
        "entries-level-unknown",
        ["entries", "edge", "--level", "UNKNOWN"],
        "/api/sets/edge/entries?level=UNKNOWN",
    ),
    Case(
        "entries-empty-since", ["entries", "edge", "--since", ""], "/api/sets/edge/entries?since="
    ),
    Case(
        "groups-blank-filters",
        ["groups", "sample", "--until", " ", "--logger", "", "--level", ""],
        "/api/sets/sample/groups?until=%20&logger=&level=",
    ),
    Case(
        "context",
        ["context", "edge", "{entry}", "--before", "2", "--after", "2", "--any-thread"],
        "/api/sets/edge/entries/{entry}/context?before=2&after=2&any_thread=true",
    ),
    Case("frames", ["frames", "edge", "{sig}"], "/api/sets/edge/frames/{sig}"),
    Case(
        "frames-all",
        ["frames", "edge", "{entry}", "--all"],
        "/api/sets/edge/frames/{entry}?all=true",
    ),
    # --- comparison
    Case("diff", ["diff", "{sample}", "{edge}"], "/api/diff?a={sample}&b={edge}"),
    Case(
        "diff-rate",
        ["diff", "sample", "edge", "--rate", "--min-level", "WARN", "--limit", "2"],
        "/api/diff?a=sample&b=edge&rate=true&min_level=WARN&limit=2",
    ),
    Case("triage", ["triage", "edge"], "/api/sets/edge/triage"),
    Case(
        "triage-baseline",
        ["triage", "edge", "--baseline", "sample", "--min-level", "ERROR", "--limit", "2"],
        "/api/sets/edge/triage?baseline=sample&min_level=ERROR&limit=2",
    ),
    Case("triage-no-baseline", ["triage", "sample"], "/api/sets/sample/triage"),
    # --- errors
    Case(
        "error-unknown-set",
        ["groups", "nope"],
        "/api/sets/nope/groups",
        exit=3,
        status=404,
        reason="set_not_found",
    ),
    Case(
        "error-ambiguous-name",
        ["groups", "twin"],
        "/api/sets/twin/groups",
        exit=3,
        status=409,
        reason="ambiguous_ref",
    ),
    Case(
        "error-ambiguous-prefix",
        ["stats", "aaaa"],
        "/api/sets/aaaa/stats",
        exit=3,
        status=409,
        reason="ambiguous_ref",
    ),
    Case(
        "error-incompatible-sets",
        ["diff", "edge", "edge-f1"],
        "/api/diff?a=edge&b=edge-f1",
        exit=1,
        status=409,
        reason="incompatible_sets",
    ),
    Case(
        "error-bad-level",
        ["groups", "edge", "--level", "SEVERE"],
        "/api/sets/edge/groups?level=SEVERE",
        exit=1,
        status=400,
    ),
    Case(
        "error-bad-column",
        ["groups", "edge", "--columns", "signature,nope"],
        "/api/sets/edge/groups?columns=signature,nope",
        exit=1,
        status=400,
    ),
    Case(
        "error-brief-and-columns",
        ["entries", "edge", "--brief", "--columns", "id"],
        "/api/sets/edge/entries?brief=true&columns=id",
        exit=1,
        status=400,
    ),
    Case(
        "error-bad-time",
        ["entries", "edge", "--until", "yesterday"],
        "/api/sets/edge/entries?until=yesterday",
        exit=1,
        status=400,
    ),
    Case(
        "error-no-group",
        ["frames", "edge", "0000000000000000"],
        "/api/sets/edge/frames/0000000000000000",
        exit=2,
        status=404,
        reason="ref_not_found",
    ),
    Case(
        "error-no-entry",
        ["context", "edge", "0000000000000000"],
        "/api/sets/edge/entries/0000000000000000/context",
        exit=2,
        status=404,
        reason="entry_not_found",
    ),
    # a number below its bound is invalid_value in both (the API mapped it to usage)
    Case(
        "error-negative-offset",
        ["entries", "edge", "--offset", "-1"],
        "/api/sets/edge/entries?offset=-1",
        exit=1,
        status=400,
    ),
    # and so is a count past int32 (the store's bound), where a page cap would be usage
    Case(
        "error-offset-past-int32",
        ["entries", "edge", "--offset", "2147483648"],
        "/api/sets/edge/entries?offset=2147483648",
        exit=1,
        status=400,
    ),
    Case(
        "error-negative-max-raw",
        ["entries", "edge", "--max-raw", "-1"],
        "/api/sets/edge/entries?max_raw=-1",
        exit=1,
        status=400,
    ),
    Case(
        "error-negative-before",
        ["context", "edge", "{entry}", "--before", "-1"],
        "/api/sets/edge/entries/{entry}/context?before=-1",
        exit=1,
        status=400,
    ),
    Case(
        "error-negative-frames-limit",
        ["frames", "edge", "{entry}", "--limit", "-1"],
        "/api/sets/edge/frames/{entry}?limit=-1",
        exit=1,
        status=400,
    ),
    Case(
        "error-triage-grow",
        ["triage", "edge", "--grow", "1"],
        "/api/sets/edge/triage?grow=1",
        exit=1,
        status=400,
    ),
]

# The capture views, on a store of their own (cluster_dir): nodes NODE1 and NODE2 of
# capture "cap-acme" of cluster "acme" (tests/fixtures/cluster) and an earlier NODE1 set
# (tests/cluster.py), the triage baseline of NODE1. Placeholders: {n1} = the NODE1 set id,
# {sig} = the heartbeat group (on both nodes), {pfx} = its first 6 characters, {sig2} = a
# WARN/ERROR group of NODE2, {entry} = an entry of NODE1 inside the overlap.
CLUSTER_CASES: list[Case] = [
    Case("capture", ["capture", "cluster:acme"], "/api/captures/cluster:acme"),
    Case(
        "capture-union-no-triage",
        ["capture", "capture:cap-a", "--window", "union", "--no-triage"],
        "/api/captures/capture:cap-a?window=union&triage=false",
    ),
    Case(
        "capture-since-until",
        ["capture", "cluster:acme@2026-11-02", "--since", "2026-11-02T10:00", "--until", "1h"],
        "/api/captures/cluster:acme@2026-11-02?since=2026-11-02T10:00&until=1h",
    ),
    Case("capture-of-a-member-set", ["capture", "{n1}"], "/api/captures/{n1}"),
    # a bare capture id names its capture in every capture view of the CLI, as in the API
    Case("capture-bare-id", ["capture", "cap-acme"], "/api/captures/cap-acme"),
    Case(
        "cluster-timeline-bare-id",
        ["timeline", "cap-acme", "--bucket", "1h"],
        "/api/captures/cap-acme/timeline?bucket=1h",
    ),
    Case(
        "capture-entries-bare-id",
        ["entries", "cap-acme", "--brief", "--limit", "5"],
        "/api/captures/cap-acme/entries?brief=true&limit=5",
    ),
    Case("capture-of-one-set", ["capture", "acme_node1_prev"], "/api/captures/acme_node1_prev"),
    Case(
        "capture-users",
        ["users", "cluster:acme", "--top", "2", "--limit", "0"],
        "/api/captures/cluster:acme/users?top=2&limit=0",
    ),
    Case(
        "capture-users-node-window",
        ["users", "cap-acme", "--node", "NODE1", "--window", "union"],
        "/api/captures/cap-acme/users?node=NODE1&window=union&limit=50",
    ),
    Case("cluster-timeline", ["timeline", "cluster:acme"], "/api/captures/cluster:acme/timeline"),
    Case(
        "cluster-timeline-group",
        ["timeline", "acme_node1", "--by-node", "--bucket", "15m", "--signature", "{pfx}"],
        "/api/captures/acme_node1/timeline?bucket=15m&signature={pfx}",
    ),
    Case(
        "cluster-timeline-filters",
        ["timeline", "cluster:acme", "--bucket", "30m", "--min-level", "WARN", "--contains", "a"],
        "/api/captures/cluster:acme/timeline?bucket=30m&min_level=WARN&contains=a",
    ),
    Case(
        "cluster-timeline-node-window",
        ["timeline", "cluster:acme", "--node", "NODE1", "--window", "union", "--thread", "x"],
        "/api/captures/cluster:acme/timeline?node=NODE1&window=union&thread=x",
    ),
    Case(
        "cluster-timeline-level-unknown",
        ["timeline", "cluster:acme", "--level", "UNKNOWN"],
        "/api/captures/cluster:acme/timeline?level=UNKNOWN",
    ),
    Case("set-timeline", ["timeline", "acme_node2"], "/api/sets/acme_node2/timeline"),
    Case(
        "set-timeline-filters",
        ["timeline", "acme_node2", "--bucket", "30m", "--min-level", "WARN", "--since", "2h"],
        "/api/sets/acme_node2/timeline?bucket=30m&min_level=WARN&since=2h",
    ),
    Case("matrix", ["matrix", "cluster:acme"], "/api/captures/cluster:acme/matrix"),
    Case(
        "matrix-spread-sort-page",
        ["matrix", "cluster:acme", "--spread", "only", "--sort", "lead", "--limit", "2"],
        "/api/captures/cluster:acme/matrix?spread=only&sort=lead&limit=2",
    ),
    Case(
        "matrix-filters",
        ["matrix", "cluster:acme", "--min-level", "WARN", "--node", "NODE1", "--skew-min", "2"],
        "/api/captures/cluster:acme/matrix?min_level=WARN&node=NODE1&skew_min=2",
    ),
    Case(
        "matrix-signature-window",
        ["matrix", "cluster:acme", "--signature", "{pfx}", "--window", "union", "--offset", "0"],
        "/api/captures/cluster:acme/matrix?signature={pfx}&window=union&offset=0",
    ),
    Case(
        "matrix-contains-since",
        ["matrix", "cluster:acme", "--contains", "deadlock", "--since", "2026-11-02T10:00"],
        "/api/captures/cluster:acme/matrix?contains=deadlock&since=2026-11-02T10:00",
    ),
    Case("events", ["events", "cluster:acme"], "/api/captures/cluster:acme/events"),
    Case(
        "events-kinds",
        ["events", "cluster:acme", "--kind", "index_replay,cluster_lock", "--kind", "start"],
        "/api/captures/cluster:acme/events?kind=index_replay,cluster_lock&kind=start",
    ),
    Case(
        "interactions", ["interactions", "cluster:acme"], "/api/captures/cluster:acme/interactions"
    ),
    Case(
        "interactions-delta-union",
        ["interactions", "cluster:acme", "--delta", "120", "--window", "union"],
        "/api/captures/cluster:acme/interactions?delta=120&window=union",
    ),
    Case(
        "merged-entries",
        ["entries", "cluster:acme", "--limit", "5"],
        "/api/captures/cluster:acme/entries?limit=5",
    ),
    Case(
        "merged-entries-page-filters",
        ["entries", "cluster:acme", "--min-level", "WARN", "--limit", "3", "--offset", "2"],
        "/api/captures/cluster:acme/entries?min_level=WARN&limit=3&offset=2",
    ),
    Case(
        "merged-entries-window",
        [
            "entries",
            "cluster:acme",
            "--since",
            "2026-11-02T10:59:00Z",
            "--until",
            "2026-11-02T11:01:00Z",
            "--brief",
        ],
        "/api/captures/cluster:acme/entries?since=2026-11-02T10:59:00Z"
        "&until=2026-11-02T11:01:00Z&brief=true",
    ),
    Case(
        "merged-entries-columns-signature",
        ["entries", "cluster:acme", "--columns", "id,ts,node", "--signature", "{pfx}"],
        "/api/captures/cluster:acme/entries?columns=id,ts,node&signature={pfx}",
    ),
    Case(
        "merged-entries-groups-node",
        ["entries", "cluster:acme", "--signature", "{pfx},{sig2}", "--node", "NODE2", "--brief"],
        "/api/captures/cluster:acme/entries?signature={pfx},{sig2}&node=NODE2&brief=true",
    ),
    Case(
        "merged-entries-group-per-node",
        ["entries", "cluster:acme", "--signature", "NODE1:{pfx},NODE2:{sig2}", "--brief"],
        "/api/captures/cluster:acme/entries?signature=NODE1:{pfx},NODE2:{sig2}&brief=true",
    ),
    Case(
        "merged-entries-nodes",
        ["entries", "cluster:acme", "--node", "NODE1,NODE2", "--limit", "3"],
        "/api/captures/cluster:acme/entries?node=NODE1,NODE2&limit=3",
    ),
    Case(
        "merged-entries-union-max-raw",
        ["entries", "cluster:acme", "--window", "union", "--max-raw", "30", "--limit", "4"],
        "/api/captures/cluster:acme/entries?window=union&max_raw=30&limit=4",
    ),
    Case(
        "context-all-nodes",
        ["context", "{n1}", "{entry}", "--all-nodes"],
        "/api/sets/{n1}/entries/{entry}/context?all_nodes=1",
    ),
    Case(
        "context-all-nodes-window",
        ["context", "acme_node1", "{entry}", "--all-nodes", "--window", "600", "--brief"],
        "/api/sets/acme_node1/entries/{entry}/context?all_nodes=true&window=600&brief=true",
    ),
    # --- errors
    Case(
        "error-unknown-capture",
        ["capture", "cluster:nope"],
        "/api/captures/cluster:nope",
        exit=2,
        status=404,
        reason="capture_not_found",
    ),
    Case(
        "error-capture-bad-day",
        ["matrix", "cluster:acme@yesterday"],
        "/api/captures/cluster:acme@yesterday/matrix",
        exit=1,
        status=400,
    ),
    Case(
        "error-capture-unknown-set",
        ["events", "nope"],
        "/api/captures/nope/events",
        exit=3,
        status=404,
        reason="set_not_found",
    ),
    Case(
        "error-capture-bad-window",
        ["interactions", "cluster:acme", "--window", "all"],
        "/api/captures/cluster:acme/interactions?window=all",
        exit=1,
        status=400,
    ),
    Case(
        "error-capture-no-overlap-time",
        ["capture", "cluster:acme", "--since", "2026-11-03", "--until", "2026-11-02"],
        "/api/captures/cluster:acme?since=2026-11-03&until=2026-11-02",
        exit=1,
        status=400,
    ),
    Case(
        "error-matrix-unknown-node",
        ["matrix", "cluster:acme", "--node", "NODE9"],
        "/api/captures/cluster:acme/matrix?node=NODE9",
        exit=1,
        status=400,
    ),
    Case(
        "error-merged-entries-unknown-node",
        ["entries", "cluster:acme", "--node", "NODE1,NODE9"],
        "/api/captures/cluster:acme/entries?node=NODE1,NODE9",
        exit=1,
        status=400,
    ),
    Case(
        "error-entries-node-of-a-set",
        ["entries", "acme_node1", "--node", "NODE1"],
        "/api/sets/acme_node1/entries?node=NODE1",
        exit=1,
        status=400,
    ),
    Case(
        "error-entries-signatures-of-a-set",
        ["entries", "acme_node1", "--signature", "{sig},{sig2}"],
        "/api/sets/acme_node1/entries?signature={sig},{sig2}",
        exit=1,
        status=400,
    ),
    Case(
        "error-merged-entries-no-group",
        ["entries", "cluster:acme", "--signature", "{sig},0000000000000000"],
        "/api/captures/cluster:acme/entries?signature={sig},0000000000000000",
        exit=2,
        status=404,
        reason="group_not_found",
    ),
    Case(
        "error-merged-entries-group-unknown-node",
        ["entries", "cluster:acme", "--signature", "NODE9:{sig}"],
        "/api/captures/cluster:acme/entries?signature=NODE9:{sig}",
        exit=1,
        status=400,
    ),
    Case(
        "error-entries-group-per-node-of-a-set",
        ["entries", "acme_node1", "--signature", "NODE1:{sig}"],
        "/api/sets/acme_node1/entries?signature=NODE1:{sig}",
        exit=1,
        status=400,
    ),
    Case(
        "error-matrix-no-group",
        ["matrix", "cluster:acme", "--signature", "0000000000000000"],
        "/api/captures/cluster:acme/matrix?signature=0000000000000000",
        exit=2,
        status=404,
        reason="group_not_found",
    ),
    Case(
        "error-events-unknown-kind",
        ["events", "cluster:acme", "--kind", "reboot"],
        "/api/captures/cluster:acme/events?kind=reboot",
        exit=1,
        status=400,
    ),
    Case(
        "error-context-no-capture",
        ["context", "acme_node1_prev", "0000000000000000", "--all-nodes"],
        "/api/sets/acme_node1_prev/entries/0000000000000000/context?all_nodes=1",
        exit=1,
        status=400,
    ),
    Case(
        "error-context-all-nodes-no-entry",
        ["context", "acme_node1", "0000000000000000", "--all-nodes"],
        "/api/sets/acme_node1/entries/0000000000000000/context?all_nodes=1",
        exit=2,
        status=404,
        reason="entry_not_found",
    ),
]

HTTP_CAPS: list[Case] = [
    # By design: one HTTP response holds a bounded page; the CLI writes any number of rows.
    Case(
        "groups-limit",
        ["groups", "edge", "--limit", "100001"],
        "/api/sets/edge/groups?limit=100001",
    ),
    Case(
        "entries-limit",
        ["entries", "edge", "--limit", "10001"],
        "/api/sets/edge/entries?limit=10001",
    ),
    # entries has no "all" page over HTTP: limit=0 is refused (follow next_offset), while
    # `ragnalog entries --limit 0` writes every entry.
    Case("entries-limit-0", ["entries", "edge", "--limit", "0"], "/api/sets/edge/entries?limit=0"),
    # nor has a group's entries: show --limit 0 writes them all, a response stays bounded
    Case(
        "show-limit-0",
        ["show", "edge", "{sig}", "--limit", "0"],
        "/api/sets/edge/groups/{sig}?limit=0",
    ),
    Case(
        "context-before",
        ["context", "edge", "{entry}", "--before", "10001"],
        "/api/sets/edge/entries/{entry}/context?before=10001",
    ),
]
"""Requests the API refuses (400 ``usage``) because they exceed a page cap; the CLI runs them."""


# --- store and front ends


def _copy_set(store: Store, ref: str, set_id: str, **changes: Any) -> None:
    """Copy a stored set under another id with manifest ``changes`` (no new ingest)."""
    src = store.open(ref)
    target = store.root / set_id
    shutil.copytree(src.path, target)
    manifest = dataclasses.replace(LogSetManifest.read(target), id=set_id, **changes)
    manifest.write(target)


@pytest.fixture(scope="module")
def store_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """``sample`` and ``edge`` from the fixtures, plus copies for the error cases.

    ``twin`` (two sets, one name, ids with a common prefix) and ``edge-f1`` (another
    signature spec) lie a year later, so neither is an automatic triage baseline. The
    twins are nodes ``NODE1`` and ``NODE2`` of capture ``cap-twin`` of cluster ``acme``.
    """
    root = tmp_path_factory.mktemp("parity") / "store"
    store = Store(root)
    ingest_paths(store, [FIXTURES / "sample.log"], name="sample")
    edge = ingest_paths(store, [FIXTURES / "edge_cases.log"], name="edge").logset.manifest
    assert edge.first_ts is not None and edge.last_ts is not None
    year = timedelta(days=365)
    twin = {"cluster": "acme", "capture": "cap-twin"}
    copies = {
        "aaaa000000000001": {"name": "twin", "node": "NODE1", **twin},
        "aaaa000000000002": {"name": "twin", "node": "NODE2", **twin},
        "bbbb000000000001": {
            "name": "edge-f1",
            "signature_spec": "L+G+E+F1",
            "recipe_key": (edge.recipe_key or "").replace("|L+G+E+F0|", "|L+G+E+F1|"),
        },
    }
    for k, (set_id, changes) in enumerate(copies.items(), start=1):
        # distinct created_at: listings sort by it, so ties would leave the order to the FS
        moved = {
            "first_ts": edge.first_ts + year,
            "last_ts": edge.last_ts + year,
            "created_at": edge.created_at + timedelta(seconds=k),
        }
        _copy_set(store, "edge", set_id, **changes, **moved)
    return root


@pytest.fixture(scope="module")
def client(store_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))) as c:
        yield c


@pytest.fixture(scope="module")
def cluster_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Capture ``cap-acme`` (cluster ``acme``): ``acme_node1``, ``acme_node2``; ``acme_node1_prev``.

    ``acme_node1_prev`` is an earlier NODE1 set in no capture: NODE1's triage baseline.
    """
    from tests.cluster import write_node_files

    root = tmp_path_factory.mktemp("cluster-parity")
    store = Store(root / "store")
    for node in ("NODE1", "NODE2"):
        name = f"acme_{node.lower()}"
        ingest_paths(store, [CLUSTER / node], name=name)
        store.label(name, node=node, cluster="acme", capture="cap-acme")
    prev = write_node_files(root / "prev", "NODE1", prefix=False, day="2026-11-01")
    ingest_paths(store, prev, name="acme_node1_prev")
    store.label("acme_node1_prev", node="NODE1", cluster="acme")
    return root / "store"


@pytest.fixture(scope="module")
def cluster_client(cluster_dir: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(store=cluster_dir, sync_jobs=True))) as c:
        yield c


@pytest.fixture(scope="module")
def cluster_refs(cluster_dir: Path) -> dict[str, str]:
    import polars as pl

    store = Store(cluster_dir)
    n1 = store.open("acme_node1")
    groups = n1.groups(contains="heartbeat", limit=1)
    sig = str(groups["signature"][0])
    n2 = store.open("acme_node2")
    sig2 = next(s for s in n2.groups(level="WARN,ERROR", limit=5)["signature"] if s != sig)
    inside = n1.entries_lazy().filter(pl.col("ts").dt.hour() == 11).select("id").head(1)
    return {
        "n1": n1.manifest.id,
        "sig": sig,
        "sig2": str(sig2),
        "pfx": sig[:6],
        "entry": str(inside.collect()["id"][0]),
    }


@pytest.fixture(scope="module")
def refs(store_dir: Path) -> dict[str, str]:
    store = Store(store_dir)
    edge = store.open("edge")
    groups = edge.groups(level="ERROR", with_exception=True, sort="signature", limit=1)
    sig = str(groups["signature"][0])
    entry = str(edge.entries(signature=sig, limit=1, columns=["id"])["id"][0])
    return {
        "edge": edge.manifest.id,
        "sample": store.open("sample").manifest.id,
        "sig": sig,
        "entry": entry,
    }


def _cli(
    store_dir: Path, argv: Sequence[str], capsys: pytest.CaptureFixture[str]
) -> tuple[int, Any]:
    capsys.readouterr()
    code = run(["--output", "json", "--store", str(store_dir), *argv])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def _api(client: TestClient, url: str) -> tuple[int, Any]:
    response = client.get(url)
    return response.status_code, response.json()


# --- comparison


def _drop(doc: Any, path: list[str]) -> None:
    if not path or not isinstance(doc, dict):
        return
    head, rest = path[0], path[1:]
    key, many = (head[:-2], True) if head.endswith("[]") else (head, False)
    if key not in doc:
        return
    if not rest:
        del doc[key]
    elif many and isinstance(doc[key], list):
        for item in doc[key]:
            _drop(item, rest)
    else:
        _drop(doc[key], rest)


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items() if k not in VOLATILE}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, str):
        return value.replace(API_MARKER, CLI_MARKER)
    return value


def _normalise(doc: Any, keys: frozenset[str]) -> Any:
    doc = _clean(doc)
    for path in keys:
        _drop(doc, path.split("."))
    return doc


def _identity(doc: Any) -> str:
    """What both front ends must agree on for an error: its reason, else its code."""
    error = doc["error"]
    return str(error.get("reason") or error["code"])


def _params() -> list[Any]:
    return [
        pytest.param(
            case,
            id=case.id,
            marks=[pytest.mark.xfail(strict=True, reason=case.drift)] if case.drift else [],
        )
        for case in CASES
    ]


@pytest.mark.parametrize("case", _params())
def test_cli_and_api_agree(
    case: Case,
    store_dir: Path,
    client: TestClient,
    refs: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("RAGNALOG_APP_PACKAGES", raising=False)
    agree(case, store_dir, client, refs, capsys)


@pytest.mark.parametrize("case", CLUSTER_CASES, ids=lambda c: c.id)
def test_capture_views_agree(
    case: Case,
    cluster_dir: Path,
    cluster_client: TestClient,
    cluster_refs: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    agree(case, cluster_dir, cluster_client, cluster_refs, capsys)


def agree(
    case: Case,
    store_dir: Path,
    client: TestClient,
    refs: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Run ``case`` on both front ends and compare (an error: reason, exit code, status)."""
    argv = [a.format(**refs) for a in case.cli]
    code, cli_doc = _cli(store_dir, argv, capsys)
    status, api_doc = _api(client, case.api.format(**refs))
    if case.exit is not None:
        assert (code, status) == (case.exit, case.status), (cli_doc, api_doc)
        assert _identity(cli_doc) == _identity(api_doc), (cli_doc, api_doc)
        if case.reason is not None:
            assert cli_doc["error"]["reason"] == case.reason
        return
    assert (code, status) == (0, 200), (cli_doc, api_doc)
    keys = case.allowed | case.masked
    assert _normalise(cli_doc, keys) == _normalise(api_doc, keys)


# --- row serialisers: the CLI's jsonable_rows and the API's to_records + JSON


def _frames() -> list[Any]:
    import polars as pl

    aware = datetime(2026, 10, 29, 9, 0, 0, 123000, tzinfo=UTC)
    return [
        pytest.param(pl.DataFrame({"t": [aware, None]}), id="aware-datetime"),
        pytest.param(pl.DataFrame({"d": [aware.date()]}), id="date"),
        # naive values are taken as UTC by both (one serialiser, store.logset.to_records)
        pytest.param(pl.DataFrame({"t": [aware.replace(tzinfo=None)]}), id="naive-datetime"),
    ]


@pytest.mark.parametrize("frame", _frames())
def test_row_serialisers_agree(frame: Any) -> None:
    from ragnalog.cli.output import encode_json, jsonable_rows
    from ragnalog.store.logset import to_records
    from ragnalog.webapp.responses import JSON

    cli_rows = json.loads(encode_json(jsonable_rows(frame)))
    api_rows = json.loads(JSON(to_records(frame)).body)
    assert cli_rows == api_rows


@pytest.mark.parametrize("case", HTTP_CAPS, ids=lambda c: c.id)
def test_http_page_caps(
    case: Case,
    store_dir: Path,
    client: TestClient,
    refs: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    code, _ = _cli(store_dir, [a.format(**refs) for a in case.cli], capsys)
    status, api_doc = _api(client, case.api.format(**refs))
    assert (code, status, api_doc["error"]["code"]) == (0, 400, "usage")


# --- label: a POST, compared on a store of its own


@pytest.fixture
def label_store(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    ingest_paths(Store(root), [FIXTURES / "sample.log"], name="sample")
    return root


LABEL_CASES = [
    # (id, CLI options, API query, expected exit, status, reason)
    ("label", ["--node", "NODE1", "--cluster", "acme"], "node=NODE1&cluster=acme", 0, 200, None),
    ("label-clear", ["--node", ""], "node=", 0, 200, None),
    ("label-capture", ["--capture", "cap-1"], "capture=cap-1", 0, 200, None),
    ("label-nothing", [], "", 1, 400, None),
    ("label-bad-value", ["--node", "NODE 1"], "node=NODE%201", 1, 400, None),
]


@pytest.mark.parametrize(
    ("argv", "query", "exit", "status", "reason"),
    [pytest.param(*case[1:], id=case[0]) for case in LABEL_CASES],
)
def test_label_cli_and_api_agree(
    label_store: Path,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    query: str,
    exit: int,
    status: int,
    reason: str | None,
) -> None:
    code, cli_doc = _cli(label_store, ["label", "sample", *argv], capsys)
    with TestClient(create_app(Settings(store=label_store, sync_jobs=True))) as client:
        response = client.post(f"/api/sets/sample/label?{query}")
    assert (code, response.status_code) == (exit, status), (cli_doc, response.json())
    if exit:
        assert _identity(cli_doc) == _identity(response.json())
        return
    assert _normalise(cli_doc, frozenset()) == _normalise(response.json(), frozenset())


def test_label_unknown_set_agrees(label_store: Path, capsys: pytest.CaptureFixture[str]) -> None:
    code, cli_doc = _cli(label_store, ["label", "nope", "--node", "NODE1"], capsys)
    with TestClient(create_app(Settings(store=label_store, sync_jobs=True))) as client:
        response = client.post("/api/sets/nope/label", data={"node": "NODE1"})
    assert (code, response.status_code) == (3, 404)
    assert cli_doc["error"]["reason"] == response.json()["error"]["reason"] == "set_not_found"
