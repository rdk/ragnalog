r"""Deterministic signatures that identify an entry *type*.

A signature is an xxh3-64 hex digest of canonical parts joined by ``\x1f``.
It is stable across runs, files and machines for a given
:class:`SignatureSpec` and masking-rule version.

Canonical parts, in order:

1. format name;
2. level name (``include_level``);
3. logger (``include_logger``);
4. masked first line of the message (the *title*; with ``titles_from_body``, an entry
   whose header message is blank uses the first body line that is neither blank nor
   decoration, :func:`~ragnalog.core.decoration.lead_line`);
5. per exception of the chain (``include_exceptions``): type, masked message key;
6. per exception: locations of its top ``frames`` frames (``frames > 0``).

The *message key* of an exception is the first line of its message; when that
line ends with ``:`` and more lines follow (Groovy ``startup failed:``), the
first following non-empty line is appended so that different compile errors
stay distinguishable.
"""

from __future__ import annotations

from dataclasses import dataclass

import xxhash

from ragnalog.core.decoration import lead_line
from ragnalog.core.masking import Masker
from ragnalog.core.models import ExceptionInfo, LogEntry

__all__ = [
    "COARSE_SPEC",
    "DEFAULT_SPEC",
    "SIGNATURE_VERSION",
    "Signature",
    "SignatureSpec",
    "Signer",
    "exception_message_key",
]

SIGNATURE_VERSION = 2
"""Bump when the canonical-parts algorithm changes; recorded in manifests.

2: trailing white space of the title and of exception message keys is dropped (the same
message with and without a trailing space was two groups); a part holding the separator
``\\x1f`` is escaped, so parts can never run into each other.
"""


@dataclass(frozen=True, slots=True)
class SignatureSpec:
    """What participates in a signature."""

    include_level: bool = True
    include_logger: bool = True
    include_exceptions: bool = True
    """Exception chain: types and masked messages (not frames)."""
    frames: int = 0
    """Number of top frames of each exception to include (``0`` = none)."""

    @property
    def key(self) -> str:
        """Short stable textual key, e.g. ``"L+G+E+F0"``; stored in manifests."""
        parts = []
        if self.include_level:
            parts.append("L")
        if self.include_logger:
            parts.append("G")
        if self.include_exceptions:
            parts.append("E")
        parts.append(f"F{self.frames}")
        return "+".join(parts)

    @classmethod
    def from_key(cls, key: str) -> SignatureSpec:
        """Inverse of :attr:`key`; ``ValueError`` when ``key`` is not one.

        Example: ``SignatureSpec.from_key("L+G+E+F2") == SignatureSpec(frames=2)``.
        """
        parts = key.split("+")
        last = parts[-1]
        flags = set(parts[:-1])
        if not last.startswith("F") or not last[1:].isdigit() or not flags <= {"L", "G", "E"}:
            raise ValueError(f"not a signature spec key: {key!r}")
        spec = cls(
            include_level="L" in flags,
            include_logger="G" in flags,
            include_exceptions="E" in flags,
            frames=int(last[1:]),
        )
        if spec.key != key:
            raise ValueError(f"not a signature spec key: {key!r}")
        return spec


DEFAULT_SPEC = SignatureSpec()
COARSE_SPEC = SignatureSpec(include_exceptions=False)


@dataclass(frozen=True, slots=True)
class Signature:
    """Result of signing an entry."""

    hash: str
    """16 hex chars (xxh3-64)."""
    template: str
    """Human-readable template of the message (first line), placeholders included."""
    parts: tuple[str, ...]
    """Canonical parts that were hashed, in order."""
    variables: tuple[str, ...]
    """Variables extracted from the message while masking."""


def _escaped(part: str) -> str:
    r"""Return ``part`` with ``\x1e`` and the separator ``\x1f`` escaped by ``\x1e``."""
    if "\x1e" not in part and "\x1f" not in part:
        return part
    return part.replace("\x1e", "\x1e\x1e").replace("\x1f", "\x1e\x1f")


