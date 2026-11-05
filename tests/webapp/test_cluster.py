"""Cluster logs in the web app: export ZIP uploads, labels, captures and the sets page."""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient

from tests.cluster import JOB_ID, export_zip


def _upload(client: TestClient, archive: Path, **form: str) -> dict:  # type: ignore[type-arg]
    posted = client.post(
        "/api/ingest",
        files=[("files", (archive.name, archive.read_bytes(), "application/zip"))],
        data=form,
    )
    assert posted.status_code == 202, posted.json()
    job: dict = client.get(f"/api/jobs/{posted.json()['id']}").json()  # type: ignore[type-arg]
    return job


def test_export_zip_upload_creates_a_capture(
    client: TestClient, store_dir: Path, tmp_path: Path
) -> None:
    job = _upload(client, export_zip(tmp_path / "export.zip"), cluster="jira-test")
    assert job["state"] == "succeeded", job["error"]
    assert job["options"]["cluster"] == "jira-test" and job["options"]["split"] == "auto"
    capture = job["capture"]
    assert capture["schema"] == "ingest-capture.v1"
    assert capture["capture"]["capture"] == JOB_ID
    assert [s["node"] for s in capture["sets"]] == ["NODE1", "NODE2"]
    assert job["result"] == capture["sets"][0]
    assert job["progress"]["entries"] == sum(s["entry_count"] for s in capture["sets"])
    assert not list((store_dir / ".uploads").iterdir()) and not (store_dir / ".spool").exists()
    listed = client.get("/api/captures").json()
    assert listed["count"] == 1 and listed["items"][0]["cluster"] == "jira-test"
    brief = client.get("/api/captures?brief=true").json()["items"][0]["nodes"][0]
    assert set(brief) == {"node", "id", "name"}
    sets = client.get("/api/sets?brief=true").json()["items"]
    assert {s["node"] for s in sets} == {"NODE1", "NODE2", None}


def test_upload_options_split_none_and_errors(client: TestClient, tmp_path: Path) -> None:
    archive = export_zip(tmp_path / "export.zip")
    job = _upload(client, archive, split="none", node="MIXED")
    assert job["state"] == "succeeded" and "capture" not in job
    assert job["result"]["node"] == "MIXED" and len(job["result"]["sources"]) == 6
    refused = _upload(client, archive, node="NODE1")
    assert refused["state"] == "failed" and refused["error"]["code"] == "invalid_value"
    bad = client.post(
        "/api/ingest?split=sideways",
        files=[("files", ("a.log", b"x\n", "text/plain"))],
    )
    assert bad.status_code == 400
    bad_form = client.post(
        "/api/ingest", files=[("files", ("a.log", b"x\n", "text/plain"))], data={"node": "a b"}
    )
    assert bad_form.status_code == 400 and bad_form.json()["error"]["code"] == "invalid_value"


def test_label_route(client: TestClient) -> None:
    before = client.get("/api/sets/mini/report").json()["logset"]
    assert before["node"] is None
    doc = client.post("/api/sets/mini/label?node=NODE1&cluster=acme").json()
    assert doc["schema"] == "logset.v1" and (doc["node"], doc["cluster"]) == ("NODE1", "acme")
    # the cached report payload holds the manifest: labelling drops it
    assert client.get("/api/sets/mini/report").json()["logset"]["node"] == "NODE1"
    cleared = client.post("/api/sets/mini/label", data={"cluster": "", "capture": "c-1"}).json()
    assert (cleared["node"], cleared["cluster"], cleared["capture"]) == ("NODE1", None, "c-1")
    assert client.post("/api/sets/mini/label").status_code == 400
    missing = client.post("/api/sets/nope/label?node=NODE1")
    assert missing.status_code == 404 and missing.json()["error"]["reason"] == "set_not_found"
