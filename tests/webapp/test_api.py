"""JSON API: documents mirror the CLI's ``--output json`` shapes."""

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import quote

import pytest
from fastapi.testclient import TestClient

from ragnalog.webapp import Settings, create_app

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
    from ragnalog.service.views import SET_BRIEF

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
    assert doc["error"]["reason"] == "set_not_found"
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
    doc = _json(client, "/api/sets/mini/groups?limit=nope", 400)
    assert doc["schema"] == "error.v1" and doc["error"]["code"] == "usage"
    # a number below its bound is invalid_value, as in the CLI; a page cap stays usage
    assert _json(client, "/api/sets/mini/groups?limit=-1", 400)["error"]["code"] == "invalid_value"
    assert _json(client, "/api/sets/mini/entries?limit=0", 400)["error"]["code"] == "usage"


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


def test_group_examples_of_a_time_window(client: TestClient) -> None:
    """With since/until the examples are the group's first entries in the window (report page)."""
    groups = _json(client, "/api/sets/sample/groups?limit=0")["items"]
    sig = next(g["signature"] for g in groups if g["count"] >= 3)
    rows = _json(client, f"/api/sets/sample/entries?signature={sig}&limit=100")["items"]
    times = [r["ts"] for r in rows]
    url = f"/api/sets/sample/groups/{sig}/examples?max_raw=0"
    doc = _json(client, f"{url}&since={quote(times[1])}&limit=50")
    assert doc["schema"] == "report-entries.v1"
    assert sorted(e["ts"] for e in doc["items"].values()) == times[1:]
    doc = _json(client, f"{url}&since={quote(times[0])}&until={quote(times[1])}")
    assert [e["ts"] for e in doc["items"].values()] == [times[0]]
    doc = _json(client, f"{url}&since={quote(times[0])}&limit=2")
    assert len(doc["items"]) == 2
    assert _json(client, f"{url}&since=nonsense", 400)["error"]["code"] == "invalid_value"


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
    assert all("levels" not in b for b in tl["items"])
    lv = _json(client, "/api/sets/mini/timeline?bucket=5m&by_level=true")
    assert sum(b["count"] for b in lv["items"]) == 10
    assert all(sum(b["levels"].values()) == b["count"] for b in lv["items"])
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
    base = doc["baseline"]["id"]
    assert all(r["report_url"].startswith(f"/sets/{base}#sig=") for r in doc["vanished"])
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
        assert c.get("/api/sets", headers={"X-Ragnalog-Token": "s3cret"}).status_code == 200


def test_triage_auto_baseline_ranks_like_the_cli(client: TestClient, store_dir: Path) -> None:
    """sample (45 entries over 4.8 days) spans 17x mini's 6.9 h; mini ends after sample."""
    mini = _json(client, "/api/sets/mini/triage")
    assert (
        mini["verdict"] == "no_baseline" and "sample (span: covers 4.8 days" in mini["warnings"][0]
    )
    sample = _json(client, "/api/sets/sample/triage")
    assert sample["verdict"] == "no_baseline" and "mini (later" in sample["warnings"][0]
    for name in ("mini", "sample"):
        cli = _cli_json(store_dir, "triage", name)
        assert cli["warnings"] == _json(client, f"/api/sets/{name}/triage")["warnings"]
    given = _json(client, "/api/sets/mini/triage?baseline=sample")
    assert given["baseline"]["resolved_by"] == "param" and given["baseline"]["entries"] == 45
    assert given["baseline"]["candidates"] == [] and given["baseline"]["reason"]


def _cli_json(store_dir: Path, *args: str) -> dict[str, Any]:
    """The CLI's JSON document for ``args`` over the same store."""
    import json
    import subprocess
    import sys

    command = [sys.executable, "-m", "ragnalog.cli.app", "--output", "json"]
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
    from ragnalog.store.pipeline import ingest_paths
    from ragnalog.store.store import Store
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
    from ragnalog.store.pipeline import ingest_paths
    from ragnalog.store.store import Store

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
    from ragnalog.service.views import DETAIL_BRIEF, GROUP_BRIEF

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


