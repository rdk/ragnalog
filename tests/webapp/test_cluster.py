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


# --- the capture page and the capture routes (documents: tests/contract)


def test_capture_page_and_set_report_links(client: TestClient, tmp_path: Path) -> None:
    job = _upload(client, export_zip(tmp_path / "export.zip"), cluster="jira-test")
    ref = f"capture:{JOB_ID}"
    page = client.get(f"/captures/{ref}")
    assert page.status_code == 200
    html = page.text
    assert f'data-report-url="/api/captures/{ref}/report"' in html
    assert '<nav class="crumbs"' in html and "jira-test · capture" in html
    first = job["capture"]["sets"][0]["id"]
    assert f'href="/sets/{first}"' in html  # node reports
    assert 'aria-label="Views of this capture"' in html and 'class="node-link"' in html
    for name in ("report.css", "cluster.css", "cluster.js"):
        assert f"/assets/report/{name}" in html
        assert client.get(f"/assets/report/{name}").status_code == 200
    # cluster:NAME and a member set lead to the same page
    assert client.get("/captures/cluster:jira-test").status_code == 200
    missing = client.get("/captures/cluster:nope", headers={"accept": "text/html"})
    assert missing.status_code == 404 and "no capture" in missing.text
    report = client.get(f"/sets/{first}").text
    assert 'id="shell-node"' in report and f'href="/captures/{ref}"' in report
    assert 'id="shell-node"' not in client.get("/sets/mini").text  # no capture: no node switch


def test_cluster_payload_urls_and_cache(client: TestClient, tmp_path: Path) -> None:
    _upload(client, export_zip(tmp_path / "export.zip"), cluster="jira-test")
    url = "/api/captures/cluster:jira-test/report?window=union"
    first = client.get(url)
    assert first.status_code == 200
    payload = first.json()
    assert payload["schema"] == "cluster-report.v1"
    assert payload["urls"] == {
        "entries": f"/api/captures/capture:{JOB_ID}/entries",
        "context": "/api/sets/{set}/entries/{id}/context?all_nodes=1&window=30",
        "set_report": "/sets/{set}",
        "examples": "/api/sets/{set}/groups/{sig}/examples?max_raw=0",
        "raw": "/api/sets/{set}/entries/{id}/raw",
    }
    assert client.get(url).content == first.content  # cached
    member = payload["capture"]["nodes"][0]["set"]["id"]
    client.post(f"/api/sets/{member}/label?node=NODE9")  # relabelling drops the cache
    again = client.get(url).json()
    assert again["nodes"] == ["NODE2", "NODE9"]
    # the page's merged log asks for since/until/min_level/limit/offset
    since, until = payload["capture"]["window"]["since"], payload["capture"]["window"]["until"]
    page = client.get(
        payload["urls"]["entries"],
        params={"since": since, "until": until, "min_level": "WARN", "limit": 200, "offset": 0},
    ).json()
    assert page["schema"] == "entries.v1" and page["total"] > 0
    assert {r["level_name"] for r in page["items"]} <= {"WARN", "ERROR"}


def test_capture_entries_filter_by_groups_and_nodes(client: TestClient, tmp_path: Path) -> None:
    """The page's merged log sends its group, node and search filters to the server."""
    _upload(client, export_zip(tmp_path / "export.zip"), cluster="jira-test")
    url = f"/api/captures/capture:{JOB_ID}/entries"
    payload = client.get(f"/api/captures/capture:{JOB_ID}/report?window=union").json()
    on_two = [g for g in payload["groups"] if len(g["per_node"]) == 2]
    a, b = (g["signature"] for g in on_two[:2])
    params = {"window": "union", "limit": 10_000, "columns": "id,node,signature"}

    def total(**more: str) -> dict:  # type: ignore[type-arg]
        res = client.get(url, params={**params, **more})
        assert res.status_code == 200, res.json()
        doc: dict = res.json()  # type: ignore[type-arg]
        return doc

    one, other = total(signature=a), total(signature=b)
    both = total(signature=f"{a},{b[:8].upper()}, {a}")  # prefixes, case, repeats, blanks
    assert both["total"] == one["total"] + other["total"] > 0
    assert {r["signature"] for r in both["items"]} == {a, b}
    node = payload["nodes"][-1]
    only = total(signature=f"{a},{b}", node=node)
    assert 0 < only["total"] < both["total"]
    assert {r["node"] for r in only["items"]} == {node}
    assert total(node=",".join(payload["nodes"]))["total"] == total()["total"]
    # the window is still that of all nodes: node N alone gives the same rows as its share
    assert total(node=node, signature=a)["total"] == sum(
        1 for r in one["items"] if r["node"] == node
    )
    # NODE:SIGNATURE: a co-burst's sides, each group on its own node only
    n1, n2 = payload["nodes"][0], payload["nodes"][-1]
    paired = total(signature=f"{n1}:{a[:8].upper()},{n2}:{b}")
    expect = {(n1, a), (n2, b)}
    assert {(r["node"], r["signature"]) for r in paired["items"]} == expect
    assert paired["total"] == sum(1 for r in both["items"] if (r["node"], r["signature"]) in expect)
    mixed = total(signature=f"{a},{n2}:{b}")  # a anywhere, b on n2 only
    assert mixed["total"] == one["total"] + sum(1 for r in other["items"] if r["node"] == n2)
    bad = client.get(url, params={"signature": f"NODE7:{a}"})
    assert bad.status_code == 400 and "NODE7" in bad.json()["error"]["message"]
    bad = client.get(url, params={"node": "NODE7"})
    assert bad.status_code == 400 and "NODE7" in bad.json()["error"]["message"]
    member = payload["capture"]["nodes"][0]["set"]["id"]
    on_set = client.get(f"/api/sets/{member}/entries", params={"node": node})
    assert on_set.status_code == 400 and on_set.json()["error"]["code"] == "usage"
    several = client.get(f"/api/sets/{member}/entries", params={"signature": f"{a},{b}"})
    assert several.status_code == 400 and "capture" in several.json()["error"]["message"]


