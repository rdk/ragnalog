"""The documents in the OpenAPI schema, and ``GET /api/manifest``.

FastAPI generates the operations from the routes of :mod:`.api`; their
``dict`` return types say nothing about the documents. :func:`install` adds
the document schemas of :mod:`ragnalog.service.documents` as
components, points each operation's success response at its document
(:data:`API_DOCUMENTS`) and documents errors as ``error.v1`` (``400`` for a
bad parameter, not FastAPI's ``422``).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, Final

from fastapi import APIRouter, FastAPI

from ragnalog.service import manifest as service_manifest
from ragnalog.service.documents import components
from ragnalog.webapp import api
from ragnalog.webapp.responses import JSON

__all__ = ["API_DOCUMENTS", "install", "router"]

API_DOCUMENTS: Final[dict[tuple[str, str], str]] = {
    ("get", "/api/health"): "health.v1",
    ("get", "/api/version"): "version.v1",
    ("get", "/api/manifest"): "manifest.v1",
    ("get", "/api/manifest/{command}"): "manifest-command.v1",
    ("get", "/api/formats"): "formats.v1",
    ("get", "/api/sets"): "sets.v1",
    ("get", "/api/sets/{ref}"): "logset.v1",
    ("delete", "/api/sets/{ref}"): "remove.v1",
    ("post", "/api/sets/{ref}/label"): "logset.v1",
    ("get", "/api/captures"): "captures.v1",
    ("get", "/api/captures/{ref}"): "capture.v1",
    ("get", "/api/captures/{ref}/timeline"): "cluster-timeline.v1",
    ("get", "/api/captures/{ref}/matrix"): "matrix.v1",
    ("get", "/api/captures/{ref}/events"): "events.v1",
    ("get", "/api/captures/{ref}/interactions"): "interactions.v1",
    ("get", "/api/captures/{ref}/entries"): "entries.v1",
    ("get", "/api/captures/{ref}/report"): "cluster-report.v1",
    ("get", "/api/sets/{ref}/stats"): "stats.v1",
    ("get", "/api/sets/{ref}/groups"): "groups.v1",
    ("get", "/api/sets/{ref}/groups/{signature}"): "group.v1",
    ("get", "/api/sets/{ref}/groups/{signature}/examples"): "report-entries.v1",
    ("get", "/api/sets/{ref}/entries"): "entries.v1",
    ("get", "/api/sets/{ref}/entries/{entry_id}/context"): "entries.v1",
    ("get", "/api/sets/{ref}/frames/{item}"): "frames.v1",
    ("get", "/api/sets/{ref}/timeline"): "timeline.v1",
    ("get", "/api/sets/{ref}/users"): "users.v1",
    ("post", "/api/sets/{ref}/query"): "query.v1",
    ("post", "/api/captures/{ref}/query"): "query.v1",
    ("get", "/api/sets/{ref}/report"): "report.v1",
    ("get", "/api/diff"): "diff.v1",
    ("get", "/api/sets/{ref}/triage"): "triage.v1",
    ("post", "/api/ingest"): "job.v1",
    ("get", "/api/jobs"): "jobs.v1",
    ("get", "/api/jobs/{job_id}"): "job.v1",
    ("post", "/api/jobs/{job_id}/cancel"): "job.v1",
}
"""Document of the success response of each ``/api`` operation."""

REF = "#/components/schemas/{name}"
_VALIDATION = ("HTTPValidationError", "ValidationError")

router = APIRouter(prefix="/api", default_response_class=JSON, dependencies=api.router.dependencies)


@router.get("/manifest")
def manifest() -> dict[str, Any]:
    """How an agent uses ragnalog (``manifest.v1``), as ``ragnalog manifest``."""
    return service_manifest.manifest_document()


@router.get("/manifest/{command}")
def manifest_command(command: str) -> dict[str, Any]:
    """One command in full (``manifest-command.v1``), as ``ragnalog manifest COMMAND``."""
    return service_manifest.command_document(command)


def _ref(name: str) -> dict[str, str]:
    return {"$ref": REF.format(name=name)}


def document(schema: dict[str, Any]) -> dict[str, Any]:
    """Add the documents to a generated OpenAPI ``schema`` (in place) and return it."""
    schemas = schema.setdefault("components", {}).setdefault("schemas", {})
    schemas.update(components(REF))
    error = {
        "description": "Error (error.v1)",
        "content": {"application/json": {"schema": _ref("error.v1")}},
    }
    for path, operations in schema.get("paths", {}).items():
        if not path.startswith("/api/"):
            continue
        for method, operation in operations.items():
            responses = operation.setdefault("responses", {})
            responses.pop("422", None)
            responses["default"] = error
            name = API_DOCUMENTS.get((method, path))
            if name is None:
                continue
            status = next((s for s in responses if s.startswith("2")), "200")
            success = responses.setdefault(status, {"description": "Successful Response"})
            success["description"] = name
            success["content"] = {"application/json": {"schema": _ref(name)}}
    if REF.format(name=_VALIDATION[0]) not in str(schema.get("paths")):
        for name in _VALIDATION:  # FastAPI's 422 body; the app answers 400 error.v1 instead
            schemas.pop(name, None)
    return schema


def install(app: FastAPI) -> None:
    """Make ``app.openapi()`` (``/openapi.json``, ``/docs``) return :func:`document`'s schema."""
    generate: Callable[[], dict[str, Any]] = app.openapi

    def openapi() -> dict[str, Any]:
        if app.openapi_schema is None:
            app.openapi_schema = document(generate())
        return app.openapi_schema

    app.openapi = openapi  # type: ignore[method-assign]
