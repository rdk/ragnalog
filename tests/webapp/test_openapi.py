"""``/openapi.json`` describes the documents; ``GET /api/manifest`` is ``manifest.v1``."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from atlas_log_analyzer.service.documents import DOCUMENTS, ROW_COMPONENTS
from atlas_log_analyzer.service.manifest import command_document, manifest_document
from atlas_log_analyzer.webapp import Settings, create_app
from atlas_log_analyzer.webapp.openapi import API_DOCUMENTS


def _openapi(client: TestClient) -> dict[str, Any]:
    response = client.get("/openapi.json")
    assert response.status_code == 200
    doc: dict[str, Any] = response.json()
    return doc


def _refs(node: Any) -> set[str]:
    if isinstance(node, list):
        return set().union(*map(_refs, node)) if node else set()
    if not isinstance(node, dict):
        return set()
    found = set().union(*map(_refs, node.values())) if node else set()
    if isinstance(node.get("$ref"), str):
        found.add(node["$ref"])
    return found


def test_openapi_has_the_document_components(empty_client: TestClient) -> None:
    schemas = _openapi(empty_client)["components"]["schemas"]
    assert set(DOCUMENTS) <= set(schemas)
    assert set(ROW_COMPONENTS.values()) <= set(schemas)
    assert schemas["triage.v1"]["properties"]["schema"] == {"enum": ["triage.v1"]}
    assert schemas["groups.v1"]["properties"]["items"]["items"] == {
        "$ref": "#/components/schemas/GroupRow"
    }


def test_operations_reference_their_documents(empty_client: TestClient) -> None:
    paths = _openapi(empty_client)["paths"]
    triage = paths["/api/sets/{ref}/triage"]["get"]["responses"]
    assert triage["200"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/triage.v1"
    }
    ingest = paths["/api/ingest"]["post"]["responses"]
    assert ingest["202"]["content"]["application/json"]["schema"]["$ref"].endswith("/job.v1")
    assert triage["default"]["content"]["application/json"]["schema"]["$ref"].endswith("/error.v1")
    assert "422" not in triage


def test_every_api_operation_has_a_document(empty_client: TestClient) -> None:
    paths = _openapi(empty_client)["paths"]
    operations = {
        (method, path) for path, ops in paths.items() if path.startswith("/api/") for method in ops
    }
    assert operations == set(API_DOCUMENTS)
    assert set(API_DOCUMENTS.values()) <= set(DOCUMENTS)


def test_every_reference_resolves(empty_client: TestClient) -> None:
    doc = _openapi(empty_client)
    schemas = doc["components"]["schemas"]
    for ref in _refs(doc):
        assert ref.startswith("#/components/schemas/"), ref
        assert ref.removeprefix("#/components/schemas/") in schemas, ref


def test_manifest_route_is_the_cli_manifest(empty_client: TestClient) -> None:
    response = empty_client.get("/api/manifest")
    assert response.status_code == 200
    assert response.json() == manifest_document()
    assert response.json()["schema"] == "manifest.v1"
    one = empty_client.get("/api/manifest/frames")
    assert one.status_code == 200 and one.json() == command_document("frames")
    missing = empty_client.get("/api/manifest/nope")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "not_found"


def test_manifest_route_needs_the_token(tmp_path: Path) -> None:
    settings = Settings(store=tmp_path / "store", token="s3cret", sync_jobs=True)
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/manifest").status_code == 401
        ok = client.get("/api/manifest", headers={"Authorization": "Bearer s3cret"})
        assert ok.status_code == 200 and ok.json()["schema"] == "manifest.v1"
