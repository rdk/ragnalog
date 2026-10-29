r"""Deterministic signatures that identify an entry *type*.

A signature is an xxh3-64 hex digest of canonical parts joined by ``\x1f``.
It is stable across runs, files and machines for a given
:class:`SignatureSpec` and masking-rule version.

Canonical parts, in order:

1. format name;
2. level name (``include_level``);
3. logger (``include_logger``);
4. masked first line of the message;
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

from atlas_log_analyzer.core.masking import Masker
from atlas_log_analyzer.core.models import ExceptionInfo, LogEntry

__all__ = [
    "COARSE_SPEC",
    "DEFAULT_SPEC",
    "SIGNATURE_VERSION",
    "Signature",
    "SignatureSpec",
    "Signer",
    "exception_message_key",
]

SIGNATURE_VERSION = 1
"""Bump when the canonical-parts algorithm changes; recorded in manifests."""


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


def exception_message_key(exc: ExceptionInfo) -> str:
    """Return the (unmasked) part of an exception message that participates in signatures."""
    message = exc.message
    if not message:
        return ""
    first, sep, rest = message.partition("\n")
    if sep and first.endswith(":"):
        for line in rest.split("\n"):
            if line.strip():
                return f"{first} {line.strip()}"
    return first


class Signer:
    """Computes :class:`Signature` for entries with a given masker and spec."""

    def __init__(self, masker: Masker, spec: SignatureSpec = DEFAULT_SPEC) -> None:
        self.masker = masker
        self.spec = spec

    def sign(self, entry: LogEntry) -> Signature:
        """Compute the signature of ``entry``."""
        template, variables = self.masker.apply(entry.message)
        return self._make(entry, self.spec, template, variables, self._exception_parts(entry))

    def sign_both(self, entry: LogEntry) -> tuple[Signature, Signature]:
        """Return ``(signature, coarse_signature)`` sharing one masking pass."""
        template, variables = self.masker.apply(entry.message)
        exc_parts = self._exception_parts(entry)
        full = self._make(entry, self.spec, template, variables, exc_parts)
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
        digest = xxhash.xxh3_64_hexdigest("\x1f".join(parts).encode("utf-8", "surrogatepass"))
        return Signature(digest, display, tuple(parts), variables)
