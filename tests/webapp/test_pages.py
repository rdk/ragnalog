"""HTML pages and the ``serve`` command wiring."""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

from fastapi.testclient import TestClient

from atlas_log_analyzer.report import asset_text
from tests.cluster import JOB_ID, export_zip

from .test_cluster import _upload


def test_index_lists_sets(client: TestClient) -> None:
    res = client.get("/")
    assert res.status_code == 200 and "text/html" in res.headers["content-type"]
    assert "mini" in res.text and "sample" in res.text
    assert 'data-triage="' in res.text and 'id="upload-form"' in res.text


def test_index_empty_state(empty_client: TestClient) -> None:
    res = empty_client.get("/")
    assert res.status_code == 200
    assert "No log sets yet" in res.text


def test_report_page_fetches_payload(client: TestClient) -> None:
    sid = client.get("/api/sets/mini").json()["id"]
    res = client.get("/sets/mini?compare=auto")
    assert res.status_code == 200
    page = res.text
    assert f'data-report-url="/api/sets/{sid}/report?compare=auto&amp;examples=3"' in page
    assert '<script src="/assets/report/report.js"></script>' in page
    assert 'class="shell"' in page and 'id="shell-set"' in page


def test_report_assets_served(client: TestClient) -> None:
    res = client.get("/assets/report/report.js")
    assert res.status_code == 200 and res.text == asset_text("report.js")
    assert client.get("/assets/report/template.html").status_code == 404
    assert client.get("/static/webapp.css").status_code == 200


def test_triage_page(client: TestClient) -> None:
    res = client.get("/sets/mini/triage?baseline=sample")
    assert res.status_code == 200
    assert "Triage" in res.text and "sample" in res.text
    assert "New types" in res.text and "New exceptions" in res.text
    assert "Baseline: given by the caller" in res.text


def test_triage_page_explains_no_baseline(client: TestClient) -> None:
    """The auto rule (store.triage.rank_baselines) rejects sample: 4.8 days vs 6.9 h."""
    res = client.get("/sets/mini/triage")
    assert res.status_code == 200 and "No baseline" in res.text
    assert "sample (span: covers 4.8 days" in res.text


def test_html_404(client: TestClient) -> None:
    res = client.get("/sets/nope", headers={"Accept": "text/html"})
    assert res.status_code == 404 and "Not found" in res.text
    res = client.get("/no/such/page", headers={"Accept": "text/html"})
    assert res.status_code == 404 and "Back to the log sets" in res.text


def test_api_404_is_json(client: TestClient) -> None:
    res = client.get("/api/no/such/route")
    assert res.status_code == 404 and res.json()["schema"] == "error.v1"


def test_openapi_lists_routes(client: TestClient) -> None:
    paths = client.get("/openapi.json").json()["paths"]
    for path in ("/api/sets", "/api/sets/{ref}/groups", "/api/sets/{ref}/triage", "/api/ingest"):
        assert path in paths


def test_cli_does_not_import_fastapi() -> None:
    code = "import sys, atlas_log_analyzer.cli.app; assert 'fastapi' not in sys.modules"
    subprocess.run([sys.executable, "-c", code], check=True)


def test_sets_page_groups_a_capture(client: TestClient, tmp_path: Path) -> None:
    _upload(client, export_zip(tmp_path / "export.zip"), cluster="jira-test")
    page = client.get("/").text
    assert f'<tr class="capture-row" data-capture="{JOB_ID}">' in page
    assert f'href="/captures/capture:{JOB_ID}"' in page
    assert "2 nodes" in page and ">jira-test</a>" in page
    assert page.count(f'data-member-of="{JOB_ID}"') == 2
    assert '<span class="node-chip" title="Cluster node">NODE1</span>' in page
    assert page.index("capture-row") < page.index("jira-test_node1") < page.index("jira-test_node2")
    # sets without a capture are listed as before
    assert 'class="set-name" href="/sets/' in page and ">mini</a>" in page
    assert page.count("data-triage=") == 4 and f'data-capture-verdict="{JOB_ID}"' in page


def test_index_rows() -> None:
    from atlas_log_analyzer.webapp.pages import index_rows

    def item(i: str, capture: str | None, node: str | None, last: str) -> dict:  # type: ignore[type-arg]
        return {
            "id": i,
            "name": i,
            "node": node,
            "cluster": "c" if capture else None,
            "capture": capture,
            "first_ts": "2026-11-05T06:00:00+00:00",
            "last_ts": last,
            "entry_count": 5,
            "summary": {"errors": 1, "warnings": 2, "error_groups": 1},
            "bytes": 10,
        }

    items = [
        item("s3", None, None, "2026-11-07T00:00:00+00:00"),
        item("b", "cap", "NODE2", "2026-11-06T12:00:00+01:00"),
        item("plain", None, None, "2026-11-06T00:00:00+00:00"),
        item("a", "cap", "NODE1", "2026-11-06T10:00:00+00:00"),
    ]
    rows = index_rows(items)
    assert [r.get("set", {}).get("id") or r["capture"]["id"] for r in rows] == [
        "s3",
        "cap",
        "plain",
    ]
    capture = rows[1]["capture"]
    assert [m["id"] for m in rows[1]["members"]] == ["a", "b"]
    assert capture["last_ts"] == "2026-11-06T12:00:00+01:00"  # by instant, not by text
    assert (capture["nodes"], capture["entry_count"], capture["errors"]) == (2, 10, 2)


