"""One error model for the CLI and the web API (``error.v1``).

An :class:`OpError` is a failure reported to the user: an ``error.v1`` code, a
message, an optional next-step ``hint``, an optional ``reason`` and extra keys
(``candidates``, ``store``). :data:`SPEC` gives every code its CLI exit code and
HTTP status, so both front ends map an error the same way.

The codes predate this module and agents depend on them, so where the two front
ends report one failure under different codes the shared ``reason`` key names it:
an unknown set is ``store_error`` (exit 3) in the CLI and ``not_found`` (404) in
the API, both with ``"reason": "set_not_found"`` (:data:`REASONS`). An error is
always built for one front end (:data:`Front`), which picks the code and the hint.

``docs/cli.md`` and ``docs/webapp.md`` hold the same tables as :data:`SPEC` and
:data:`REASONS` (``tests/service/test_errors.py`` compares them).
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from enum import IntEnum
from typing import Any, Final, Literal, NamedTuple

__all__ = [
    "REASONS",
    "SPEC",
    "ErrorSpec",
    "ExitCode",
    "Front",
    "OpError",
    "Reason",
    "flag",
    "from_exception",
    "hint",
]

type Front = Literal["cli", "api"]
"""The front end an error or a message is meant for."""


class ExitCode(IntEnum):
    """Process exit codes of the CLI."""

    OK = 0
    USAGE = 1
    """Invalid arguments, unknown format, bad SQL, version mismatch without ``--force``."""
    NOT_FOUND = 2
    """Nothing found (no files, no entries for a signature) or nothing could be parsed."""
    STORE = 3
    """Store failure: unknown log set, corrupt manifest, unsupported schema version."""


class ErrorSpec(NamedTuple):
    """How one ``error.v1`` code is reported."""

    exit: ExitCode
    """CLI exit code."""
    http: int
    """HTTP status of the web API."""
    meaning: str
    """When the code is used (the docs' error tables)."""


SPEC: Final[Mapping[str, ErrorSpec]] = {
    "usage": ErrorSpec(
        ExitCode.USAGE,
        400,
        "Bad option or argument (parse error, unexpected argument, "
        "`brief` with `columns`, a page over the API's cap).",
    ),
    "invalid_value": ErrorSpec(
        ExitCode.USAGE,
        400,
        "A value that does not parse or is out of range (time, column, "
        "limit, bucket); in the CLI also sets that are not comparable.",
    ),
    "invalid_level": ErrorSpec(ExitCode.USAGE, 400, "Unknown level name."),
    "unknown_format": ErrorSpec(ExitCode.USAGE, 400, "Unknown format name."),
    "sql_error": ErrorSpec(ExitCode.USAGE, 400, "DuckDB rejected the SQL of `query`."),
    "ambiguous": ErrorSpec(
        ExitCode.USAGE,
        409,
        "A signature prefix (or, in the API, a set reference) matches "
        "several; signature errors list `candidates`.",
    ),
    "incompatible_sets": ErrorSpec(
        ExitCode.USAGE,
        409,
        "The API's code for sets built with different signature or mask-rule versions or specs.",
    ),
    "missing_dependency": ErrorSpec(
        ExitCode.USAGE, 500, "An optional extra is not installed (`mine`, `web`)."
    ),
    "unauthorized": ErrorSpec(ExitCode.USAGE, 401, "Missing or wrong API token."),
    "payload_too_large": ErrorSpec(ExitCode.USAGE, 413, "An upload over the size limit."),
    "file_not_found": ErrorSpec(ExitCode.NOT_FOUND, 404, "A named file or directory is missing."),
    "no_sources": ErrorSpec(ExitCode.NOT_FOUND, 422, "No log files in the given paths."),
    "format_not_detected": ErrorSpec(ExitCode.NOT_FOUND, 422, "No format recognises the files."),
    "not_in_format": ErrorSpec(
        ExitCode.NOT_FOUND, 422, "A named file over 64 MiB is not in the format (force reads it)."
    ),
    "no_entries": ErrorSpec(ExitCode.NOT_FOUND, 422, "No entries could be parsed."),
    "not_found": ErrorSpec(
        ExitCode.NOT_FOUND, 404, "No such group, entry or job; in the API also no such set."
    ),
    "store_error": ErrorSpec(
        ExitCode.STORE,
        500,
        "Store failure: corrupt manifest, newer schema, store refused or "
        "in use; in the CLI also an unknown or ambiguous set.",
    ),
    "http": ErrorSpec(ExitCode.USAGE, 400, "Another HTTP error (the API sends its own status)."),
    "internal_error": ErrorSpec(
        ExitCode.USAGE, 500, "Unexpected exception (set `ATLAS_LOG_DEBUG=1` for the traceback)."
    ),
}
"""Every ``error.v1`` code with its CLI exit code and HTTP status."""


class Reason(NamedTuple):
    """One failure that the front ends report under their own codes."""

    cli: str
    """The CLI's ``error.v1`` code."""
    api: str
    """The web API's ``error.v1`` code."""
    meaning: str


REASONS: Final[Mapping[str, Reason]] = {
    "set_not_found": Reason(
        "store_error", "not_found", "No log set has this id, name or id prefix."
    ),
    "ambiguous_ref": Reason(
        "store_error", "ambiguous", "Several log sets share this name or id prefix."
    ),
    "ambiguous_signature": Reason(
        "ambiguous", "ambiguous", "A signature prefix matches several groups (`candidates`)."
    ),
    "incompatible_sets": Reason(
        "invalid_value",
        "incompatible_sets",
        "The sets were built with different signature "
        "or mask-rule versions or specs (`diff` takes force).",
    ),
    "group_not_found": Reason(
        "not_found", "not_found", "No group (or no entries) with this signature."
    ),
    "entry_not_found": Reason("not_found", "not_found", "No entry with this id."),
    "ref_not_found": Reason(
        "not_found", "not_found", "`frames`: neither an entry id nor a group signature."
    ),
}
"""Values of the ``reason`` key and the code each front end reports them under."""

_HINTS: Final[Mapping[str, Mapping[Front, str]]] = {
    "set_not_found": {"api": "list the sets with GET /api/sets"},
    "ambiguous_signature": {
        "cli": "pass more characters of the signature, or the full one",
        "api": "pass more characters of the signature, or the full one",
    },
    "group_not_found": {
        "cli": "list signatures with `atlas-log groups {set}`",
        "api": "list signatures with GET /api/sets/{id}/groups",
    },
    "entry_not_found": {
        "cli": 'entry ids are the "id" column of `atlas-log entries {set} --brief` '
        "or `atlas-log show {set} SIGNATURE`",
        "api": "entry ids are the id column of GET /api/sets/{id}/entries",
    },
    "ref_not_found": {
        "cli": "pass an entry id (`atlas-log entries {set} --with-exception --brief`) "
        "or a group signature (`atlas-log groups {set} --with-exception --brief`)",
        "api": "pass an entry id or a signature from GET /api/sets/{id}/groups?with_exception=true",
    },
    "incompatible_sets": {
        "cli": "pass --force to compare anyway",
        "api": "pass force=true to compare anyway",
    },
    "not_in_format": {
        "cli": "name the right file or --format, or pass --force to read it anyway",
        "api": "upload the right file or format, or send force=true",
    },
}


def hint(key: str, front: Front, **context: str) -> str | None:
    """Return the next-step hint of a reason or code for ``front`` (``None`` if it has none).

    Fields: ``set`` (the set's name or id, as the user would type it) and ``id``.
    """
    template = _HINTS.get(key, {}).get(front)
    return template.format(**context) if template is not None else None


def flag(name: str, front: Front) -> str:
    """How ``front`` spells a parameter in messages: ``--max-raw`` or ``max_raw``."""
    return "--" + name.replace("_", "-") if front == "cli" else name


class OpError(Exception):
    """A failure reported as an ``error.v1`` document plus an exit code or HTTP status.

    Build it for one front end: the code is that front end's, and :attr:`exit_code` /
    :attr:`status` come from :data:`SPEC` (an unknown code: exit 1, HTTP 500).
    :meth:`because` builds the error of a :data:`REASONS` entry.
    """

    def __init__(
        self,
        code: str,
        message: str,
        *,
        hint: str | None = None,
        reason: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> None:
        """Create the error; ``extra`` adds keys to the ``error`` object (e.g. candidates)."""
        super().__init__(message)
        self.code = code
        self.message = message
        self.hint = hint
        """Optional suggestion for the next step (``"hint"`` in ``error.v1``)."""
        self.reason = reason
        """Optional :data:`REASONS` key, the same in both front ends."""
        self.extra = dict(extra or {})
        """Extra keys of the ``error`` object, e.g. ``{"store": path}``."""

    @classmethod
    def because(
        cls,
        reason: str,
        front: Front,
        message: str,
        *,
        hint: str | None = None,
        extra: Mapping[str, Any] | None = None,
    ) -> OpError:
        """Build the error of a :data:`REASONS` entry as ``front`` reports it."""
        return cls(getattr(REASONS[reason], front), message, hint=hint, reason=reason, extra=extra)

    @property
    def exit_code(self) -> ExitCode:
        """CLI exit code (:data:`SPEC`)."""
        spec = SPEC.get(self.code)
        return spec.exit if spec is not None else ExitCode.USAGE

    @property
    def status(self) -> int:
        """HTTP status (:data:`SPEC`)."""
        spec = SPEC.get(self.code)
        return spec.http if spec is not None else 500

    def envelope(self) -> dict[str, Any]:
        """Return the ``error.v1`` document."""
        error: dict[str, Any] = {"code": self.code, "message": self.message}
        if self.hint:
            error["hint"] = self.hint
        if self.reason is not None:
            error["reason"] = self.reason
        error.update(self.extra)
        return {"schema": "error.v1", "error": error}


def _message(exc: BaseException) -> str:
    # str() of a LookupError with one argument adds quotes around it.
    return str(exc.args[0]) if len(exc.args) == 1 else str(exc)


def _loaded(module: str, name: str) -> type[BaseException] | None:
    """Return the exception class ``module.name`` if that module is loaded, else ``None``.

    An exception of a module that was never imported cannot have been raised, so
    mapping an error loads neither polars nor the ingest pipeline.
    """
    found = getattr(sys.modules.get(module), name, None)
    return found if isinstance(found, type) and issubclass(found, BaseException) else None


def _is(exc: BaseException, module: str, name: str) -> bool:
    cls = _loaded(module, name)
    return cls is not None and isinstance(exc, cls)


_PIPELINE_CODES: Final = (
    ("NoSourcesFound", "no_sources"),
    ("FormatNotDetected", "format_not_detected"),
    ("NotInFormat", "not_in_format"),
)


def from_exception(exc: BaseException, front: Front) -> OpError | None:
    """Map a known library exception to an :class:`OpError` for ``front``; ``None`` otherwise.

    Typed exceptions are matched by class (never by message): the store's
    ``LogSetNotFound``/``AmbiguousLogSet``, the pipeline's ``NoSourcesFound``,
    ``FormatNotDetected`` and ``NotInFormat``, ``AmbiguousSignature``,
    ``IncompatibleSets``, ``InvalidLevel``, ``UnknownFormatError``,
    ``FileNotFoundError`` and, last, any ``StoreError`` or ``ValueError``.
    """
    if isinstance(exc, OpError):
        return exc
    views, store, pipeline = (
        "atlas_log_analyzer.service.views",
        "atlas_log_analyzer.store.store",
        "atlas_log_analyzer.store.pipeline",
    )
    if _is(exc, views, "AmbiguousSignature"):
        return OpError.because(
            "ambiguous_signature",
            front,
            str(exc),
            hint=hint("ambiguous_signature", front),
            extra={"candidates": list(getattr(exc, "candidates", []))},
        )
    if _is(exc, store, "LogSetNotFound"):
        return OpError.because("set_not_found", front, str(exc), hint=hint("set_not_found", front))
    if _is(exc, store, "AmbiguousLogSet"):
        return OpError.because("ambiguous_ref", front, str(exc))
    if _is(exc, "atlas_log_analyzer.store.query", "IncompatibleSets"):
        return OpError.because(
            "incompatible_sets", front, str(exc), hint=hint("incompatible_sets", front)
        )
    if _is(exc, "atlas_log_analyzer.store.levels", "InvalidLevel"):
        return OpError("invalid_level", str(exc))
    for name, code in _PIPELINE_CODES:
        if _is(exc, pipeline, name):
            return OpError(code, str(exc), hint=hint(code, front))
    if _is(exc, store, "StoreError"):
        return OpError("store_error", str(exc))
    if _is(exc, "atlas_log_analyzer.core.formats.registry", "UnknownFormatError"):
        return OpError("unknown_format", _message(exc))
    if isinstance(exc, FileNotFoundError):
        name = exc.filename if exc.filename is not None else _message(exc)
        return OpError("file_not_found", f"no such file or directory: {name}")
    if isinstance(exc, ValueError):
        return OpError("invalid_value", str(exc))
    return None
