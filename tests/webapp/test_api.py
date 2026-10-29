"""JSON API: documents mirror the CLI's ``--output json`` shapes."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient

from atlas_log_analyzer.webapp import Settings, create_app

from .conftest import MINI


def _json(client: TestClient, url: str, status: int = 200) -> dict[str, Any]:
    res = client.get(url)
    assert res.status_code == status, res.text
    doc: dict[str, Any] = res.json()
    return doc


def _first_signature(client: TestClient, ref: str = "mini") -> str:
    return str(_json(client, f"/api/sets/{ref}/groups?limit=1")["items"][0]["signature"])


def test_health_and_version(client: TestClient) -> None:
    assert _json(client, "/api/health") == {"schema": "health.v1", "status": "ok", "sets": 2}
    doc = _json(client, "/api/version")
    assert doc["schema"] == "version.v1"
    assert {"version", "schema_version", "signature_version", "mask_rules_version"} <= doc.keys()


def test_formats(client: TestClient) -> None:
    doc = _json(client, "/api/formats")
    assert doc["schema"] == "formats.v1"
    assert "jira" in [f["name"] for f in doc["items"]]


def test_sets_list_with_summary(client: TestClient) -> None:
    doc = _json(client, "/api/sets")
    assert doc["schema"] == "sets.v1" and doc["count"] == 2
    names = {item["name"] for item in doc["items"]}
    assert names == {"mini", "sample"}
    for item in doc["items"]:
        assert "schema" not in item
        assert set(item["summary"]) == {"errors", "warnings", "error_groups"}


def test_empty_store(empty_client: TestClient) -> None:
    assert _json(empty_client, "/api/sets")["count"] == 0


def test_get_set_by_name_and_id(client: TestClient) -> None:
    by_name = _json(client, "/api/sets/mini")
    assert by_name["schema"] == "logset.v1"
    by_id = _json(client, f"/api/sets/{by_name['id']}")
    assert by_id["id"] == by_name["id"] and by_id["entry_count"] == by_name["entry_count"]


def test_unknown_set_is_error_doc(client: TestClient) -> None:
    doc = _json(client, "/api/sets/nope", 404)
    assert doc["schema"] == "error.v1"
    assert doc["error"]["code"] == "not_found"
    assert "hint" in doc["error"]
    assert "store" not in doc["error"]["message"].lower()


def test_groups_paging_and_totals(client: TestClient) -> None:
    full = _json(client, "/api/sets/sample/groups?limit=0")
    assert full["schema"] == "groups.v1" and full["count"] == full["total"] > 2
    assert full["truncated"] is False
    page = _json(client, "/api/sets/sample/groups?limit=2&offset=1")
    assert page["count"] == 2 and page["total"] == full["total"] and page["truncated"] is True
    assert [g["signature"] for g in page["items"]] == [g["signature"] for g in full["items"][1:3]]


def test_groups_level_filter_and_bad_level(client: TestClient) -> None:
    doc = _json(client, "/api/sets/sample/groups?min_level=ERROR&limit=0")
    assert doc["items"] and all(g["level"] >= 50 for g in doc["items"])
    bad = _json(client, "/api/sets/sample/groups?level=loud", 400)
    assert bad["error"]["code"] == "invalid_level"


def test_validation_error_is_usage(client: TestClient) -> None:
    doc = _json(client, "/api/sets/mini/groups?limit=-1", 400)
    assert doc["schema"] == "error.v1" and doc["error"]["code"] == "usage"


def test_relative_since(client: TestClient) -> None:
    all_groups = _json(client, "/api/sets/sample/groups?limit=0")["total"]
    recent = _json(client, "/api/sets/sample/groups?limit=0&since=1m")["total"]
    assert 0 < recent <= all_groups
    assert _json(client, "/api/sets/mini/groups?since=yesterday", 400)["error"]["code"] == (
        "invalid_value"
    )


def test_group_detail(client: TestClient) -> None:
    sig = _first_signature(client)
    doc = _json(client, f"/api/sets/mini/groups/{sig}")
    assert doc["schema"] == "group.v1"
    assert doc["group"]["signature"] == sig
    assert doc["items"] and all(e["signature"] == sig for e in doc["items"])
    assert sum(b["count"] for b in doc["histogram"]) == doc["group"]["count"]
    assert _json(client, "/api/sets/mini/groups/0000000000000000", 404)["error"]["code"] == (
        "not_found"
    )


def test_group_examples_in_report_shape(client: TestClient) -> None:
    sig = _first_signature(client)
    doc = _json(client, f"/api/sets/mini/groups/{sig}/examples")
    assert doc["schema"] == "report-entries.v1"
    assert doc["items"]
    entry = next(iter(doc["items"].values()))
    assert isinstance(entry["source"], int) and "raw" in entry and "source_path" not in entry


def test_entries_paging(client: TestClient) -> None:
    first = _json(client, "/api/sets/mini/entries?limit=4")
    assert first["schema"] == "entries.v1"
    assert first["count"] == 4 and first["total"] == 10 and first["next_offset"] == 4
    rest = _json(client, "/api/sets/mini/entries?limit=100&offset=4")
    assert rest["count"] == 6 and rest["next_offset"] is None
    ids = [e["id"] for e in first["items"] + rest["items"]]
    assert len(set(ids)) == 10


def test_entries_columns_and_contains(client: TestClient) -> None:
    doc = _json(client, "/api/sets/mini/entries?columns=ts&columns=level_name&limit=3")
    assert all(set(e) == {"ts", "level_name"} for e in doc["items"])
    hits = _json(client, "/api/sets/mini/entries?contains=nullpointer")
    assert hits["total"] >= 1


def test_stats_and_timeline(client: TestClient) -> None:
    stats = _json(client, "/api/sets/mini/stats")
    assert stats["schema"] == "stats.v1" and stats["entry_count"] == 10
    tl = _json(client, "/api/sets/mini/timeline?bucket=1h")
    assert tl["schema"] == "timeline.v1"
    assert sum(b["count"] for b in tl["items"]) == 10
    assert _json(client, "/api/sets/mini/timeline?bucket=xyz", 400)["error"]["code"] == (
        "invalid_value"
    )


def test_report_payload_cached(client: TestClient) -> None:
    doc = _json(client, "/api/sets/mini/report?compare=sample")
    assert doc["schema"] == "report.v1"
    assert doc["compare"]["other"]["name"] == "sample"
    assert doc["entries_url"].endswith("/groups/{sig}/examples")
    again = client.get("/api/sets/mini/report?compare=sample")
    assert again.json() == doc


def test_diff_same_set(client: TestClient) -> None:
    doc = _json(client, "/api/diff?a=mini&b=mini")
    assert doc["schema"] == "diff.v1"
    assert doc["new"] == doc["vanished"] == doc["changed"] == []
    groups = _json(client, "/api/sets/mini")["group_count"]
    assert doc["unchanged_count"] == groups


def test_triage_with_explicit_baseline(client: TestClient) -> None:
    doc = _json(client, "/api/sets/mini/triage?baseline=sample&min_level=WARN")
    assert doc["schema"] == "triage.v1"
    assert doc["baseline"]["name"] == "sample"
    assert doc["verdict"] in ("new_errors", "new_warnings", "clean")
    s = doc["summary"]
    assert s["new"] == len(doc["new"])
    assert all(r["report_url"].startswith("/sets/") for r in doc["new"])
    keys = [(r["level"], r["count_b"]) for r in doc["new"]]
    assert keys == sorted(keys, reverse=True)


def test_triage_against_itself_is_clean(client: TestClient) -> None:
    doc = _json(client, "/api/sets/mini/triage?baseline=mini")
    assert doc["verdict"] == "clean" and doc["summary"]["new"] == 0


def test_triage_without_baseline(empty_client: TestClient, tmp_path: Path) -> None:
    res = empty_client.post(
        "/api/ingest", files={"files": ("mini.log", MINI.read_bytes())}, data={"name": "only"}
    )
    assert res.status_code == 202
    doc = _json(empty_client, "/api/sets/only/triage")
    assert doc["verdict"] == "no_baseline" and doc["baseline"] is None


def test_ingest_upload_job(empty_client: TestClient) -> None:
    res = empty_client.post(
        "/api/ingest",
        files=[("files", ("../../etc/mini.log", MINI.read_bytes()))],
        data={"name": "uploaded"},
    )
    assert res.status_code == 202, res.text
    job = res.json()
    assert job["schema"] == "job.v1" and res.headers["location"] == f"/api/jobs/{job['id']}"
    assert job["state"] == "succeeded", job
    assert job["files"] == ["mini.log"]
    assert job["result"]["name"] == "uploaded" and job["result"]["entry_count"] == 10
    assert job["progress"]["fraction"] == 1.0
    assert _json(empty_client, f"/api/jobs/{job['id']}")["state"] == "succeeded"
    assert _json(empty_client, "/api/jobs")["count"] == 1
    assert _json(empty_client, "/api/sets")["count"] == 1


def test_ingest_garbage_fails_cleanly(empty_client: TestClient) -> None:
    res = empty_client.post("/api/ingest", files={"files": ("x.log", b"not a jira log\n" * 5)})
    job = res.json()
    assert job["state"] == "failed" and job["error"]["code"]
    assert _json(empty_client, "/api/sets")["count"] == 0


def test_upload_limit(tmp_path: Path) -> None:
    app = create_app(Settings(store=tmp_path / "s", max_upload_mb=1, sync_jobs=True))
    with TestClient(app) as c:
        res = c.post("/api/ingest", files={"files": ("big.log", b"x" * (1024 * 1024 + 10))})
        assert res.status_code == 413
        assert res.json()["error"]["code"] == "payload_too_large"
        assert not any((tmp_path / "s" / ".uploads").glob("*/*"))


def test_unknown_job(client: TestClient) -> None:
    assert _json(client, "/api/jobs/nope", 404)["error"]["code"] == "not_found"
    assert client.post("/api/jobs/nope/cancel").status_code == 404


def test_delete_set(client: TestClient) -> None:
    res = client.delete("/api/sets/mini")
    assert res.status_code == 200 and res.json()["schema"] == "remove.v1"
    assert _json(client, "/api/sets")["count"] == 1
    _json(client, "/api/sets/mini", 404)


def test_token_required(store_dir: Path) -> None:
    with TestClient(create_app(Settings(store=store_dir, token="s3cret"))) as c:
        assert c.get("/api/sets").status_code == 401
        assert c.get("/api/sets", headers={"Authorization": "Bearer s3cret"}).status_code == 200
        assert c.get("/api/sets", headers={"X-Atlas-Token": "s3cret"}).status_code == 200


def test_triage_auto_baseline_is_an_earlier_or_overlapping_set(client: TestClient) -> None:
    sets = {s["name"]: s for s in _json(client, "/api/sets")["items"]}
    for name, other in (("mini", "sample"), ("sample", "mini")):
        doc = _json(client, f"/api/sets/{name}/triage")
        if sets[other]["last_ts"] <= sets[name]["last_ts"]:
            assert doc["baseline"]["name"] == other and doc["baseline"]["resolved_by"] == "auto"
        else:
            assert doc["verdict"] == "no_baseline"
