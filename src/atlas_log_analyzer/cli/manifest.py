"""The ``manifest.v1`` document: how an agent uses ``atlas-log``, in about 7 KB of JSON.

Generated from the cyclopts app (commands, parameters with type, default and
help, the output document named in each docstring, exit codes), from
:mod:`atlas_log_analyzer.service.documents` (document names) and
:mod:`atlas_log_analyzer.service.views` (``--brief`` presets) and
:data:`atlas_log_analyzer.config.ENV_VARS` (environment). Only :data:`WORKFLOW`,
:data:`WHEN` and :data:`NOTES` are written here.
This module generates them: :func:`package_data` is written to
``service/manifest.json`` by ``docs/formats/generate.py``, and
:mod:`atlas_log_analyzer.service.manifest` serves that file to ``atlas-log
manifest [COMMAND]``, bare ``atlas-log`` on a pipe and ``GET /api/manifest[/COMMAND]``.
"""

from __future__ import annotations

import inspect
import json
import re
import types
import typing
from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING, Any, Literal

from atlas_log_analyzer import __version__
from atlas_log_analyzer.config import ENV_VARS
from atlas_log_analyzer.service.documents import (
    Manifest,
    ManifestCommand,
    ManifestCommandDetail,
    ManifestDocuments,
    ManifestParam,
    ManifestStep,
    document_names,
)
from atlas_log_analyzer.service.views import DETAIL_BRIEF, ENTRY_BRIEF, GROUP_BRIEF

if TYPE_CHECKING:
    from cyclopts import App

__all__ = [
    "ENV",
    "NOTES",
    "WHEN",
    "WORKFLOW",
    "build_command",
    "build_manifest",
    "package_data",
    "render_package_file",
]

WORKFLOW: tuple[tuple[str, str], ...] = (
    ("ingest PATH... --name N", "parse logs into a stored set"),
    ("triage LOGSET", "new kinds of errors since the previous set?"),
    ("groups LOGSET --brief", "groups by count; --with-exception: any stack trace"),
    ("show LOGSET SIGNATURE --brief", "entries of one group"),
    ("frames LOGSET SIGNATURE", "its stack trace, application frames first"),
    ("context LOGSET ENTRY_ID", "what the thread did around one entry"),
    ("entries LOGSET --request-id ID", "one request"),
    ("diff SET_A SET_B --rate", "compare two sets of any length"),
)
"""The usual order of commands with a one-line purpose (hand-written)."""

WHEN: tuple[tuple[str, str], ...] = (
    ("query LOGSET SQL", "aggregates no command gives (DuckDB SQL)"),
    ("batch", "several commands in one process"),
    ("sets --brief", "what is in the store"),
)
"""Commands outside the workflow and when to use them (hand-written); commands the app
does not have are left out."""

NOTES: tuple[str, ...] = (
    'JSON on a pipe: "schema" names the document, listings have "items"',
    "errors: error.v1 on stdout with a hint",
    'paging: "truncated"; next --offset = offset + count',
    "times: ISO 8601, YYYY-MM-DD or 2h/1d before the last entry",
)
"""Conventions shared by every command (hand-written)."""

ENV: dict[str, str] = {
    **{var.name: var.short for var in ENV_VARS},
    "NO_COLOR": "--color auto: no colour",
    "COLUMNS": "default of --width",
}
"""Environment variables read by atlas-log: the settings' (:data:`~atlas_log_analyzer.config.
ENV_VARS`) and the two of the terminal."""

_OUTPUT = re.compile(r'"schema": "([\w.-]+\.v\d+)"')
_EXIT = re.compile(r"(?:Exit codes:|;)\s*(\d)\b")
_CODE = re.compile(r"(\d) ([^,.;]+)")


_SENTENCE = re.compile(r"(?<!e\.g)(?<!i\.e)\.(?:\s|$)")
_PAREN = re.compile(r"\s*\([^()]*\)")


