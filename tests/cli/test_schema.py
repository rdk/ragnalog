"""`schema NAME`: stored tables and the JSON Schemas of the documents."""

from __future__ import annotations

from ragnalog.service.documents import document_names, document_schema

from .conftest import Invoke


def test_schema_without_name_lists_the_documents(jcli: Invoke) -> None:
    doc = jcli("schema").json()
    assert set(doc["definitions"]) == {"entries", "groups", "merges", "logset"}
    assert doc["documents"] == document_names()
    assert {"triage.v1", "error.v1", "manifest.v1"} <= set(doc["documents"])


def test_schema_of_a_document(jcli: Invoke) -> None:
    doc = jcli("schema", "triage.v1").json()
    assert doc["schema"] == "schema.v1" and "documents" not in doc
    schema = doc["definitions"]["triage.v1"]
    assert schema == document_schema("triage.v1")
    assert schema["properties"]["schema"] == {"enum": ["triage.v1"]}
    assert schema["properties"]["new"]["items"] == {"$ref": "#/$defs/DiffRow"}


def test_schema_of_a_table_is_unchanged(jcli: Invoke) -> None:
    doc = jcli("schema", "entries").json()
    assert doc["definitions"]["entries"]["schema"] == "entries.v1"
    assert "fields" in doc["definitions"]["entries"]


def test_unknown_schema_names_the_choices(jcli: Invoke) -> None:
    result = jcli("schema", "triage")
    assert result.code == 1
    err = result.json()["error"]
    assert err["code"] == "usage" and '"entries"' in err["message"]
    assert "triage.v1" in err["message"] and err["hint"] == "ragnalog schema --help"
