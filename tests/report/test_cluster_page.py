"""The cluster page: ``render_cluster_html`` (static and web), the sample payload, valid JS."""

from __future__ import annotations

import copy
import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from atlas_log_analyzer.report import (
    CLUSTER_SCHEMA,
    render_cluster_html,
    write_cluster_report,
)
from atlas_log_analyzer.report.render import asset_text

from .cluster_data import load_payload, with_nodes

EVIL = 'x</script><script>alert(1)</script> <!-- y --> </SCRIPT> "q" & <b>'
ROOT_IDS = (
    "c-title",
    "c-ov",
    "c-scope",
    "ids",
    "c-cards",
    "c-timeline",
    "tl-y",
    "btn-tz",
    "btn-theme",
    "c-tabs",
    "p-groups",
    "groups",
    "drawer",
    "p-inter",
    "p-log",
    "ml",
    "tooltip",
    "fatal",
    "cluster-data",
)
TOP_KEYS = {
    "schema",
    "generated_at",
    "tool_version",
    "capture",
    "nodes",
    "timeline",
    "groups",
    "entries",
    "events",
    "interactions",
    "options",
    "urls",
}


def cluster_data(page: str) -> Any:
    """The embedded payload of a rendered cluster page."""
    start = page.index('<script type="application/json" id="cluster-data">')
    start = page.index(">", start) + 1
    end = page.index("</script>", start)
    return json.loads(page[start:end]) if page[start:end] else None


@pytest.fixture(scope="module")
def payload() -> dict[str, Any]:
    return load_payload()


@pytest.fixture(scope="module")
def page(payload: dict[str, Any]) -> str:
    return render_cluster_html(payload)


# ----------------------------------------------------------------------------- sample payload


def test_sample_payload_has_the_contract_shape(payload: dict[str, Any]) -> None:
    assert set(payload) == TOP_KEYS
    assert payload["schema"] == CLUSTER_SCHEMA
    assert payload["nodes"] == ["NODE1", "NODE2"]
    cap = payload["capture"]
    assert {"capture", "cluster", "window", "nodes", "skew"} <= set(cap)
    assert set(cap["window"]) == {"mode", "since", "until"}
    for node in cap["nodes"]:
        assert {
            "node", "set", "first_ts", "last_ts", "coverage", "gaps", "entries", "errors",
            "warnings", "errors_per_hour", "warnings_per_hour", "top_error", "verdict",
            "baseline", "events",
        } <= set(node)  # fmt: skip
    tl = payload["timeline"]
    assert tl["bucket"] == "1h" and set(tl["nodes"]) == {"NODE1", "NODE2"}
    nb = len(tl["buckets"])
    for t in tl["nodes"].values():
        assert all(len(v) == nb for v in t["levels"].values())
    assert set(payload["events"]) == {"items", "pairs"}
    assert set(payload["interactions"]) == {"mentions", "co_bursts", "concurrent_jobs", "sessions"}
    assert payload["urls"] is None


def test_sample_payload_is_consistent(payload: dict[str, Any]) -> None:
    nb = len(payload["timeline"]["buckets"])
    for g in payload["groups"]:
        assert g["total"] == sum(p["count"] for p in g["per_node"].values())
        for node, hourly in g["hourly"].items():
            assert sum(n for _, n in hourly) == g["per_node"][node]["count"]
            assert all(0 <= i < nb for i, _ in hourly)
        for ids in g["examples"].values():
            assert all(i in payload["entries"] for i in ids)
    for e in payload["entries"].values():
        assert e["node"] in payload["nodes"] and e["set"]


def test_sample_payload_covers_the_page_features(payload: dict[str, Any]) -> None:
    n1, n2 = payload["capture"]["nodes"]
    assert n2["coverage"]["since"] < n1["coverage"]["since"]  # hatching before NODE1's logs
    assert n1["gaps"] and not n2["gaps"]
    kinds = {e["kind"] for e in payload["events"]["items"]}
    assert {"cache_replication", "index_snapshot", "index_replay", "job_run"} <= kinds
    assert any(
        e["kind"] == "cache_replication" and e["subject_node"] == "NODE1"
        for e in payload["events"]["items"]
    )
    ix = payload["interactions"]
    assert ix["mentions"] and ix["co_bursts"] and ix["concurrent_jobs"] and ix["sessions"]
    assert {g["spread"] for g in payload["groups"]} == {"all", "only", "skewed"}
    assert any("\n\tat org.acme." in e["raw"] for e in payload["entries"].values())


