"""Remote mode (``--server``): commands ask a web app's ``/api`` and print what a local run prints.

Every parity case (``tests/contract/test_parity.py``) runs once locally and once through
``--server`` against the same data directory served by the web app in-process; the documents
must agree as the parity contract says (same allowed differences), errors on their exit code
and reason. Requests go through ``ragnalog.cli.remote.transport``, replaced by a TestClient.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import re
import urllib.error
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

import pytest

pytest.importorskip("fastapi")
pytest.importorskip("httpx")

from fastapi.testclient import TestClient

from ragnalog.cli import remote
from ragnalog.cli.app import COMMANDS, run
from ragnalog.store.pipeline import ingest_paths
from ragnalog.store.store import Store
from ragnalog.webapp import Settings, create_app
from tests.contract.test_parity import (  # noqa: F401 (fixtures)
    CASES,
    CLUSTER_CASES,
    FIXTURES,
    Case,
    _identity,
    _normalise,
    client,
    cluster_client,
    cluster_dir,
    cluster_refs,
    refs,
    store_dir,
)

SERVER = "http://testserver"

Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]


def _over(app_client: TestClient) -> Transport:
    def transport(
        method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
    ) -> tuple[int, bytes]:
        response = app_client.request(method, url, headers=dict(headers), content=body)
        return response.status_code, response.content

    return transport


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("RAGNALOG_SERVER", "RAGNALOG_TOKEN", "RAGNALOG_STORE", "RAGNALOG_APP_PACKAGES"):
        monkeypatch.delenv(name, raising=False)


def _run(argv: list[str], capsys: pytest.CaptureFixture[str]) -> tuple[int, Any]:
    capsys.readouterr()
    code = run(["--output", "json", *argv])
    out = capsys.readouterr().out
    return code, json.loads(out) if out.strip() else None


def _agree(
    case: Case,
    store: Path,
    app_client: TestClient,
    names: dict[str, str],
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    argv = [a.format(**names) for a in case.cli]
    local_code, local_doc = _run(["--store", str(store), *argv], capsys)
    monkeypatch.setattr(remote, "transport", _over(app_client))
    remote_code, remote_doc = _run(["--server", SERVER, *argv], capsys)
    assert remote_code == local_code, (local_doc, remote_doc)
    if local_code:
        assert _identity(remote_doc) == _identity(local_doc), (local_doc, remote_doc)
        return
    keys = case.allowed | case.masked
    assert _normalise(remote_doc, keys) == _normalise(local_doc, keys)


def _cases(cases: list[Case]) -> list[Any]:
    return [
        pytest.param(
            c,
            id=c.id,
            marks=[pytest.mark.xfail(strict=True, reason=c.drift)] if c.drift else [],
        )
        for c in cases
    ]


@pytest.mark.parametrize("case", _cases(CASES))
def test_remote_matches_local(
    case: Case,
    store_dir: Path,  # noqa: F811
    client: TestClient,  # noqa: F811
    refs: dict[str, str],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _agree(case, store_dir, client, refs, capsys, monkeypatch)


@pytest.mark.parametrize("case", _cases(CLUSTER_CASES))
def test_remote_capture_views_match_local(
    case: Case,
    cluster_dir: Path,  # noqa: F811
    cluster_client: TestClient,  # noqa: F811
    cluster_refs: dict[str, str],  # noqa: F811
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _agree(case, cluster_dir, cluster_client, cluster_refs, capsys, monkeypatch)


@pytest.mark.parametrize(
    "argv",
    [
        pytest.param(
            ["query", "edge", "select level_name, count(*) n from entries group by 1 order by 1"],
            id="query",
        ),
        pytest.param(["query", "edge", "select count(*) from nope"], id="query-error"),
        pytest.param(["entries", "capture:cap-twin", "--limit", "3"], id="capture-entries"),
        pytest.param(["timeline", "cap-twin", "--bucket", "1h"], id="bare-capture-id"),
        pytest.param(["set", "nope"], id="unknown-set"),
    ],
)
def test_remote_more_commands_match_local(
    argv: list[str],
    store_dir: Path,  # noqa: F811
    client: TestClient,  # noqa: F811
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    case = Case("extra", argv, "", frozenset({"store", "summary", "items[].summary"}))
    _agree(case, store_dir, client, {}, capsys, monkeypatch)


# --- coverage: every option of a remote command is a parameter of its API route

ROUTE_COMMANDS: dict[str, tuple[str, str]] = {
    "sets": ("browse", "sets"),
    "captures": ("browse", "captures"),
    "set": ("browse", "set_"),
    "groups": ("browse", "groups"),
    "show": ("browse", "show"),
    "entries": ("browse", "entries"),
    "entries-capture": ("browse", "entries"),
    "context": ("browse", "context"),
    "frames": ("browse", "frames"),
    "query": ("browse", "query"),
    "stats": ("browse", "stats"),
    "users": ("browse", "users"),
    "capture": ("cluster", "capture"),
    "timeline": ("cluster", "timeline"),
    "timeline-capture": ("cluster", "timeline"),
    "matrix": ("cluster", "matrix"),
    "events": ("cluster", "events"),
    "interactions": ("cluster", "interactions"),
    "diff": ("compare", "diff"),
    "triage": ("compare", "triage"),
    "label": ("ingest", "label"),
    "remove": ("admin", "remove"),
    "formats": ("admin", "formats_"),
    "version": ("admin", "version"),
}


def _command(module: str, name: str) -> Callable[..., Any]:
    mod = __import__(f"ragnalog.cli.commands.{module}", fromlist=[name])
    return getattr(mod, name)  # type: ignore[no-any-return]


def _shape(path: str) -> str:
    """A path with its placeholder names dropped (the command's and the API's names differ)."""
    return re.sub(r"\{[^}]*\}", "{}", path)


def _api_path(spec: dict[str, Any], path: str) -> str:
    hits = [p for p in spec["paths"] if _shape(p) == _shape(path)]
    assert len(hits) == 1, (path, hits)
    return hits[0]


def test_every_route_key_names_a_command() -> None:
    assert set(ROUTE_COMMANDS) == set(remote.ROUTES)


def test_every_command_is_remote_local_or_static() -> None:
    remote_commands = {n for _, n in ROUTE_COMMANDS.values()}
    static = {"manifest", "schema", "batch"}
    for name, target in COMMANDS:
        function = target.split(":")[1]
        assert function in remote_commands or name in remote.LOCAL_ONLY or name in static, name


@pytest.mark.parametrize("key", sorted(ROUTE_COMMANDS))
def test_every_option_is_an_api_parameter(key: str, tmp_path: Path) -> None:
    route = remote.ROUTES[key]
    func = _command(*ROUTE_COMMANDS[key])
    spec = create_app(Settings(store=tmp_path)).openapi()
    op = spec["paths"][_api_path(spec, route.path)]
    method = op[route.method.lower()]
    api = {p["name"] for p in method.get("parameters", [])}
    body = method.get("requestBody", {}).get("content", {}).get("application/json", {})
    if body:
        schema = body["schema"]
        if "$ref" in schema:
            schema = spec["components"]["schemas"][schema["$ref"].rsplit("/", 1)[1]]
        api |= set(schema.get("properties", {}))
    path_names = set(route.path.replace("{", " ").replace("}", " ").split()) - {"api"}
    for name, param in inspect.signature(func).parameters.items():
        if name in path_names or name in route.ignore or name in route.local:
            continue
        default = param.default
        names = (
            [f.name for f in dataclasses.fields(default)]
            if dataclasses.is_dataclass(default) and not isinstance(default, type)
            else [route.rename.get(name, name.rstrip("_"))]
        )
        for n in names:
            assert n in api, f"{key}: --{n.replace('_', '-')} has no API parameter ({sorted(api)})"


def test_route_paths_match_the_api(tmp_path: Path) -> None:
    spec = create_app(Settings(store=tmp_path)).openapi()
    for key, route in remote.ROUTES.items():
        path = _api_path(spec, route.path)
        assert route.method.lower() in spec["paths"][path], key


# --- refusals and errors


@pytest.fixture
def small(tmp_path: Path) -> Path:
    root = tmp_path / "store"
    ingest_paths(Store(root), [FIXTURES / "sample.log"], name="sample")
    return root


@pytest.mark.parametrize(
    ("argv", "needle"),
    [
        (["regroup", "sample", "--spec", "L+G+E+F1"], "needs a data directory"),
        (["upgrade", "--all"], "needs a data directory"),
        (["ingest", "x.log"], "needs a data directory"),
        (["show", "sample", "abcd", "--raw"], "--raw is not available"),
        (["entries", "sample", "--raw"], "--raw is not available"),
        (["formats", "--sample", "x"], "--sample is not available"),
    ],
)
def test_local_only_is_refused(
    argv: list[str],
    needle: str,
    small: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with TestClient(create_app(Settings(store=small))) as c:
        monkeypatch.setattr(remote, "transport", _over(c))
        code, doc = _run(["--server", SERVER, *argv], capsys)
    assert code == 1
    assert doc["error"]["code"] == "usage" and needle in doc["error"]["message"]


def test_token(
    small: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(store=small, token="s3cret"))) as c:
        monkeypatch.setattr(remote, "transport", _over(c))
        code, doc = _run(["--server", SERVER, "sets"], capsys)
        assert (code, doc["error"]["code"]) == (1, "unauthorized")
        assert "RAGNALOG_TOKEN" in doc["error"]["hint"]
        monkeypatch.setenv("RAGNALOG_TOKEN", "s3cret")
        code, doc = _run(["--server", SERVER, "sets"], capsys)
    assert code == 0 and doc["schema"] == "sets.v1"


def test_env_var_selects_the_server(
    small: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    with TestClient(create_app(Settings(store=small))) as c:
        monkeypatch.setattr(remote, "transport", _over(c))
        monkeypatch.setenv("RAGNALOG_SERVER", SERVER)
        code, doc = _run(["stats", "sample"], capsys)
    assert code == 0 and doc["schema"] == "stats.v1"


def test_unreachable_server(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def down(*_: Any) -> tuple[int, bytes]:
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(remote, "transport", down)
    code, doc = _run(["--server", "https://logs.example.org", "sets"], capsys)
    assert (code, doc["error"]["code"]) == (1, "http")
    assert "cannot reach https://logs.example.org" in doc["error"]["message"]


def test_not_a_url(capsys: pytest.CaptureFixture[str]) -> None:
    code, doc = _run(["--server", "logs.example.org", "sets"], capsys)
    assert (code, doc["error"]["code"]) == (1, "invalid_value")


def test_older_server_without_the_route(
    capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    def old(*_: Any) -> tuple[int, bytes]:
        body = {"schema": "error.v1", "error": {"code": "not_found", "message": "Not Found"}}
        return 404, json.dumps(body).encode()

    monkeypatch.setattr(remote, "transport", old)
    code, doc = _run(["--server", SERVER, "query", "sample", "select 1"], capsys)
    assert (code, doc["error"]["code"]) == (1, "usage")
    assert "older ragnalog" in doc["error"]["message"]


def test_batch_runs_each_line_remotely(
    small: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    lines = '["stats", "sample"]\n["set", "nope"]\n'
    monkeypatch.setattr("sys.stdin", __import__("io").StringIO(lines))
    with TestClient(create_app(Settings(store=small))) as c:
        monkeypatch.setattr(remote, "transport", _over(c))
        capsys.readouterr()
        run(["--output", "jsonl", "--server", SERVER, "batch"])
        out = [json.loads(line) for line in capsys.readouterr().out.splitlines() if line.strip()]
    schemas = [d.get("schema") for d in out]
    assert "stats.v1" in json.dumps(out) and "error.v1" in json.dumps(out), schemas


# --- the query endpoint


def test_query_endpoint(small: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from ragnalog.webapp import api

    with TestClient(create_app(Settings(store=small))) as c:
        ok = c.post("/api/sets/sample/query", json={"sql": "select count(*) n from entries"})
        assert ok.status_code == 200 and ok.json()["schema"] == "query.v1"
        assert ok.json()["truncated"] is False
        bad = c.post("/api/sets/sample/query", json={"sql": "select * from nope"})
        assert (bad.status_code, bad.json()["error"]["code"]) == (400, "sql_error")
        two = c.post("/api/sets/sample/query", json={"sql": "select 1; select 2"})
        assert (two.status_code, two.json()["error"]["code"]) == (400, "usage")
        files = c.post(
            "/api/sets/sample/query", json={"sql": "select * from read_csv('/etc/passwd')"}
        )
        assert files.status_code == 400
        monkeypatch.setattr(api, "QUERY_MAX_ROWS", 2)
        cut = c.post("/api/sets/sample/query", json={"sql": "select id from entries"})
        assert (cut.json()["count"], cut.json()["truncated"]) == (2, True)
        monkeypatch.setattr(api, "QUERY_TIMEOUT_S", 0.2)
        slow = c.post(
            "/api/sets/sample/query",
            json={"sql": "select sum(a.range * b.range) from range(200000) a, range(200000) b"},
        )
        assert (slow.status_code, slow.json()["error"]["code"]) == (400, "sql_error")
        assert "longer than 0.2 s" in slow.json()["error"]["message"]