def test_signature_prefixes(store_dir: Path, tmp_path: Path) -> None:
    from ragnalog.store.pipeline import ingest_paths
    from ragnalog.store.store import Store
    from tests.cli.synth import write_log
    from tests.cli.test_diff_rate import wide

    ingest_paths(Store(store_dir), [write_log(tmp_path / "c" / "a.log", wide(1500))], name="many")
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))) as client:
        sig = _json(client, "/api/sets/mini/groups?exception=DataAccess")["items"][0]["signature"]
        group = _json(client, f"/api/sets/mini/groups/{sig[:6]}")
        assert group["group"]["signature"] == sig
        assert _json(client, f"/api/sets/mini/frames/{sig[:5]}")["signature"] == sig
        assert _json(client, f"/api/sets/mini/entries?signature={sig[:4]}")["total"] == 1
        pairs = _json(client, "/api/sets/many/groups?limit=0&columns=signature")["items"]
        heads = [s["signature"][:4] for s in pairs]
        prefix = next(h for h in sorted(heads) if heads.count(h) > 1)
        for url in (
            f"/api/sets/many/groups/{prefix}",
            f"/api/sets/many/frames/{prefix}",
            f"/api/sets/many/entries?signature={prefix}",
        ):
            err = _json(client, url, 409)["error"]
            assert err["code"] == "ambiguous" and 2 <= len(err["candidates"]) <= 5, url
            assert all(c.startswith(prefix) for c in err["candidates"])
    cli = _cli_json(store_dir, "show", "many", prefix)
    assert cli["error"]["candidates"] == err["candidates"]