def exception_message_key(exc: ExceptionInfo) -> str:
    """Return the (unmasked) part of an exception message that participates in signatures."""
    message = exc.message
    if not message:
        return ""
    first, sep, rest = message.partition("\n")
    first = first.rstrip()
    if sep and first.endswith(":"):
        for line in rest.split("\n"):
            if line.strip():
                return f"{first} {line.strip()}"
    return first


class Signer:
    """Computes :class:`Signature` for entries with a given masker and spec.

    With ``titles_from_body`` (a format's opt-in, passed by
    :meth:`~ragnalog.core.pipeline.Recipe.signer`) an entry whose message is
    blank is masked and signed by its body's
    :func:`~ragnalog.core.decoration.lead_line` instead (:meth:`title`).
    """

    def __init__(
        self, masker: Masker, spec: SignatureSpec = DEFAULT_SPEC, *, titles_from_body: bool = False
    ) -> None:
        self.masker = masker
        self.spec = spec
        self.titles_from_body = titles_from_body
        # Without exceptions an entry's parts under ``spec`` and COARSE_SPEC are equal
        # when both treat level and logger alike (exceptions and frames add nothing).
        self._coarse_if_no_exceptions = (
            spec.include_level == COARSE_SPEC.include_level
            and spec.include_logger == COARSE_SPEC.include_logger
        )

    def title(self, entry: LogEntry) -> str:
        """Return the text masked into the template: the message, or the body's lead line.

        The lead line stands in only with ``titles_from_body`` and a message that is
        empty or white space; the entry's ``message`` itself is never changed. Trailing
        white space is dropped (it would split one message into two groups).
        """
        message = entry.message
        if self.titles_from_body and (not message or message.isspace()):
            return lead_line(entry.body).rstrip()
        return message.rstrip()

    def sign(self, entry: LogEntry) -> Signature:
        """Compute the signature of ``entry``."""
        template, variables = self.masker.apply(self.title(entry))
        return self._make(entry, self.spec, template, variables, self._exception_parts(entry))

    def sign_both(self, entry: LogEntry) -> tuple[Signature, Signature]:
        """Return ``(signature, coarse_signature)`` sharing one masking pass.

        For an entry without exceptions both are the same object when the spec
        and :data:`COARSE_SPEC` agree on level and logger (the default).
        """
        template, variables = self.masker.apply(self.title(entry))
        exc_parts = self._exception_parts(entry)
        full = self._make(entry, self.spec, template, variables, exc_parts)
        if not entry.exceptions and self._coarse_if_no_exceptions:
            return full, full
        coarse = self._make(entry, COARSE_SPEC, template, variables, exc_parts)
        return full, coarse

    def _exception_parts(self, entry: LogEntry) -> tuple[tuple[str, str], ...]:
        """Return ``(type, masked message key)`` per exception.

        Hashed only when the spec includes exceptions, but always used for the
        display template of entries with an empty message.
        """
        if not entry.exceptions:
            return ()
        mask = self.masker.template
        return tuple((exc.type, mask(exception_message_key(exc))) for exc in entry.exceptions)

    def _make(
        self,
        entry: LogEntry,
        spec: SignatureSpec,
        template: str,
        variables: tuple[str, ...],
        exc_parts: tuple[tuple[str, str], ...],
    ) -> Signature:
        parts = [entry.format]
        if spec.include_level:
            parts.append(entry.level.name)
        if spec.include_logger:
            parts.append(entry.logger)
        parts.append(template)
        if spec.include_exceptions:
            for exc_type, exc_message in exc_parts:
                parts.append(exc_type)
                parts.append(exc_message)
        if spec.frames > 0:
            for exc in entry.exceptions:
                parts.extend(frame.location for frame in exc.frames[: spec.frames])
        display = template
        if not template.strip() and exc_parts:
            exc_type, exc_message = exc_parts[0]
            display = f"{exc_type}: {exc_message}" if exc_message else exc_type
        joined = "\x1f".join(_escaped(p) for p in parts)
        digest = xxhash.xxh3_64_hexdigest(joined.encode("utf-8", "surrogatepass"))
        return Signature(digest, display, tuple(parts), variables)
