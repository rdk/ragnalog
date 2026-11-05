"""CLI ↔ API parity: ``atlas-log --output json`` and ``/api`` return the same documents.

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

from atlas_log_analyzer.cli.app import run
from atlas_log_analyzer.store.logset import LogSetManifest
from atlas_log_analyzer.store.pipeline import ingest_paths
from atlas_log_analyzer.store.store import Store
from atlas_log_analyzer.webapp import Settings, create_app

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "jira"

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
    # `atlas-log entries --limit 0` writes every entry.
    Case("entries-limit-0", ["entries", "edge", "--limit", "0"], "/api/sets/edge/entries?limit=0"),
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
    signature spec) lie a year later, so neither is an automatic triage baseline.
    """
    root = tmp_path_factory.mktemp("parity") / "store"
    store = Store(root)
    ingest_paths(store, [FIXTURES / "sample.log"], name="sample")
    edge = ingest_paths(store, [FIXTURES / "edge_cases.log"], name="edge").logset.manifest
    assert edge.first_ts is not None and edge.last_ts is not None
    year = timedelta(days=365)
    copies = {
        "aaaa000000000001": {"name": "twin"},
        "aaaa000000000002": {"name": "twin"},
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
    monkeypatch.delenv("ATLAS_LOG_APP_PACKAGES", raising=False)
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
    from atlas_log_analyzer.cli.output import encode_json, jsonable_rows
    from atlas_log_analyzer.store.logset import to_records
    from atlas_log_analyzer.webapp.responses import JSON

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
