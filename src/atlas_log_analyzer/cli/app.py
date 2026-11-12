"""The ``atlas-log`` command line application: root app, global options, entry points.

The commands live in :mod:`atlas_log_analyzer.cli.commands` (``ingest``, ``browse``,
``cluster``, ``compare``, ``admin``) and are registered here by import path, so a command loads
only its own module. Each is a thin adapter: it parses its options into the request
models of :mod:`atlas_log_analyzer.service.params`, calls a builder of
:mod:`atlas_log_analyzer.service` and renders the document
(:mod:`atlas_log_analyzer.cli.output`, :mod:`atlas_log_analyzer.cli.render`).
Global options (``--output``, ``--store``, ``--quiet``) are parsed by the meta app
and may appear anywhere on the line; errors are reported here (:func:`dispatch`).
"""

from __future__ import annotations

import dataclasses
import errno
import inspect
import io
import os
import sys
from collections.abc import Callable, Iterable, Sequence
from typing import Annotated, Any

from cyclopts import App, CycloptsError, Group, Parameter, UnusedCliTokensError

from atlas_log_analyzer import __version__
from atlas_log_analyzer.cli import context as ctx
from atlas_log_analyzer.cli.output import (
    ColorMode,
    OpError,
    Output,
    OutputMode,
    default_mode,
    error_from_exception,
)
from atlas_log_analyzer.config import Settings

__all__ = ["COMMANDS", "app", "dispatch", "main", "run"]

GLOBAL = Group("Global options", sort_key=100)

app = App(
    name="atlas-log",
    help=(
        "Parse, group and compare application log files (Jira Data Center first).\n\n"
        "Typical flow: `ingest` log files into a log set; `triage` it (new kinds of\n"
        "errors since the previous set?); list `groups --brief` (one row per message\n"
        "signature; `groups --with-exception` for stack traces at any level); drill\n"
        "into one group with `show` (its entries), `frames` (its stack trace) and\n"
        "`context` (what the thread did around an entry); compare two sets with\n"
        "`diff --rate`. `set SET` prints one set, `sets --brief` all of them.\n"
        "Output is a table on a terminal and JSON otherwise (--output json|jsonl|table).\n"
        'Every JSON document has a "schema" key; listings put rows under "items".\n'
        'Errors in json/jsonl mode: {"schema": "error.v1", "error": {"code", "message"}}\n'
        "on stdout. Exit codes: 0 ok, 1 usage error, 2 nothing found / parse failure,\n"
        "3 store error. SET arguments accept a log-set id, a unique id prefix or a name;\n"
        "signatures may be given as a unique prefix of at least 4 hex characters.\n"
        "Agents: `manifest` prints all of this as JSON (so does bare `atlas-log` on a pipe);\n"
        "`batch` runs many commands in one process."
    ),
    version=__version__,
    default_parameter=Parameter(negative=()),
    help_format="plaintext",
)

COMMANDS: tuple[tuple[str, str], ...] = (
    ("ingest", "ingest:ingest"),
    ("sets", "browse:sets"),
    ("set", "browse:set_"),
    ("captures", "browse:captures"),
    ("capture", "cluster:capture"),
    ("label", "ingest:label"),
    ("groups", "browse:groups"),
    ("show", "browse:show"),
    ("entries", "browse:entries"),
    ("context", "browse:context"),
    ("frames", "browse:frames"),
    ("query", "browse:query"),
    ("stats", "browse:stats"),
    ("timeline", "cluster:timeline"),
    ("matrix", "cluster:matrix"),
    ("events", "cluster:events"),
    ("interactions", "cluster:interactions"),
    ("diff", "compare:diff"),
    ("triage", "compare:triage"),
    ("report", "compare:report"),
    ("parse", "ingest:parse"),
    ("regroup", "ingest:regroup"),
    ("upgrade", "ingest:upgrade"),
    ("formats", "admin:formats_"),
    ("schema", "admin:schema"),
    ("manifest", "admin:manifest"),
    ("remove", "admin:remove"),
    ("serve", "admin:serve"),
    ("version", "admin:version"),
    ("batch", "admin:batch"),
)
"""Command name and ``module:function`` in :mod:`atlas_log_analyzer.cli.commands`, in the
order of ``--help``. Registered by import path: a command imports only its module."""

