"""Remote mode: commands ask a ragnalog web app's ``/api`` instead of opening a data directory.

With ``--server URL`` (or ``RAGNALOG_SERVER``) a command that reads or labels sets sends the
request its parity case pairs it with (``tests/contract/test_parity.py``) and renders the
document the API returns, so the output and the exit codes are those of a local run
against the server's data directory. The token is ``RAGNALOG_TOKEN`` (``Authorization:
Bearer``). Requests are built from the command's own parameters: path placeholders take
the positional arguments, every other parameter (a filter dataclass flattened into its
fields) becomes a query parameter of the same name, as the API names them; options the API
does not have are listed per route and refused when given.

Standard library only (``urllib``): the CLI imports this module only in remote mode.
"""

from __future__ import annotations

import dataclasses
import functools
import inspect
import json
import string
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from ragnalog import __version__
from ragnalog.config import Settings
from ragnalog.service.errors import REASONS, OpError

__all__ = ["LOCAL_ONLY", "ROUTES", "Route", "fetch", "is_capture", "transport"]

Doc = dict[str, Any]
Transport = Callable[[str, str, Mapping[str, str], bytes | None, float], tuple[int, bytes]]

TIMEOUT_S = 300.0
"""Seconds to wait for one response (a big triage or query on a busy server takes a while)."""


def _show(doc: Doc) -> Doc:
    """Turn the API's ``group.v1`` into the ``entries.v1`` that ``show`` prints locally.

    ``show`` echoes the signature; ``group.v1`` keeps it in ``group`` and adds the hourly
    histogram for its page.
    """
    if doc.get("schema") != "group.v1":
        return doc
    group = doc.get("group") or {}
    out: Doc = {"schema": "entries.v1", "logset": doc.get("logset")}
    if isinstance(group, dict) and "signature" in group:
        out["signature"] = group["signature"]
    out.update({k: v for k, v in doc.items() if k not in ("schema", "logset", "histogram")})
    return out


@dataclass(frozen=True)
class Route:
    """How one command (or one of its branches) is asked of the API."""

    path: str
    """``/api/...`` with ``{name}`` placeholders filled from the command's parameters."""
    method: str = "GET"
    rename: Mapping[str, str] = field(default_factory=dict)
    """Command parameter -> API parameter, where the names differ (``set_a`` -> ``a``)."""
    local: frozenset[str] = frozenset()
    """Parameters the API does not have: refused when not at their default."""
    ignore: frozenset[str] = frozenset()
    """Parameters that only choose the route or the rendering: not sent."""
    body: frozenset[str] = frozenset()
    """For a JSON POST: parameters sent in the body ``{name: value, ...}`` (not ``None``s)."""
    after: Callable[[Doc], Doc] | None = None
    """Turns the API's document into the one the local command prints."""


ROUTES: Mapping[str, Route] = {
    "sets": Route("/api/sets"),
    "captures": Route("/api/captures"),
    "set": Route("/api/sets/{logset}"),
    "groups": Route("/api/sets/{logset}/groups"),
    "show": Route("/api/sets/{logset}/groups/{signature}", local=frozenset({"raw"}), after=_show),
    "entries": Route(
        "/api/sets/{logset}/entries", local=frozenset({"raw"}), ignore=frozenset({"window"})
    ),
    "entries-capture": Route("/api/captures/{logset}/entries", local=frozenset({"raw"})),
    "context": Route("/api/sets/{logset}/entries/{entry_id}/context"),
    "frames": Route("/api/sets/{logset}/frames/{ref}", rename={"all_": "all"}),
    "query": Route("/api/sets/{logset}/query", method="POST", body=frozenset({"sql", "baseline"})),
    "query-capture": Route(
        "/api/captures/{logset}/query", method="POST", body=frozenset({"sql", "baseline"})
    ),
    "stats": Route("/api/sets/{logset}/stats"),
    "users": Route("/api/sets/{logset}/users", ignore=frozenset({"window"})),
    "users-capture": Route("/api/captures/{logset}/users"),
    "capture": Route("/api/captures/{ref}"),
    "timeline": Route("/api/sets/{ref}/timeline", ignore=frozenset({"window", "by_node"})),
    "timeline-capture": Route(
        "/api/captures/{ref}/timeline", ignore=frozenset({"by_node", "by_level"})
    ),
    "matrix": Route("/api/captures/{ref}/matrix"),
    "events": Route("/api/captures/{ref}/events"),
    "interactions": Route("/api/captures/{ref}/interactions"),
    "diff": Route("/api/diff", rename={"set_a": "a", "set_b": "b"}, ignore=frozenset({"level"})),
    "triage": Route("/api/sets/{logset}/triage"),
    "label": Route("/api/sets/{logset}/label", method="POST"),
    "remove": Route("/api/sets/{logset}", method="DELETE"),
    "formats": Route("/api/formats", local=frozenset({"sample"})),
    "version": Route("/api/version"),
}
"""Every command (or branch) the remote mode supports, by the key the command passes."""

