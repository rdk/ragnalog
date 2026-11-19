"""``query_model``: the service's request dataclasses read from the query string."""

from __future__ import annotations

from dataclasses import asdict
from typing import Annotated, Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient

from ragnalog.service.params import EntryQuery, Select
from ragnalog.webapp.params import field_docs, query_model


@pytest.fixture(scope="module")
def client() -> TestClient:
    app = FastAPI()

    @app.get("/echo")
    def echo(
        q: Annotated[EntryQuery, Depends(query_model(EntryQuery))],
        sel: Annotated[Select, Depends(query_model(Select))],
    ) -> dict[str, Any]:
        return {"q": asdict(q), "sel": asdict(sel)}

    return TestClient(app)


def test_defaults(client: TestClient) -> None:
    doc = client.get("/echo").json()
    assert doc["q"] == {f: None for f in doc["q"]} | {"with_exception": False}
    assert doc["sel"] == {"columns": None, "brief": False}


def test_values_and_repeated_lists(client: TestClient) -> None:
    url = "/echo?level=WARN&with_exception=true&columns=id,ts&columns=message&brief=1"
    doc = client.get(url).json()
    assert doc["q"]["level"] == "WARN" and doc["q"]["with_exception"] is True
    assert doc["sel"] == {"columns": ["id,ts", "message"], "brief": True}


def test_openapi_has_field_descriptions(client: TestClient) -> None:
    params = {
        p["name"]: p
        for p in client.get("/openapi.json").json()["paths"]["/echo"]["get"]["parameters"]
    }
    assert set(params) >= {"signature", "request_id", "since", "columns", "brief"}
    assert all(p["in"] == "query" for p in params.values())
    assert params["since"]["description"].startswith("Only entries at/after this time: ISO 8601")


def test_field_docs_joins_continuation_lines() -> None:
    docs = field_docs(EntryQuery)
    assert docs["since"].endswith("30m/2h/1d before the set's last entry.")
    assert set(docs) == set(asdict(EntryQuery()))


def test_query_model_needs_a_dataclass() -> None:
    with pytest.raises(TypeError):
        query_model(int)