def _one_line(text: str | None, limit: int = 40) -> str:
    """First sentence of ``text`` on one line without parentheses, at most ``limit`` chars.

    A longer sentence is cut at its last clause (``;``, ``:``, ``,``) or word that fits.
    """
    line = _SENTENCE.split(" ".join(str(text or "").split()), maxsplit=1)[0]
    line = _PAREN.sub("", line).strip().rstrip(".")
    if len(line) <= limit:
        return line
    head = line[: limit + 1]
    for mark in ("; ", ": ", ", "):
        if (cut := head.rfind(mark)) > limit // 2:
            return head[:cut]
    return head[: head.rfind(" ")]


def _type(hint: Any) -> str:
    """Compact type name: str, int, float, path, flag, a|b|c; ``*`` = repeatable."""
    args = [a for a in typing.get_args(hint) if a is not type(None)]
    origin = typing.get_origin(hint)
    if origin in (typing.Union, types.UnionType) and len(args) == 1:
        return _type(args[0])
    if origin is Literal:
        return "|".join(str(a) for a in typing.get_args(hint))
    if origin in (list, tuple):
        return _type(args[0]) + "*"
    if hint is bool:
        return "flag"
    if isinstance(hint, type) and issubclass(hint, Path):
        return "path"
    return getattr(hint, "__name__", str(hint))


def _spec(arg: Any) -> tuple[str, str]:
    """``(name, "TYPE[=DEFAULT]")`` of one cyclopts argument; positionals are upper-case."""
    name = str(arg.parameter.name[0])
    if arg.field_info.kind is inspect.Parameter.VAR_POSITIONAL:
        name += "..."
    kind = _type(arg.hint)
    default = arg.field_info.default
    if kind == "flag" or default is None or default is inspect.Parameter.empty:
        return name, kind
    return name, f"{kind}={default}"


def _params(app: App) -> list[tuple[str, str, str]]:
    """``(name, "TYPE[=DEFAULT]", one-line help)`` of the shown parameters of a command."""
    return [
        (*_spec(arg), _one_line(arg.parameter.help))
        for arg in app.assemble_argument_collection(parse_docstring=True)
        if arg.show
    ]


def _exit_codes(doc: str) -> list[int]:
    start = doc.find("Exit codes:")
    if start < 0:
        return []
    paragraph = " ".join(doc[start:].split("\n\n", 1)[0].split())
    return sorted({int(code) for code in _EXIT.findall(paragraph)})


_FILLER = frozenset({"a", "an", "the", "this", "only", "of", "to"})


def _restates(name: str, text: str) -> bool:
    """Whether ``text`` says no more than the parameter name (``--user``: "Only this user")."""
    said = set(re.findall(r"[a-z0-9]+", text.lower())) - _FILLER
    return said <= set(re.findall(r"[a-z0-9]+", name.lower()))


def _usage(params: list[tuple[str, str, str]]) -> str:
    """``LOGSET [--level str] [--limit int=50] [--brief]`` from :func:`_params`."""
    words = []
    for name, spec, _ in params:
        if not name.startswith("--"):
            words.append(name)
        else:
            words.append(f"[{name}]" if spec == "flag" else f"[{name} {spec}]")
    return " ".join(words)


