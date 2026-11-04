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


def test_sets_brief_and_set_as_cli(client: TestClient, store_dir: Path) -> None:
    from atlas_log_analyzer.store.views import SET_BRIEF

    full = _json(client, "/api/sets")
    brief = _json(client, "/api/sets?brief=true")
    assert all(tuple(item) == SET_BRIEF for item in brief["items"])
    assert brief["items"] == [{k: i[k] for k in SET_BRIEF} for i in full["items"]]
    cli = _cli_json(store_dir, "sets", "--brief")
    assert cli["items"] == brief["items"] and cli["count"] == brief["count"]
    assert _cli_json(store_dir, "sets")["items"] == full["items"]
    assert _cli_json(store_dir, "set", "mini") == _json(client, "/api/sets/mini")


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


def _cli_json(store_dir: Path, *args: str) -> dict[str, Any]:
    """The CLI's JSON document for ``args`` over the same store."""
    import json
    import subprocess
    import sys

    command = [sys.executable, "-m", "atlas_log_analyzer.cli.app", "--output", "json"]
    res = subprocess.run(
        [*command, "--store", str(store_dir), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    doc: dict[str, Any] = json.loads(res.stdout)
    return doc


def test_groups_exception_filters_and_columns(client: TestClient, store_dir: Path) -> None:
    url = "/api/sets/mini/groups?with_exception=true&columns=signature,exception_type&limit=0"
    doc = _json(client, url)
    assert doc["count"] >= 1 and all(
        set(g) == {"signature", "exception_type"} for g in doc["items"]
    )
    assert all(g["exception_type"] for g in doc["items"])
    cli = _cli_json(store_dir, "groups", "mini", "--with-exception", "--limit", "0")
    assert sorted(g["signature"] for g in doc["items"]) == sorted(
        g["signature"] for g in cli["items"]
    )
    npe = _json(client, "/api/sets/mini/groups?exception=nullpointer&brief=true")
    assert npe["total"] == 1 and "top_users" not in npe["items"][0]
    assert _json(client, "/api/sets/mini/groups?contains=SLA")["total"] == 1
    bad = _json(client, "/api/sets/mini/groups?columns=signature,nope", 400)["error"]
    assert bad["code"] == "invalid_value" and "top_users" in bad["hint"]
    both = _json(client, "/api/sets/mini/groups?brief=true&columns=signature", 400)
    assert both["error"]["code"] == "usage"


def test_entries_request_id_exception_and_max_raw(client: TestClient) -> None:
    rid = _json(client, "/api/sets/mini/entries?request_id=543x1290x2")
    assert rid["total"] == 1 and rid["items"][0]["request_id"] == "543x1290x2"
    exc = _json(client, "/api/sets/mini/entries?exception=ConnectException&brief=true")
    assert exc["total"] == 0  # the outermost exception is DataAccessException
    exc = _json(client, "/api/sets/mini/entries?exception=DataAccess&brief=true")
    assert exc["total"] == 1 and list(exc["items"][0]) == [
        "id",
        "ts",
        "level_name",
        "thread",
        "user",
        "request_id",
        "logger",
        "message",
        "exception_type",
        "signature",
    ]
    cut = _json(client, "/api/sets/mini/entries?with_exception=true&max_raw=50&columns=raw")
    row = cut["items"][0]
    assert row["body_truncated"] is True and row["raw_chars"] > 50
    assert row["raw"].endswith("chars total, use max_raw=0]")
    full = _json(client, "/api/sets/mini/entries?with_exception=true&max_raw=0&columns=raw")
    assert "body_truncated" not in full["items"][0]


def test_context_endpoint(client: TestClient, store_dir: Path) -> None:
    ids = [e["id"] for e in _json(client, "/api/sets/mini/entries?columns=id")["items"]]
    anchor = ids[1]
    doc = _json(client, f"/api/sets/mini/entries/{anchor}/context?before=1&after=1&any_thread=true")
    assert doc["schema"] == "entries.v1" and doc["anchor"] == anchor and doc["any_thread"] is True
    assert [e["id"] for e in doc["items"]] == ids[0:3]
    cli = _cli_json(store_dir, "context", "mini", anchor, "--before", "1", "--after", "1",
                    "--any-thread")  # fmt: skip
    assert doc["items"] == cli["items"]
    missing = _json(client, "/api/sets/mini/entries/0000000000000000/context", 404)
    assert missing["error"]["code"] == "not_found"


def test_frames_endpoint(client: TestClient, store_dir: Path) -> None:
    sig = _json(client, "/api/sets/mini/groups?exception=DataAccess")["items"][0]["signature"]
    doc = _json(client, f"/api/sets/mini/frames/{sig}")
    assert doc["schema"] == "frames.v1" and doc["count"] == 2
    assert doc == _cli_json(store_dir, "frames", "mini", sig)
    every = _json(client, f"/api/sets/mini/frames/{sig}?all=true")
    assert all("app" in f for f in every["items"][0]["frames"])
    assert _json(client, "/api/sets/mini/frames/nope", 404)["error"]["code"] == "not_found"


def test_diff_rate_and_warning(client: TestClient, store_dir: Path) -> None:
    doc = _json(client, "/api/diff?a=sample&b=mini&rate=true")
    cli = _cli_json(store_dir, "diff", "sample", "mini", "--rate")
    assert doc["changed"] == cli["changed"] and doc.get("warnings") == cli.get("warnings")
    assert all({"rate_a", "rate_b", "rate_ratio"} <= set(r) for r in doc["changed"])


def test_triage_coverage_and_new_exceptions(client: TestClient) -> None:
    doc = _json(client, "/api/sets/mini/triage?baseline=sample")
    assert set(doc["coverage"]) == {"hours_baseline", "hours_set", "ratio"}
    assert isinstance(doc["warnings"], list) and isinstance(doc["new_exceptions"], list)
    assert doc["summary"]["new_exceptions"] == len(doc["new_exceptions"])
    assert doc["report_url"] == f"/sets/{doc['set']['id']}"
    assert all(r["report_url"].startswith("/sets/") for r in doc["new_exceptions"])
    assert all(r["exception_type"] for r in doc["new_exceptions"])
    none = _json(client, "/api/sets/mini/triage?baseline=none")
    assert none["verdict"] == "no_baseline" and none["coverage"]["hours_baseline"] is None


def test_diff_limit_defaults_to_50(store_dir: Path, tmp_path: Path) -> None:
    from atlas_log_analyzer.store.pipeline import ingest_paths
    from atlas_log_analyzer.store.store import Store
    from tests.cli.synth import write_log
    from tests.cli.test_diff_rate import wide

    ingest_paths(Store(store_dir), [write_log(tmp_path / "w" / "a.log", wide(60))], name="wide")
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))) as client:
        doc = _json(client, "/api/diff?a=mini&b=wide")
        assert len(doc["new"]) == 50 and doc["totals"]["new"] == 60 and doc["truncated"]
        assert len(_json(client, "/api/diff?a=mini&b=wide&limit=0")["new"]) == 60
    assert doc == _cli_json(store_dir, "diff", "mini", "wide")