def test_stale_spool_is_removed_at_start(store_dir: Path) -> None:
    import os
    import time

    from atlas_log_analyzer.webapp import Settings, create_app

    spool = store_dir / ".spool"
    old, fresh = spool / "old", spool / "fresh"
    for d in (old, fresh):
        (d / "x").mkdir(parents=True)
    hours_ago = time.time() - 7 * 3600
    os.utime(old, (hours_ago, hours_ago))
    with TestClient(create_app(Settings(store=store_dir, sync_jobs=True))):
        assert not old.exists() and fresh.exists()  # a running ingest keeps its spool


def test_cluster_examples_url_returns_full_entries(client: TestClient, tmp_path: Path) -> None:
    _upload(client, export_zip(tmp_path / "export.zip"), cluster="jira-test")
    payload = client.get(f"/api/captures/capture:{JOB_ID}/report").json()
    group = next(g for g in payload["groups"] if any(g["examples"].values()))
    node, ids = next((n, i) for n, i in group["examples"].items() if i)
    set_id = next(m["set"]["id"] for m in payload["capture"]["nodes"] if m["node"] == node)
    url = payload["urls"]["examples"].replace("{set}", set_id).replace("{sig}", group["signature"])
    items = client.get(url).json()["items"]
    assert set(ids) <= set(items)
    assert not any(e.get("raw_truncated") for e in items.values())


def test_a_bare_capture_id_names_the_capture(client: TestClient, tmp_path: Path) -> None:
    _upload(client, export_zip(tmp_path / "export.zip"), cluster="jira-test")
    assert client.get(f"/captures/{JOB_ID}").status_code == 200
    doc = client.get(f"/api/captures/{JOB_ID}").json()
    assert doc["capture"] == JOB_ID


def test_an_explicit_window_is_clamped_to_the_logs(client: TestClient, tmp_path: Path) -> None:
    _upload(client, export_zip(tmp_path / "export.zip"), cluster="jira-test")
    url = f"/api/captures/capture:{JOB_ID}"
    res = client.get(url, params={"since": "1d", "until": "9999-12-31"})
    assert res.status_code == 200 and not res.json()["window"]["until"].startswith("9999")
    report = client.get(f"{url}/report", params={"since": "1d", "until": "9999-12-31"})
    assert report.status_code == 200
    outside = client.get(url, params={"since": "2000-01-01", "until": "2000-01-02"})
    assert outside.status_code == 400 and "holds no entries" in outside.json()["error"]["message"]
    bucket = client.get(f"{url}/timeline", params={"bucket": "999999999999d"})
    assert bucket.status_code == 400


def test_warm_builds_the_newest_capture_page(
    client: TestClient, store_dir: Path, tmp_path: Path
) -> None:
    from atlas_log_analyzer.service.capture_queries import CaptureWindow
    from atlas_log_analyzer.webapp import Settings, create_app

    assert _upload(client, export_zip(tmp_path / "export.zip"))["state"] == "succeeded"
    app = create_app(Settings(store=store_dir), warm=True)
    state = app.state.atlas
    state.warm().join(60)
    assert len(state._clusters) == 1
    built = next(iter(state._clusters.values()))
    again = state.cluster_report_bytes(  # the page's default request is a cache hit
        f"capture:{JOB_ID}", CaptureWindow(), examples=3, max_raw=4000, max_groups=500, triage=True
    )
    assert again is built
    empty = create_app(Settings(store=tmp_path / "none"))
    empty.state.atlas.warm().join(60)  # no capture: nothing to build, no error
    assert not empty.state.atlas._clusters