def test_sample_payload_content_is_invented() -> None:
    text = (Path(__file__).parent / "data" / "cluster_payload.json").read_text(encoding="utf-8")
    assert "http://" not in text and "https://" not in text
    assert not re.search(r"\b(?:\d{1,3}\.){3}\d{1,3}\b", text)  # no IP addresses
    loggers = {g["logger"] for g in json.loads(text)["groups"]}
    assert all(lg.startswith("org.acme.") for lg in loggers)


# ----------------------------------------------------------------------------- static page


def test_static_page_inlines_assets_and_payload(page: str, payload: dict[str, Any]) -> None:
    assert page.startswith("<!doctype html>")
    for marker in (
        "__REPORT_TITLE__",
        "/*__REPORT_CSS__*/",
        "/*__CLUSTER_CSS__*/",
        "/*__CLUSTER_JS__*/",
    ):
        assert marker not in page
    assert "--lv-error: #d03b3b" in page  # report.css
    assert "--n1: #2a78d6" in page  # cluster.css
    assert page.index("--lv-error") < page.index("--n1:")  # report.css first
    assert '"use strict"' in page and '$("cluster-data")' in page  # cluster.js
    assert "<title>acme-jira · atlas-log cluster</title>" in page
    for root in ROOT_IDS:
        assert f'id="{root}"' in page, root
    assert "data-report-url" not in page[: page.index("<head>")]
    assert cluster_data(page) == payload


def test_static_page_has_no_external_references(page: str) -> None:
    assert not re.search(r"""\b(?:src|href|action)\s*=\s*["']?\s*(?:https?:)?//""", page, re.I)
    assert not re.search(r"<link\b", page, re.I)
    assert not re.search(r"<script\b[^>]*\bsrc=", page, re.I)
    assert not re.search(r"@import|url\(\s*['\"]?(?:https?:)?//", page, re.I)


def test_script_breakers_are_escaped(payload: dict[str, Any]) -> None:
    p = copy.deepcopy(payload)
    entry_id = next(iter(p["entries"]))
    p["entries"][entry_id]["raw"] = EVIL
    p["groups"][0]["template"] = EVIL
    p["events"]["items"][0]["text"] = EVIL
    p["capture"]["cluster"] = "<b>c</b>"
    out = render_cluster_html(p)
    assert "alert(1)</script>" not in out
    assert "<!-- y" not in out
    assert "</SCRIPT>" not in out
    assert "<title>&lt;b&gt;c&lt;/b&gt; · atlas-log cluster</title>" in out
    data = cluster_data(out)
    assert data["entries"][entry_id]["raw"] == EVIL
    assert data["groups"][0]["template"] == EVIL
    start = out.index('id="cluster-data">')
    outside = out[:start] + out[out.index("</script>", start) :]
    assert len(re.findall(r"<script\b", outside, re.I)) == 3  # theme, data, cluster.js


def test_placeholder_text_in_payload_is_not_substituted(payload: dict[str, Any]) -> None:
    p = copy.deepcopy(payload)
    p["groups"][0]["template"] = "/*__CLUSTER_JS__*/ /*__REPORT_CSS__*/ __REPORT_TITLE__"
    data = cluster_data(render_cluster_html(p))
    assert data["groups"][0]["template"] == p["groups"][0]["template"]


@pytest.mark.parametrize("nodes", [1, 5, 10])
def test_renders_other_node_counts(nodes: int) -> None:
    if nodes == 1:
        p = load_payload()
        p["nodes"] = ["NODE1"]
        p["capture"]["nodes"] = p["capture"]["nodes"][:1]
    else:
        p = with_nodes(nodes)
    assert len(p["nodes"]) == nodes
    assert cluster_data(render_cluster_html(p)) == p


