"""`manifest` (and bare `ragnalog` on a pipe): the agent manifest, manifest.v1."""

from __future__ import annotations

import inspect
import json
import re
from pathlib import Path

import msgspec

from ragnalog.cli.app import app
from ragnalog.cli.manifest import ENV, WORKFLOW, build_command, build_manifest
from ragnalog.service.documents import (
    DOCUMENTS,
    Manifest,
    ManifestCommandDetail,
    convert,
)
from ragnalog.service.views import DETAIL_BRIEF, ENTRY_BRIEF, GROUP_BRIEF

from .conftest import Invoke

SRC = Path(__file__).resolve().parents[2] / "src" / "ragnalog"
COMMANDS = [n for n in app if not n.startswith("-")]


def _manifest(cli: Invoke) -> dict[str, object]:
    result = cli("--output", "json", "manifest")
    assert result.code == 0
    doc: dict[str, object] = result.json()
    return doc


def test_manifest_is_a_valid_small_document(cli: Invoke) -> None:
    result = cli("--output", "json", "manifest")
    assert result.code == 0 and result.stderr == ""
    doc = result.json()
    assert isinstance(convert(doc), Manifest)
    assert len(result.stdout.strip().encode()) < 16 * 1024  # budget (AGENTS.md); about 10 KB today
    assert doc == msgspec.to_builtins(build_manifest())


def test_manifest_covers_every_command(cli: Invoke) -> None:
    commands = _manifest(cli)["commands"]
    assert isinstance(commands, dict)
    assert list(commands) == COMMANDS
    assert "manifest" in commands
    for name, command in commands.items():
        assert 0 in command["exit"], name
        doc = inspect.getdoc(app[name].default_command) or ""
        if command.get("output") is not None:
            assert f'"schema": "{command["output"]}"' in doc, name
            assert command["output"] in DOCUMENTS, name
    assert commands["groups"]["output"] == "groups.v1"
    assert commands["groups"]["exit"] == [0, 1, 3]
    assert "[--limit int=50]" in commands["groups"]["usage"]
    assert "[--brief]" in commands["groups"]["usage"]
    assert commands["groups"]["usage"].startswith("LOGSET ")
    assert commands["ingest"]["usage"].startswith("PATHS... ")
    assert (
        "[--sort count|last_seen|first_seen|signature|template=count]"
        in (commands["groups"]["usage"])
    )
    assert "output" not in commands["serve"]  # serve prints nothing


def test_manifest_workflow_and_help(cli: Invoke) -> None:
    doc = _manifest(cli)
    steps = [s["run"].split()[0] for s in doc["workflow"]]  # type: ignore[attr-defined]
    assert steps == [run.split()[0] for run, _ in WORKFLOW]
    assert steps[:3] == ["ingest", "triage", "groups"] and steps[-1] == "diff"
    assert all(step["purpose"] for step in doc["workflow"])  # type: ignore[attr-defined]
    assert "query" in [s["run"].split()[0] for s in doc["when"]]  # type: ignore[attr-defined]
    help_ = doc["help"]
    assert isinstance(help_, dict)
    assert help_["LOGSET"] == "Log set id, unique id prefix or name"
    for option in ("--limit", "--since", "--columns", "--grow", "--rate", "--baseline"):
        assert help_[option], option
    assert all(len(text) <= 40 for text in help_.values())


def test_manifest_presets_globals_documents_env(cli: Invoke) -> None:
    doc = _manifest(cli)
    assert doc["presets"] == {
        "groups": list(GROUP_BRIEF),
        "entries": list(ENTRY_BRIEF),
        "show, context": list(DETAIL_BRIEF),
    }
    options = doc["global_options"]
    assert isinstance(options, dict)
    assert set(options) == {"--output", "--store", "--quiet", "--width", "--color"}
    assert options["--output"].startswith("table|json|jsonl: ")
    assert doc["documents"]["names"] == sorted(DOCUMENTS)  # type: ignore[index]
    assert "ragnalog schema NAME" in doc["documents"]["get"]  # type: ignore[index]
    assert doc["exit_codes"] == {
        "0": "ok",
        "1": "usage error",
        "2": "nothing found / parse failure",
        "3": "store error",
    }


def test_manifest_env_lists_every_variable_the_code_reads() -> None:
    mentioned = set()
    for path in SRC.rglob("*.py"):
        mentioned |= set(re.findall(r"\bRAGNALOG_[A-Z_]*[A-Z]\b", path.read_text("utf-8")))
    assert mentioned <= set(ENV)


def test_bare_invocation_on_a_pipe_prints_the_manifest(cli: Invoke) -> None:
    result = cli()
    assert result.code == 0
    assert result.json() == _manifest(cli)


def test_bare_invocation_in_table_mode_prints_the_help(cli: Invoke) -> None:
    result = cli("--output", "table")
    assert result.code == 0
    assert result.stdout.startswith("Usage: ragnalog")


def test_manifest_table_mode_is_indented_json(cli: Invoke) -> None:
    result = cli("--output", "table", "manifest")
    assert result.code == 0
    assert json.loads(result.stdout)["schema"] == "manifest.v1"
    assert result.stdout.startswith('{\n  "schema"')


def test_manifest_command_describes_one_command_in_full(cli: Invoke) -> None:
    for name in COMMANDS:
        result = cli("--output", "json", "manifest", name)
        assert result.code == 0, name
        doc = result.json()
        assert isinstance(convert(doc), ManifestCommandDetail), name
        assert doc == msgspec.to_builtins(build_command(app, name)), name
    frames = cli("--output", "json", "manifest", "frames").json()
    assert frames["output"] == "frames.v1" and frames["usage"].startswith("LOGSET REF ")
    assert "Output (json):" in frames["description"] and "Args:" not in frames["description"]
    params = {p["name"]: p for p in frames["params"]}
    assert params["--limit"]["type"] == "int" and params["--limit"]["default"] == "10"
    assert params["--all"]["type"] == "flag" and "default" not in params["--all"]
    assert params["LOGSET"]["help"]


def test_manifest_unknown_command(cli: Invoke) -> None:
    result = cli("--output", "json", "manifest", "nope")
    assert result.code == 2
    error = result.json()["error"]
    assert error["code"] == "not_found" and "frames" in error["hint"]