def test_levels_as_cli(tmp_path: Path) -> None:
    log = tmp_path / "pe" / "atlassian-jira.log"
    log.parent.mkdir()
    log.write_text("preamble\n" + MINI.read_text(encoding="utf-8"), encoding="utf-8")
    from atlas_log_analyzer.store.pipeline import ingest_paths
    from atlas_log_analyzer.store.store import Store

    ingest_paths(Store(tmp_path / "s"), [log], name="pe")
    with TestClient(create_app(Settings(store=tmp_path / "s", sync_jobs=True))) as client:
        unknown = _json(client, "/api/sets/pe/entries?level=UNKNOWN&columns=level")
        assert unknown["items"] == [{"level": 0}]
        blank = _json(client, "/api/sets/pe/entries?level=&limit=1")
        assert blank["total"] == 11
        bad = _json(client, "/api/sets/pe/groups?level=SEVERE", 400)["error"]
    assert bad["code"] == "invalid_level"
    assert bad["message"] == (
        "level: unknown level 'SEVERE'; expected one of TRACE, DEBUG, INFO, WARN, ERROR, "
        "FATAL, UNKNOWN (WARNING = WARN)"
    )


def test_group_and_context_columns_and_brief(client: TestClient, store_dir: Path) -> None:
    from atlas_log_analyzer.store.views import DETAIL_BRIEF, GROUP_BRIEF

    sig = _first_signature(client)
    doc = _json(client, f"/api/sets/mini/groups/{sig}?brief=true")
    assert doc["items"] and all(tuple(r) == DETAIL_BRIEF for r in doc["items"])
    assert tuple(doc["group"]) == GROUP_BRIEF
    cli = _cli_json(store_dir, "show", "mini", sig, "--brief")
    assert cli["items"] == doc["items"] and cli["group"] == doc["group"]
    picked = _json(client, f"/api/sets/mini/groups/{sig}?columns=ts,message")
    assert all(list(r) == ["ts", "message"] for r in picked["items"])
    assert len(picked["group"]) > len(GROUP_BRIEF)
    entry = doc["items"][0]["id"]
    ctx = _json(client, f"/api/sets/mini/entries/{entry}/context?brief=true&any_thread=true")
    assert all(tuple(r) == DETAIL_BRIEF for r in ctx["items"])
    assert ctx == _cli_json(store_dir, "context", "mini", entry, "--brief", "--any-thread")
    bad = _json(client, f"/api/sets/mini/entries/{entry}/context?columns=nope", 400)
    assert bad["error"]["code"] == "invalid_value"
    both = _json(client, f"/api/sets/mini/groups/{sig}?brief=true&columns=ts", 400)
    assert both["error"]["code"] == "usage"
