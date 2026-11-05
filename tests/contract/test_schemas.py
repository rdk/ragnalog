"""Every document of the CLI and the API matches its Struct and its generated JSON Schema.

The parity cases of ``test_parity.py`` (same store, same requests) supply most
documents; :data:`EXTRA` covers the documents those cases do not produce. Each
document is checked three ways:

- ``msgspec.convert`` into the Struct its ``schema`` names (types, required keys);
- converting back gives the same JSON, so no key is missing from the Struct;
- it validates against ``document_schema(name)``, which checks the rows too.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import msgspec
import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from atlas_log_analyzer.service.documents import DOCUMENTS, convert, decode, document_schema
from atlas_log_analyzer.webapp import Settings, create_app

from . import test_parity as parity

# the parity stores and front ends (module-scoped: this module gets its own stores)
store_dir = parity.store_dir
client = parity.client
refs = parity.refs
cluster_dir = parity.cluster_dir
cluster_client = parity.cluster_client
cluster_refs = parity.cluster_refs


# --- a small JSON Schema validator: the keywords msgspec and document_schema emit


def _type_ok(value: Any, name: str) -> bool:
    match name:
        case "string":
            return isinstance(value, str)
        case "integer":
            return isinstance(value, int) and not isinstance(value, bool)
        case "number":
            return isinstance(value, int | float) and not isinstance(value, bool)
        case "boolean":
            return isinstance(value, bool)
        case "null":
            return value is None
        case "array":
            return isinstance(value, list)
        case "object":
            return isinstance(value, dict)
    raise AssertionError(f"unknown type {name!r}")


def validate(value: Any, schema: dict[str, Any], root: dict[str, Any], path: str = "$") -> None:
    """Raise ``AssertionError`` naming the first place where ``value`` breaks ``schema``."""
    if "$ref" in schema:
        name = schema["$ref"].removeprefix("#/$defs/")
        validate(value, root["$defs"][name], root, path)
    if "anyOf" in schema:
        errors = []
        for option in schema["anyOf"]:
            try:
                validate(value, option, root, path)
                break
            except AssertionError as exc:
                errors.append(str(exc))
        else:
            raise AssertionError(f"{path}: no anyOf option matches: {errors}")
    if "enum" in schema:
        assert value in schema["enum"], f"{path}: {value!r} not in {schema['enum']}"
    if "const" in schema:
        assert value == schema["const"], f"{path}: {value!r} != {schema['const']!r}"
    if "type" in schema:
        types = schema["type"] if isinstance(schema["type"], list) else [schema["type"]]
        assert any(_type_ok(value, t) for t in types), f"{path}: {value!r} is not {types}"
    if isinstance(value, dict):
        props = schema.get("properties", {})
        for key in schema.get("required", []):
            assert key in value, f"{path}: missing {key!r}"
        extra = schema.get("additionalProperties")
        for key, item in value.items():
            if key in props:
                validate(item, props[key], root, f"{path}.{key}")
            elif isinstance(extra, dict):
                validate(item, extra, root, f"{path}.{key}")
    if isinstance(value, list):
        prefix = schema.get("prefixItems", [])
        for i, item in enumerate(value):
            if i < len(prefix):
                validate(item, prefix[i], root, f"{path}[{i}]")
            elif "items" in schema:
                validate(item, schema["items"], root, f"{path}[{i}]")


def check(doc: Any) -> str:
    """Check one decoded document all three ways; return its schema name."""
    assert isinstance(doc, dict) and doc.get("schema") in DOCUMENTS, doc
    typed = convert(doc)
    again = json.loads(msgspec.json.encode(typed))  # tuples come back as lists, as in doc
    assert again == doc, "a key of the document is not in its Struct"
    assert type(decode(json.dumps(doc))) is type(typed)
    root = document_schema(doc["schema"])
    validate(doc, root, root)
    return str(doc["schema"])


# --- parity cases


@pytest.mark.parametrize("case", [*parity.CASES, *parity.HTTP_CAPS], ids=lambda c: c.id)
def test_parity_documents_match_their_structs(
    case: parity.Case,
    store_dir: Path,
    client: TestClient,
    refs: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("ATLAS_LOG_APP_PACKAGES", raising=False)
    _, cli_doc = parity._cli(store_dir, [a.format(**refs) for a in case.cli], capsys)
    _, api_doc = parity._api(client, case.api.format(**refs))
    check(cli_doc)
    check(api_doc)


@pytest.mark.parametrize("case", parity.CLUSTER_CASES, ids=lambda c: c.id)
def test_capture_view_documents_match_their_structs(
    case: parity.Case,
    cluster_dir: Path,
    cluster_client: TestClient,
    cluster_refs: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    _, cli_doc = parity._cli(cluster_dir, [a.format(**cluster_refs) for a in case.cli], capsys)
    _, api_doc = parity._api(cluster_client, case.api.format(**cluster_refs))
    check(cli_doc)
    check(api_doc)


def test_cluster_report_documents(
    cluster_dir: Path,
    cluster_client: TestClient,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    payload = cluster_client.get("/api/captures/cluster:acme/report?examples=2").json()
    assert check(payload) == "cluster-report.v1"
    assert payload["urls"]["entries"] == "/api/captures/capture:cap-acme/entries"
    out = tmp_path / "c.html"
    _, doc = parity._cli(cluster_dir, ["report", "cluster:acme", "--out", str(out)], capsys)
    assert check(doc) == "report-file.v1"
    assert (doc["capture"], doc["nodes"], doc["compare"]) == ("cap-acme", 2, False)


# --- the documents the parity cases do not produce


@dataclass(frozen=True)
class Extra:
    schema: str
    cli: Sequence[str] = ()
    api: str | None = None


EXTRA: list[Extra] = [
    Extra("query.v1", cli=["query", "edge", "select level, count(*) n from entries group by 1"]),
    Extra("schema.v1", cli=["schema"]),
    Extra("schema.v1", cli=["schema", "entries"]),
    Extra("schema.v1", cli=["schema", "triage.v1"]),
    Extra("manifest.v1", cli=["manifest"], api="/api/manifest"),
    Extra("manifest-command.v1", cli=["manifest", "frames"], api="/api/manifest/frames"),
    Extra("health.v1", api="/api/health"),
    Extra("timeline.v1", api="/api/sets/edge/timeline"),
    Extra("report.v1", api="/api/sets/edge/report?compare=sample"),
    Extra("report-entries.v1", api="/api/sets/edge/groups/{sig}/examples"),
    Extra("entries.v1", cli=["show", "edge", "{sig}", "--brief"]),
    Extra(
        "groups.v1", cli=["groups", "sample", "--coarse"], api="/api/sets/sample/groups?coarse=1"
    ),
    Extra("sets.v1", cli=["sets", "--brief"], api="/api/sets?brief=true"),
    Extra("formats.v1", cli=["formats", "--sample", str(parity.FIXTURES / "sample.log")]),
    Extra("error.v1", cli=["schema", "bogus"], api="/api/jobs/nope"),
]


@pytest.mark.parametrize("extra", EXTRA, ids=lambda e: f"{e.schema}:{' '.join(e.cli) or e.api}")
def test_other_documents_match_their_structs(
    extra: Extra,
    store_dir: Path,
    client: TestClient,
    refs: dict[str, str],
    capsys: pytest.CaptureFixture[str],
) -> None:
    if extra.cli:
        _, doc = parity._cli(store_dir, [a.format(**refs) for a in extra.cli], capsys)
        assert check(doc) == extra.schema
    if extra.api is not None:
        _, doc = parity._api(client, extra.api.format(**refs))
        assert check(doc) == extra.schema


def test_report_file_document(
    store_dir: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = tmp_path / "r.html"
    _, doc = parity._cli(store_dir, ["report", "edge", "--out", str(out)], capsys)
    assert check(doc) == "report-file.v1"


@pytest.fixture
def upload_client(tmp_path: Path) -> Iterator[TestClient]:
    with TestClient(create_app(Settings(store=tmp_path / "store", sync_jobs=True))) as c:
        yield c


def test_ingest_document(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    store = tmp_path / "store"
    argv = ["ingest", str(parity.FIXTURES / "sample.log"), "--name", "s", "--mine"]
    _, doc = parity._cli(store, argv, capsys)
    assert check(doc) == "logset.v1"
    assert doc["ingest"]["existing"] is False and "merges" in doc
    _, again = parity._cli(store, ["ingest", str(parity.FIXTURES / "sample.log")], capsys)
    assert check(again) == "logset.v1" and again["ingest"]["existing"] is True
    _, regrouped = parity._cli(store, ["regroup", "s", "--frames", "1"], capsys)
    assert check(regrouped) == "logset.v1"
    _, upgraded = parity._cli(store, ["upgrade", "--all"], capsys)
    assert check(upgraded) == "upgrade.v1" and upgraded["items"][0]["reason"] == "current"
    _, listed = parity._cli(store, ["sets"], capsys)
    assert check(listed) == "sets.v1" and listed["items"][0]["stale"] is False
    _, removed = parity._cli(store, ["remove", "s"], capsys)
    assert check(removed) == "remove.v1"


def test_split_ingest_documents(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], upload_client: TestClient
) -> None:
    from tests.cluster import export_zip

    archive = export_zip(tmp_path / "export.zip")
    _, doc = parity._cli(tmp_path / "store", ["ingest", str(archive), "--cluster", "acme"], capsys)
    assert check(doc) == "ingest-capture.v1"
    assert all(check(item) == "logset.v1" for item in doc["sets"])
    _, label = parity._cli(tmp_path / "store", ["label", "acme_node1", "--node", "NODE1"], capsys)
    assert check(label) == "logset.v1"
    posted = upload_client.post(
        "/api/ingest", files=[("files", ("export.zip", archive.read_bytes(), "application/zip"))]
    )
    job = upload_client.get(f"/api/jobs/{posted.json()['id']}").json()
    assert check(job) == "job.v1" and job["state"] == "succeeded", job
    assert job["capture"]["schema"] == "ingest-capture.v1"
    assert job["result"]["id"] == job["capture"]["sets"][0]["id"]
    assert check(upload_client.get("/api/captures").json()) == "captures.v1"
    labelled = upload_client.post(f"/api/sets/{job['result']['id']}/label", data={"cluster": "x"})
    assert check(labelled.json()) == "logset.v1"


def test_job_documents(upload_client: TestClient) -> None:
    data = (parity.FIXTURES / "edge_cases.log").read_bytes()
    posted = upload_client.post(
        "/api/ingest?name=up", files=[("files", ("edge_cases.log", data, "text/plain"))]
    )
    assert posted.status_code == 202
    assert check(posted.json()) == "job.v1"
    job = upload_client.get(f"/api/jobs/{posted.json()['id']}").json()
    assert check(job) == "job.v1" and job["state"] == "succeeded"
    assert check(job["result"]) == "logset.v1"
    assert check(upload_client.get("/api/jobs").json()) == "jobs.v1"
    assert check(upload_client.get("/api/sets/up").json()) == "logset.v1"
    assert check(upload_client.delete("/api/sets/up").json()) == "remove.v1"
    failed = upload_client.post(
        "/api/ingest", files=[("files", ("x.log", b"not a log line\n", "text/plain"))]
    )
    doc = failed.json()
    assert check(doc) in ("job.v1", "error.v1")


PARITY_DOCUMENTS = {
    "sets.v1",
    "captures.v1",
    "logset.v1",
    "version.v1",
    "formats.v1",
    "stats.v1",
    "groups.v1",
    "group.v1",
    "entries.v1",
    "frames.v1",
    "diff.v1",
    "triage.v1",
    "error.v1",
}
"""Documents that the parity cases produce."""

CLUSTER_DOCUMENTS = {
    "capture.v1",
    "cluster-timeline.v1",
    "timeline.v1",
    "matrix.v1",
    "events.v1",
    "interactions.v1",
    "entries.v1",
    "error.v1",
}
"""Documents that the capture view cases (``parity.CLUSTER_CASES``) produce."""


def test_every_document_has_a_case() -> None:
    others = {e.schema for e in EXTRA} | {"report-file.v1", "job.v1", "jobs.v1", "remove.v1"}
    others.add("upgrade.v1")  # test_ingest_document
    others.add("ingest-capture.v1")  # test_split_ingest_documents
    others.add("cluster-report.v1")  # test_cluster_report_documents
    produced = {
        str(c.cli[0]) for c in parity.CLUSTER_CASES if c.exit is None
    }  # every capture view command has a case
    assert {"capture", "timeline", "matrix", "events", "interactions", "entries"} <= produced
    assert PARITY_DOCUMENTS | CLUSTER_DOCUMENTS | others == set(DOCUMENTS)