for _name, _target in COMMANDS:
    app.command(f"atlas_log_analyzer.cli.commands.{_target}", name=_name)


def _load_all() -> None:
    """Import every command module, so that the root ``--help`` shows each summary."""
    for name, _ in COMMANDS:
        app[name]


@app.meta.default
def _meta(
    *tokens: Annotated[str, Parameter(show=False, allow_leading_hyphen=True)],
    output: Annotated[OutputMode | None, Parameter(group=GLOBAL)] = None,
    store: Annotated[str | None, Parameter(group=GLOBAL)] = None,
    quiet: Annotated[bool, Parameter(group=GLOBAL, negative="")] = False,
    width: Annotated[int | None, Parameter(group=GLOBAL)] = None,
    color: Annotated[ColorMode, Parameter(group=GLOBAL)] = "auto",
) -> int:
    """Run a command with global options applied.

    Args:
        tokens: Command and its arguments.
        output: Output mode. Default: table when stdout is a terminal, json otherwise.
        store: Data directory holding the log sets (env ATLAS_LOG_STORE, ignored when empty;
            default ./.atlas-log, which ingest/serve do not create inside a git work tree).
        quiet: Suppress progress and diagnostics on stderr (warnings and errors still shown).
        width: Table width; default: the terminal width, else $COLUMNS, else 160.
        color: Colour tables and raw entries: auto (terminal and no $NO_COLOR), always, never.

    """
    if width is not None and width < 40:
        return Output(output or default_mode()).error(
            OpError("invalid_value", "--width must be >= 40")
        )
    try:
        settings = Settings.load({"store": store}, os.environ)
    except ValueError as exc:  # an ATLAS_LOG_* variable that does not parse
        return Output(output or default_mode()).error(OpError("invalid_value", str(exc)))
    out = Output(
        output or default_mode(),
        quiet=quiet,
        width=width,
        color_mode=color,
        app_packages=settings.app_packages,
    )
    here = ctx.Context(out, settings)
    ctx.set_current(here)
    if not tokens and out.mode != "table":  # bare `atlas-log` on a pipe: the agent manifest
        from atlas_log_analyzer.cli.commands.admin import emit_manifest

        emit_manifest(out)
        return 0
    return dispatch(tokens, here, unexpected=None)


def dispatch(
    tokens: Sequence[str], here: ctx.Context, *, unexpected: Callable[[Exception], OpError] | None
) -> int:
    """Run one command line in ``here``; report known errors and return the exit code.

    The error boundary of the CLI: an :class:`OpError` or a known library exception
    becomes ``error.v1`` (stdout in json/jsonl mode, stderr in table mode) and its
    exit code. Unknown exceptions propagate, unless ``unexpected`` maps them
    (``batch`` keeps going).
    """
    out = here.output
    try:
        result = app(tokens, exit_on_error=False, print_error=False, result_action="return_value")
    except CycloptsError as exc:
        return out.error(_usage_error(exc, tokens))
    except Exception as exc:
        err = error_from_exception(exc)
        if err is None:
            if unexpected is None:
                raise
            err = unexpected(exc)
        if err.reason == "set_not_found":
            err.extra.setdefault("store", here.store.as_posix())
        return out.error(err)
    return int(result) if isinstance(result, int) else 0


# --- entry points


def _sniff_mode(tokens: Sequence[str]) -> OutputMode:
    for i, tok in enumerate(tokens):
        value: str | None = None
        if tok == "--output" and i + 1 < len(tokens):
            value = tokens[i + 1]
        elif tok.startswith("--output="):
            value = tok.partition("=")[2]
        if value in ("table", "json", "jsonl"):
            return value  # type: ignore[return-value]
    return default_mode()


def run(argv: Sequence[str] | None = None) -> int:
    """Run the CLI with ``argv`` (default: ``sys.argv[1:]``) and return the exit code.

    Known errors are reported and mapped to exit codes; unexpected exceptions propagate.
    """
    tokens = list(sys.argv[1:] if argv is None else argv)
    ctx.set_current(None)
    if not any(token in app for token in tokens if not token.startswith("-")):
        _load_all()  # root --help (or a bare call) lists every command with its summary
    try:
        result = app.meta(
            tokens, exit_on_error=False, print_error=False, result_action="return_value"
        )
    except CycloptsError as exc:
        return Output(_sniff_mode(tokens)).error(_usage_error(exc, tokens))
    return int(result) if isinstance(result, int) else 0