def test_index_draws_activity_strips_on_one_axis(client: TestClient) -> None:
    page = client.get("/").text
    assert page.count('<svg class="strip"') == 2  # mini and sample
    assert 'data-axis-lo="' in page and 'data-axis-bucket="1"' in page
    assert re.search(r'data-bins="\d+,\d+,\d+', page)
    assert client.get("/favicon.ico").headers["content-type"].startswith("image/svg+xml")


def test_activity_strips_share_the_axis_and_merge_buckets() -> None:
    from atlas_log_analyzer.webapp.pages import activity_strips

    base = 490_000
    strips, axis = activity_strips(
        {"a": [(base, 10, 0), (base + 2, 40, 4)], "b": [(base + 1000, 5, 5)], "c": []}
    )
    assert set(strips) == {"a", "b"}  # a key without hours draws nothing
    assert axis is not None and axis["lo"] == base * 3_600_000 and axis["bucket"] == 3
    assert axis["hi"] == (base + 1002) * 3_600_000  # 334 buckets of 3 hours
    assert 'data-bins="0,50,4"' in strips["a"]  # hours 0 and 2 share the first bucket
    assert 'x="333"' in strips["b"] and 'data-bins="333,5,5"' in strips["b"]
    assert activity_strips({"a": []}) == ({}, None)


def test_set_activity_counts_entries_and_errors_per_hour(client: TestClient) -> None:
    from atlas_log_analyzer.service.views import set_activity

    state = client.app.state.atlas  # type: ignore[attr-defined]
    ls = state.open(client.get("/api/sets/mini").json()["id"])
    hours = set_activity(ls)
    assert sum(n for _, n, _ in hours) == ls.manifest.entry_count
    assert [h for h, _, _ in hours] == sorted(h for h, _, _ in hours)
    assert sum(e for _, _, e in hours) == state.summary(ls)["errors"]


def test_page_errors_are_html_with_their_status(client: TestClient) -> None:
    html = {"accept": "text/html"}
    res = client.get("/sets/mini?examples=x", headers=html)
    assert res.status_code == 400 and "text/html" in res.headers["content-type"]
    assert "examples" in res.text
    api = client.get("/api/sets/mini/report?examples=x")
    assert api.json()["schema"] == "error.v1"  # the API keeps error.v1


def test_pages_answer_head_and_have_titles(client: TestClient) -> None:
    assert client.head("/").status_code == 200
    assert client.head("/sets/mini").status_code == 200
    assert "<title>mini · atlas-log report</title>" in client.get("/sets/mini").text


def test_report_payload_is_gzipped_with_an_etag(client: TestClient) -> None:
    url = "/api/sets/mini/report"
    res = client.get(url, headers={"accept-encoding": "gzip"})
    assert res.status_code == 200 and res.headers["content-encoding"] == "gzip"
    payload = res.json()  # httpx decodes it
    assert payload["entries"] == {} and any(g["examples"] for g in payload["groups"])
    tag = res.headers["etag"]
    again = client.get(url, headers={"if-none-match": tag})
    assert again.status_code == 304 and not again.content


def test_group_examples_accept_a_signature_prefix(client: TestClient) -> None:
    sig = client.get("/api/sets/mini/groups?limit=1").json()["items"][0]["signature"]
    full = client.get(f"/api/sets/mini/groups/{sig}/examples").json()
    short = client.get(f"/api/sets/mini/groups/{sig[:6]}/examples")
    assert short.status_code == 200 and short.json()["items"] == full["items"]


def test_time_ranges_carry_their_utc_ends(client: TestClient) -> None:
    """Pages print UTC; the ISO ends let webapp.js show the viewer's zone (shared preference)."""
    from atlas_log_analyzer.webapp.pages import _fmt_range

    page = client.get("/").text
    assert 'id="shell-tz"' in page and "data-tz-name" in page
    found = re.findall(
        r'<span class="tz-range" data-a="([^"]+)" data-b="([^"]+)">([^<]+)</span>', page
    )
    assert found and all(a <= b and "→" in text for a, b, text in found)
    assert 'class="tz-range"' in client.get("/sets/mini/triage").text
    assert str(_fmt_range(None, "2026-10-29T10:00:00+00:00")) == "- → 2026-10-29 10:00"
    assert ">2026-10-29 10:00 → 11:30<" in str(
        _fmt_range("2026-10-29T10:00:00+00:00", "2026-10-29T11:30:00+00:00")
    )