def test_ingest_large_foreign_file_needs_force(
    empty_client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import ragnalog.store.pipeline as pipeline

    monkeypatch.setattr(pipeline, "MAX_FOREIGN_BYTES", 100)
    blob = {"files": ("dump.log", b"no line breaks here " * 20)}
    job = empty_client.post("/api/ingest", files=blob, data={"format": "jira"}).json()
    assert job["state"] == "failed", job
    assert job["error"]["code"] == "not_in_format" and "force" in job["error"]["hint"]
    forced = empty_client.post("/api/ingest?force=true&format=jira", files=blob).json()
    assert forced["state"] == "succeeded", forced
    assert forced["options"]["force"] is True
    assert forced["result"]["ingest"]["parse_errors"] == 1


def test_ingest_frames_option(empty_client: TestClient) -> None:
    upload = {"files": ("mini.log", MINI.read_bytes())}
    job = empty_client.post("/api/ingest", files=upload, data={"frames": "2"}).json()
    assert job["state"] == "succeeded", job
    assert job["options"]["frames"] == 2
    assert job["result"]["signature_spec"].endswith("F2")
    again = {"files": ("mini.log", MINI.read_bytes() + b"\n")}
    job = empty_client.post("/api/ingest?frames=1", files=again).json()
    assert job["result"]["signature_spec"].endswith("F1")
    bad = empty_client.post("/api/ingest", files=upload, data={"frames": "-1"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_value"
    bad = empty_client.post("/api/ingest?frames=-1", files=upload)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "invalid_value"
    assert not any((empty_client.app.state.ragnalog.store.root / ".uploads").glob("*/*"))


def test_ingest_job_reports_ignored_name(client: TestClient) -> None:
    job = client.post(
        "/api/ingest", files={"files": ("mini.log", MINI.read_bytes())}, data={"name": "again"}
    ).json()
    assert job["state"] == "succeeded" and job["name"] == "again"
    assert job["result"]["name"] == "mini" and job["result"]["ingest"]["existing"] is True
    assert job["result"]["ingest"]["warnings"] == ["--name again ignored: set exists as 'mini'"]


def test_entry_raw_is_the_exact_text_with_its_banner(client: TestClient, store_dir: Path) -> None:
    from ragnalog.store.store import Store

    ls = Store(store_dir).open("sample")
    rows = list(ls.entries_lazy().select("id", "raw", "context").collect().iter_rows(named=True))
    with_banner = next(r for r in rows if dict(r["context"] or []).get("preamble"))
    plain = next(r for r in rows if not dict(r["context"] or []).get("preamble"))
    preamble = dict(with_banner["context"])["preamble"]

    res = client.get(f"/api/sets/sample/entries/{with_banner['id']}/raw")
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/plain")
    assert res.headers["x-content-type-options"] == "nosniff"
    assert res.text == f"{preamble}\n{with_banner['raw']}"
    assert client.get(f"/api/sets/sample/entries/{plain['id']}/raw").text == plain["raw"]

    missing = client.get("/api/sets/sample/entries/0123456789abcdef/raw")
    assert missing.status_code == 404
    assert missing.json()["error"]["reason"] == "entry_not_found"

    # the cards' "Open raw" page: the same text, rendered by entry-card.js (highlighted trace)
    page = client.get(f"/sets/sample/entries/{with_banner['id']}/raw")
    assert page.status_code == 200 and page.headers["content-type"].startswith("text/html")
    assert 'id="raw-data"' in page.text and "/static/entry_raw.js" in page.text
    assert f"/api/sets/{ls.manifest.id}/entries/{with_banner['id']}/raw" in page.text
    gone = client.get("/sets/sample/entries/0123456789abcdef/raw", headers={"accept": "text/html"})
    assert gone.status_code == 404


def test_token_guards_the_pages_and_a_link_sets_a_cookie(store_dir: Path) -> None:
    html = {"Accept": "text/html"}
    with TestClient(create_app(Settings(store=store_dir, token="s3cret"))) as c:
        for path in ("/", "/sets/mini", "/sets/mini/triage"):
            r = c.get(path, headers=html)
            assert r.status_code == 401 and "token" in r.text, path
        assert c.get("/static/webapp.css").status_code == 200
        r = c.get("/sets/mini?token=s3cret", headers=html, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].endswith("/sets/mini")
        assert "ragnalog_token=s3cret" in r.headers["set-cookie"]
        assert "httponly" in r.headers["set-cookie"].lower()
        c.cookies.set("ragnalog_token", "s3cret")
        assert c.get("/", headers=html).status_code == 200
        assert c.get("/api/sets").status_code == 200


def test_upload_with_a_wrong_token_is_refused_before_the_body(store_dir: Path) -> None:
    settings = Settings(store=store_dir, token="s3cret", max_upload_mb=1)
    with TestClient(create_app(settings)) as c:
        r = c.post("/api/ingest", files={"files": ("a.log", b"x" * 10)})
        assert r.status_code == 401 and r.json()["error"]["code"] == "unauthorized"
        big = b"x" * (3 << 20)
        auth = {"Authorization": "Bearer s3cret"}
        r = c.post("/api/ingest", files={"files": ("a.log", big)}, headers=auth)
        assert r.status_code == 413 and r.json()["error"]["code"] == "payload_too_large"


def test_triage_page_errors_are_html(client: TestClient) -> None:
    html = {"Accept": "text/html"}
    r = client.get("/sets/nope-nope/triage", headers=html)
    assert r.status_code == 404 and r.headers["content-type"].startswith("text/html")
    r = client.get("/sets/mini/triage?min_level=BOGUS", headers=html)
    assert r.status_code == 400 and r.headers["content-type"].startswith("text/html")
    assert "Cannot triage" in r.text


def test_out_of_range_parameters_are_errors_not_500(client: TestClient) -> None:
    r = client.get("/api/sets/mini/entries?since=800000d")
    assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_value"
    for path in (
        "/api/sets/mini/entries?offset=9223372036854775808",
        "/api/sets/mini/report?max_raw=100000000000000000000",
    ):
        r = client.get(path)
        assert r.status_code == 400 and r.json()["error"]["code"] == "invalid_value", path


@pytest.mark.parametrize("name", ["nul.log", "AUX.log", "com1.log"])
def test_upload_of_a_windows_device_name(empty_client: TestClient, name: str) -> None:
    res = empty_client.post("/api/ingest", files=[("files", (name, MINI.read_bytes()))])
    job = res.json()
    assert job["state"] == "succeeded", job
    assert job["files"] == ["_" + name] and job["result"]["entry_count"] == 10


def test_cross_site_writes_are_refused(client: TestClient) -> None:
    """A page of another site can post a form to the server; such requests change nothing."""
    evil = {"Origin": "https://evil.example"}
    res = client.delete("/api/sets/mini", headers=evil)
    assert res.status_code == 403 and res.json()["error"]["code"] == "http"
    assert (
        client.delete("/api/sets/mini", headers={"Sec-Fetch-Site": "cross-site"}).status_code == 403
    )
    assert client.post("/api/jobs/x/cancel", headers={"Origin": "null"}).status_code == 403
    assert client.get("/api/sets/mini", headers=evil).status_code == 200  # reads are not writes
    same = {"Origin": "http://testserver", "Sec-Fetch-Site": "same-origin"}
    assert client.post("/api/jobs/x/cancel", headers=same).status_code == 404  # reached the route
    assert client.get("/api/sets/mini").status_code == 200


def test_loopback_server_refuses_other_host_names(store_dir: Path) -> None:
    from ragnalog.webapp.app import LOOPBACK_HOSTS, _host_name

    assert [_host_name(h) for h in ("[::1]:8321", "LocalHost:80", "127.0.0.1", "")] == [
        "::1",
        "localhost",
        "127.0.0.1",
        "",
    ]
    app = create_app(Settings(store=store_dir), hosts=LOOPBACK_HOSTS)
    with TestClient(app, base_url="http://127.0.0.1:8321") as c:
        assert c.get("/api/sets").status_code == 200
        rebound = c.get("/api/sets", headers={"Host": "attacker.example:8321"})
        assert rebound.status_code == 400 and rebound.json()["error"]["code"] == "http"


def test_upload_without_content_length_is_refused(empty_client: TestClient) -> None:
    def chunks() -> Any:
        yield b'--x\r\nContent-Disposition: form-data; name="files"; filename="a.log"\r\n\r\n'
        yield b"x" * 1024 + b"\r\n--x--\r\n"

    res = empty_client.post(
        "/api/ingest",
        content=chunks(),
        headers={"Content-Type": "multipart/form-data; boundary=x"},
    )
    assert res.status_code == 411 and res.json()["error"]["code"] == "http"


def test_guards_behind_a_reverse_proxy(store_dir: Path) -> None:
    """A proxy rewrites Host or forwards it: same-origin writes and reads still work."""
    from ragnalog.webapp.app import LOOPBACK_HOSTS

    app = create_app(Settings(store=store_dir), hosts=LOOPBACK_HOSTS | {"logs.example.org"})
    with TestClient(app, base_url="http://127.0.0.1:8321") as c:
        public = {"Host": "logs.example.org", "X-Forwarded-For": "10.0.0.5"}
        assert c.get("/api/sets", headers=public).status_code == 200  # an allowed name
        # a rebinding page is same-origin: it may send any header, none lifts the check
        rebound = {"Host": "rebind.example:8321", "X-Forwarded-For": "x", "Forwarded": "for=x"}
        assert c.get("/api/sets", headers=rebound).status_code == 400
        # the proxy sets Host to the upstream; the browser says same-origin
        same = {"Origin": "https://logs.example.org", "Sec-Fetch-Site": "same-origin"}
        assert c.post("/api/jobs/x/cancel", headers=same).status_code == 404
        # no Sec-Fetch-Site: Origin against X-Forwarded-Host, default ports aside
        fwd = {
            "Origin": "https://logs.example.org",
            "X-Forwarded-Host": "logs.example.org:443",
            "X-Forwarded-Proto": "https",
        }
        assert c.post("/api/jobs/x/cancel", headers=fwd).status_code == 404
        other = {**fwd, "Origin": "https://evil.example"}
        assert c.post("/api/jobs/x/cancel", headers=other).status_code == 403
        assert (
            c.post("/api/jobs/x/cancel", headers={"Sec-Fetch-Site": "same-site"}).status_code == 403
        )
