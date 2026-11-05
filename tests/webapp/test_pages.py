"""HTML pages and the ``serve`` command wiring."""

from __future__ import annotations

import subprocess
import sys

from fastapi.testclient import TestClient

from atlas_log_analyzer.report import asset_text


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