def _usage_error(exc: CycloptsError, tokens: Sequence[str]) -> OpError:
    """Map a cyclopts parse error to ``usage`` with the command's ``--help`` as hint.

    Cyclopts' messages are kept (they name the option and the expected values),
    except "Unused Tokens", which becomes "unexpected argument 'x'" and says what
    the command takes.
    """
    chain = tuple(exc.command_chain or ())
    command = chain[-1] if chain and chain[-1] in app else None
    hint = f"atlas-log {command} --help" if command else "atlas-log --help"
    message = str(exc).strip()
    if isinstance(exc, UnusedCliTokensError) and command and exc.unused_tokens:
        message = _unexpected(command, list(exc.unused_tokens), tokens)
    return OpError("usage", message, hint=hint)


def _unexpected(command: str, unused: Sequence[str], tokens: Sequence[str]) -> str:
    """Message for arguments a command did not consume (cyclopts' "Unused Tokens")."""
    func = app[command].default_command
    params = inspect.signature(func).parameters if func is not None else {}
    words = ", ".join(repr(t) for t in unused)
    what = f"unexpected argument{'s' if len(unused) > 1 else ''} {words}"
    at = [i for i, token in enumerate(tokens) if token == unused[0]]
    before = tokens[at[-1] - 1] if at and at[-1] > 0 else ""
    if before.startswith("--") and "=" not in before:
        name = before[2:].replace("-", "_")
        defaults = _option_defaults(params.values())
        if isinstance(defaults.get(name, defaults.get(name + "_")), bool):
            return f"{what}; {before} is a flag and takes no value"
    kinds = (inspect.Parameter.POSITIONAL_ONLY, inspect.Parameter.POSITIONAL_OR_KEYWORD)
    names = [p.name.upper() for p in params.values() if p.kind in kinds]
    if not names:
        return f"{what}; {command} takes no arguments"
    count = f"{len(names)} argument{'s' if len(names) > 1 else ''}"
    return f"{what}; {command} takes {count}: {' '.join(names)}"


def _option_defaults(params: Iterable[inspect.Parameter]) -> dict[str, Any]:
    """Default of every option, the fields of flattened dataclasses (``name="*"``) included."""
    out: dict[str, Any] = {}
    for p in params:
        if dataclasses.is_dataclass(p.default) and not isinstance(p.default, type):
            out.update({f.name: getattr(p.default, f.name) for f in dataclasses.fields(p.default)})
        else:
            out[p.name] = p.default
    return out


def _utf8_streams() -> None:
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")


def _stdout_closed(exc: BaseException) -> bool:
    """Tell whether ``exc`` means that the reader of stdout has gone (e.g. ``| head``).

    POSIX raises :class:`BrokenPipeError`. Windows raises ``OSError(EINVAL)`` from
    the write or flush instead; it is told apart from other EINVAL errors (such as
    an invalid file name) by flushing stdout again, which fails the same way while
    the unwritten data is still buffered.
    """
    if isinstance(exc, BrokenPipeError):
        return True
    if not isinstance(exc, OSError) or exc.errno != errno.EINVAL:
        return False
    try:
        sys.stdout.flush()
    except OSError:
        return True
    return False


def _debug() -> bool:
    """``ATLAS_LOG_DEBUG`` of this invocation (also when its settings could not be built)."""
    if (here := ctx.peek()) is not None:
        return here.settings.debug
    return bool(os.environ.get("ATLAS_LOG_DEBUG", "").strip())


def main() -> None:
    """Console-script entry point (``atlas-log``)."""
    _utf8_streams()
    tokens = sys.argv[1:]
    try:
        code = run(tokens)
    except KeyboardInterrupt:
        code = 130
    except Exception as exc:
        if _stdout_closed(exc):
            # Downstream closed the pipe; silence the flush at exit.
            devnull = os.open(os.devnull, os.O_WRONLY)
            os.dup2(devnull, sys.stdout.fileno())
            sys.exit(1)
        if _debug():
            raise
        here = ctx.peek()
        out = here.output if here is not None else Output(_sniff_mode(tokens))
        code = out.error(
            OpError("internal_error", f"{type(exc).__name__}: {exc} (set ATLAS_LOG_DEBUG=1)")
        )
    sys.exit(code)


if __name__ == "__main__":
    main()