def test_renders_a_minimal_payload() -> None:
    p = {
        "schema": CLUSTER_SCHEMA,
        "generated_at": "2026-11-05T10:00:00Z",
        "tool_version": "0.1.0",
        "capture": {"capture": None, "cluster": None, "window": None, "nodes": [], "skew": []},
        "nodes": [],
        "timeline": {"bucket": "1h", "buckets": [], "nodes": {}},
        "groups": [],
        "entries": {},
        "events": {"items": [], "pairs": []},
        "interactions": {"mentions": [], "co_bursts": [], "concurrent_jobs": [], "sessions": []},
        "options": {},
        "urls": None,
    }
    out = render_cluster_html(p)
    assert "<title>cluster · atlas-log cluster</title>" in out
    assert cluster_data(out) == p


def _inline_script(page: str) -> str:
    scripts = re.findall(r"<script>(.*?)</script>", page, re.S)
    assert len(scripts) == 2  # theme bootstrap + cluster.js
    return scripts[-1]


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not on PATH")
def test_inline_script_parses_with_node(page: str, tmp_path: Path) -> None:
    js = tmp_path / "cluster.js"
    js.write_text(_inline_script(page), encoding="utf-8")
    proc = subprocess.run(
        ["node", "--check", str(js)], capture_output=True, text=True, timeout=60, check=False
    )
    assert proc.returncode == 0, proc.stderr


# ----------------------------------------------------------------------------- web page


def test_web_shell_links_assets_and_sets_the_data_url() -> None:
    out = render_cluster_html(
        data_url='/api/captures/a"b/report?x=1&y=2', asset_base="/assets/report/"
    )
    assert '<html data-report-url="/api/captures/a&quot;b/report?x=1&amp;y=2"' in out
    assert '<script type="application/json" id="cluster-data"></script>' in out
    assert '<link rel="stylesheet" href="/assets/report/report.css">' in out
    assert '<link rel="stylesheet" href="/assets/report/cluster.css">' in out
    assert '<script src="/assets/report/cluster.js"></script>' in out
    assert out.index("report.css") < out.index("cluster.css")
    assert "--lv-error" not in out and "--n1:" not in out and '"use strict"' not in out
    for marker in (
        "__REPORT_TITLE__",
        "/*__REPORT_CSS__*/",
        "/*__CLUSTER_CSS__*/",
        "/*__CLUSTER_JS__*/",
        "__REPORT_DATA__",
    ):
        assert marker not in out
    assert cluster_data(out) is None


def test_web_page_with_payload(payload: dict[str, Any]) -> None:
    p = copy.deepcopy(payload)
    p["urls"] = {
        "entries": "/api/captures/capture:c0ffee12/entries",
        "context": "/api/sets/{set}/entries/{id}/context?all_nodes=1&window=30s",
        "set_report": "/sets/{set}",
    }
    out = render_cluster_html(p, asset_base="/static/")
    assert '<script src="/static/cluster.js"></script>' in out
    assert "data-report-url" not in out[: out.index("<head>")]
    assert cluster_data(out) == p


def test_render_needs_payload_or_url() -> None:
    with pytest.raises(ValueError):
        render_cluster_html()


def test_cluster_assets() -> None:
    assert "__REPORT_DATA__" in asset_text("cluster.html")
    assert "/*__CLUSTER_JS__*/" in asset_text("cluster.html")
    assert "--n8:" in asset_text("cluster.css")
    for name in ("cluster.css", "cluster.js"):
        text = asset_text(name).lower()
        assert "</script" not in text and "</style" not in text
        assert "http://" not in text and "https://" not in text


def test_write_cluster_report(payload: dict[str, Any], tmp_path: Path) -> None:
    out = tmp_path / "cluster.html"
    info = write_cluster_report(payload, out)
    assert info.path == out and info.bytes == out.stat().st_size
    assert (info.nodes, info.groups, info.entries) == (
        2,
        len(payload["groups"]),
        len(payload["entries"]),
    )
    assert cluster_data(out.read_text(encoding="utf-8")) == payload
    with pytest.raises(ValueError):
        write_cluster_report({"schema": "report.v1"}, tmp_path / "x.html")