def build_manifest(app: App | None = None) -> Manifest:
    """Build ``manifest.v1`` from ``app`` (default: the ``atlas-log`` app)."""
    if app is None:
        app = _default_app()
    names = [n for n in app if not n.startswith("-")]
    agent_path = {run.split()[0] for run, _ in (*WORKFLOW, *WHEN)}
    commands: dict[str, ManifestCommand] = {}
    helps: dict[str, list[str]] = {}
    described: set[str] = set()
    for name in names:
        doc = inspect.getdoc(app[name].default_command) or ""
        params = _params(app[name])
        commands[name] = ManifestCommand(
            summary="" if name in agent_path else _one_line(doc, 56),
            usage=_usage(params),
            output=m.group(1) if (m := _OUTPUT.search(doc)) else None,
            exit=_exit_codes(doc),
        )
        for pname, _, text in params:
            helps.setdefault(pname, []).append(text)
            if name in agent_path:
                described.add(pname)
    # one help per parameter name, as most commands give it, for the names that several
    # commands share and for the parameters of the workflow commands
    help_ = {
        pname: text
        for pname, texts in helps.items()
        if (len(texts) > 1 or pname in described)
        and (text := Counter(texts).most_common(1)[0][0])
        and not _restates(pname, text)
    }
    app_help = " ".join(str(app.help).split())
    codes_text = app_help.partition("Exit codes:")[2].split(".", 1)[0]

    def steps(table: tuple[tuple[str, str], ...]) -> list[ManifestStep]:
        return [ManifestStep(run=r, purpose=p) for r, p in table if r.split()[0] in commands]

    return Manifest(
        tool="atlas-log",
        version=__version__,
        usage="atlas-log [GLOBAL OPTIONS] COMMAND ARGS... [OPTIONS]",
        notes=list(NOTES),
        workflow=steps(WORKFLOW),
        when=steps(WHEN),
        commands=commands,
        help=help_,
        global_options={
            p: f"{spec}: {_one_line(h, 32)}"
            for p, spec, h in _params(app.meta)
            if p.startswith("--")
        },
        exit_codes={code: text.strip() for code, text in _CODE.findall(codes_text)},
        documents=ManifestDocuments(
            get="atlas-log schema NAME (JSON Schema); web app: GET /openapi.json",
            names=document_names(),
        ),
        presets={
            "groups": list(GROUP_BRIEF),
            "entries": list(ENTRY_BRIEF),
            "show, context": list(DETAIL_BRIEF),
        },
        env=dict(ENV),
    )


def _default_app() -> App:
    from atlas_log_analyzer.cli.app import app

    return app


_ARGS_SECTION = re.compile(r"\n\s*(?:Args|Parameters):\n", re.IGNORECASE)


def build_command(app: App, name: str) -> ManifestCommandDetail:
    """Build ``manifest-command.v1`` of the command ``name`` of ``app``."""
    command = app[name]
    doc = inspect.getdoc(command.default_command) or ""
    params: list[ManifestParam] = []
    for arg in command.assemble_argument_collection(parse_docstring=True):
        if not arg.show:
            continue
        pname, spec = _spec(arg)
        kind, _, default = spec.partition("=")
        help_ = " ".join(str(arg.parameter.help or "").split())
        params.append(ManifestParam(name=pname, type=kind, default=default or None, help=help_))
    return ManifestCommandDetail(
        command=name,
        version=__version__,
        usage=_usage(_params(command)),
        description=_ARGS_SECTION.split(doc, maxsplit=1)[0].strip(),
        output=m.group(1) if (m := _OUTPUT.search(doc)) else None,
        exit=_exit_codes(doc),
        params=params,
    )


def package_data(app: App | None = None) -> dict[str, Any]:
    """``{"manifest": manifest.v1, "commands": {name: manifest-command.v1}}``, version empty.

    The content of ``service/manifest.json``; ``app`` defaults to the ``atlas-log`` app.
    """
    import msgspec

    app = app or _default_app()
    manifest: dict[str, Any] = msgspec.to_builtins(build_manifest(app))
    manifest["version"] = ""
    commands: dict[str, Any] = {}
    for name in (n for n in app if not n.startswith("-")):
        detail: dict[str, Any] = msgspec.to_builtins(build_command(app, name))
        detail["version"] = ""
        commands[name] = detail
    return {"manifest": manifest, "commands": commands}


def render_package_file(app: App | None = None) -> str:
    """Text of ``service/manifest.json`` (:func:`package_data` as indented JSON)."""
    return json.dumps(package_data(app), indent=1, ensure_ascii=False) + "\n"