LOCAL_ONLY: Mapping[str, str] = {
    "ingest": "upload the files on the web app's start page, or ingest where the data directory is",
    "parse": "",
    "regroup": "run it where the data directory is",
    "upgrade": "run it where the data directory is",
    "report": "open the report page of the web app, or run report where the data directory is",
    "serve": "",
}
"""Commands that need a data directory (or local files) and why; "" = they run locally anyway."""


# --- the request


def _flatten(name: str, value: Any) -> list[tuple[str, Any]]:
    """Return ``(api name, value)`` pairs of one parameter; a dataclass gives one per field."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return [(f.name, getattr(value, f.name)) for f in dataclasses.fields(value)]
    return [(name, value)]


def _query_pairs(name: str, value: Any) -> list[tuple[str, str]]:
    if value is None:
        return []
    if isinstance(value, bool):
        return [(name, "true" if value else "false")]
    if isinstance(value, (list, tuple, set, frozenset)):
        return [(name, str(v)) for v in value]
    return [(name, str(value))]


@functools.cache
def _params(func: Callable[..., Any]) -> Mapping[str, inspect.Parameter]:
    return inspect.signature(func).parameters


def build(
    route: Route, func: Callable[..., Any], args: Mapping[str, Any]
) -> tuple[str, list[tuple[str, str]], bytes | None]:
    """Return the path, query pairs and JSON body for calling ``func(**args)`` remotely.

    Raises:
        OpError: ``usage`` for an option the API does not have.

    """
    params = _params(func)
    path_names = {p for _, p, _, _ in string.Formatter().parse(route.path) if p}
    path_values: dict[str, str] = {}
    pairs: list[tuple[str, str]] = []
    fields: dict[str, Any] = {}
    for name, param in params.items():
        if name not in args:
            continue
        value = args[name]
        if name in path_names:
            path_values[name] = urllib.parse.quote(str(value), safe=":@,")
            continue
        if name in route.ignore:
            continue
        if name in route.local:
            if value != param.default:
                option = "--" + name.rstrip("_").replace("_", "-")
                raise OpError(
                    "usage",
                    f"{option} is not available with --server: the web API has no such option",
                    hint="drop the option, or run the command where the data directory is",
                )
            continue
        if name in route.body:
            if value is not None:
                fields[name] = value
            continue
        for key, item in _flatten(route.rename.get(name, name.rstrip("_")), value):
            pairs.extend(_query_pairs(route.rename.get(key, key), item))
    missing = path_names - path_values.keys()
    if missing:  # a programming error in a route
        raise RuntimeError(f"route {route.path}: no value for {sorted(missing)}")
    body = json.dumps(fields).encode("utf-8") if route.body else None
    return route.path.format(**path_values), pairs, body


# --- the transport


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """Refuse redirects: urllib would send the ``Authorization`` header to the new host."""

    def redirect_request(self, *args: Any, **kwargs: Any) -> None:
        return None  # the 3xx comes back as an HTTPError, reported by request()


_OPENER = urllib.request.build_opener(_NoRedirect)


def _urllib(
    method: str, url: str, headers: Mapping[str, str], body: bytes | None, timeout: float
) -> tuple[int, bytes]:
    request = urllib.request.Request(url, data=body, method=method, headers=dict(headers))
    try:
        with _OPENER.open(request, timeout=timeout) as response:  # http(s) only: _server checks
            return int(response.status), response.read()
    except urllib.error.HTTPError as exc:
        return int(exc.code), exc.read()


transport: Transport = _urllib
"""How requests are sent; tests replace it with a FastAPI ``TestClient``."""


_LOOPBACK = frozenset({"localhost", "127.0.0.1", "::1"})
_WARNED: set[str] = set()
"""Servers already warned about a token over plain http (once per process)."""


def _server(settings: Settings) -> str:
    server = (settings.server or "").strip().rstrip("/")
    if not server.startswith(("http://", "https://")):
        raise OpError(
            "invalid_value",
            f"--server {settings.server!r} is not an http(s) URL",
            hint="give the web app's base URL, e.g. --server https://logs.example.org",
        )
    host = (urllib.parse.urlsplit(server).hostname or "").lower()
    plain = server.startswith("http://") and host not in _LOOPBACK
    if plain and settings.token and not host.endswith(".localhost") and server not in _WARNED:
        _WARNED.add(server)
        sys.stderr.write(
            f"warning: the token goes to {host} over plain http, readable on the way: "
            "use https (a reverse proxy, or tailscale serve)\n"
        )
        sys.stderr.flush()
    return server


def request(
    settings: Settings,
    method: str,
    path: str,
    pairs: list[tuple[str, str]],
    body: bytes | None = None,
) -> Doc:
    """Send one request and return its JSON document.

    Raises:
        OpError: The server's ``error.v1``, under the code the CLI uses for its reason;
            ``http`` when the server cannot be reached or does not answer JSON.

    """
    server = _server(settings)
    url = server + path + ("?" + urllib.parse.urlencode(pairs) if pairs else "")
    headers = {"Accept": "application/json", "User-Agent": f"ragnalog/{__version__}"}
    if body is not None:
        headers["Content-Type"] = "application/json"
    if settings.token:
        headers["Authorization"] = f"Bearer {settings.token}"
    try:
        status, data = transport(method, url, headers, body, TIMEOUT_S)
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        reason = getattr(exc, "reason", exc)
        raise OpError(
            "http",
            f"cannot reach {server}: {reason}",
            hint="check --server (RAGNALOG_SERVER) and the network",
        ) from exc
    try:
        doc = json.loads(data.decode("utf-8")) if data else {}
    except UnicodeDecodeError, json.JSONDecodeError:
        raise OpError(
            "http",
            f"{server} answered HTTP {status} with no JSON document (is it a ragnalog web app?)",
            hint="--server takes the web app's base URL, without /api",
        ) from None
    if status >= 400:
        raise _error(doc, status, server, path)
    if not 200 <= status < 300:
        raise OpError(
            "http",
            f"{server} answered HTTP {status} for {path}"
            + (
                " (a redirect: not followed, it would carry the token)"
                if 300 <= status < 400
                else ""
            ),
            hint="give the web app's final base URL as --server (https:// and the right host)",
        )
    if not isinstance(doc, dict):
        raise OpError("http", f"{server} answered with a JSON {type(doc).__name__}, not a document")
    if not doc:
        raise OpError("http", f"{server} answered HTTP {status} with an empty body for {path}")
    return doc


def _error(doc: Any, status: int, server: str, path: str) -> OpError:
    error = doc.get("error") if isinstance(doc, dict) else None
    if not isinstance(error, dict):
        return OpError("http", f"{server} answered HTTP {status} for {path}")
    code, reason = str(error.get("code") or "http"), error.get("reason")
    message = str(error.get("message") or f"HTTP {status}")
    if status == 401:
        return OpError(
            "unauthorized",
            message,
            hint="set RAGNALOG_TOKEN to the server's token",
        )
    if status in (404, 405) and reason is None and message in ("Not Found", "Method Not Allowed"):
        return OpError(
            "usage",
            f"{server} has no {path}: it runs an older ragnalog than this command needs",
            hint="upgrade the server, or run the command where the data directory is",
        )
    if isinstance(reason, str) and reason in REASONS:
        code = REASONS[reason].cli
    extra = {k: v for k, v in error.items() if k not in ("code", "message", "hint", "reason")}
    extra["server"] = server
    return OpError(code, message, hint=error.get("hint"), reason=reason, extra=extra)


# --- what commands call


def is_capture(settings: Settings, ref: str) -> bool:
    """Whether ``ref`` names a capture on the server (as ``names_capture`` does locally)."""
    from ragnalog.service.captures import is_capture_ref

    if is_capture_ref(ref):
        return True
    quoted = urllib.parse.quote(ref, safe=":@,")
    try:
        request(settings, "GET", f"/api/sets/{quoted}", [])
    except OpError as exc:
        if exc.reason != "set_not_found":
            return False
        try:
            request(settings, "GET", f"/api/captures/{quoted}", [])
        except OpError:
            return False
        return True
    return False


def fetch(settings: Settings, key: str, func: Callable[..., Any], args: Mapping[str, Any]) -> Doc:
    """Run the command ``func`` (its parameters in ``args``) on the server as route ``key``."""
    route = ROUTES[key]
    path, pairs, body = build(route, func, args)
    try:
        doc = request(settings, route.method, path, pairs, body)
    except OpError as exc:
        _cli_hint(exc, args)
        raise
    return route.after(doc) if route.after else doc


def _cli_hint(exc: OpError, args: Mapping[str, Any]) -> None:
    """Give a server error the CLI's hint for its reason (the API's speaks of GET /api/...)."""
    if exc.reason is None:
        return
    from ragnalog.service.errors import hint

    ref = str(next((args[k] for k in ("logset", "ref", "set_b") if args.get(k)), ""))
    if exc.reason == "set_not_found":
        exc.hint = "list the server's sets with `ragnalog sets --brief`"
        return
    exc.hint = hint(exc.reason, "cli", set=ref, id=ref)
