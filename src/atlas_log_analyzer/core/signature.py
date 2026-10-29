"""Deterministic signatures that identify an entry *type*.

A signature is an xxh3-64 hex digest of canonical parts joined by ``\\x1f``.
It is stable across runs, files and machines for a given
:class:`SignatureSpec` and masking-rule version.
"""

from __future__ import annotations

from dataclasses import dataclass

from atlas_log_analyzer.core.masking import Masker
from atlas_log_analyzer.core.models import LogEntry

__all__ = [
    "COARSE_SPEC",
    "DEFAULT_SPEC",
    "SIGNATURE_VERSION",
    "Signature",
    "SignatureSpec",
    "Signer",
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
        raise NotImplementedError


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


class Signer:
    """Computes :class:`Signature` for entries with a given masker and spec."""

    def __init__(self, masker: Masker, spec: SignatureSpec = DEFAULT_SPEC) -> None:
        self.masker = masker
        self.spec = spec

    def sign(self, entry: LogEntry) -> Signature:
        """Compute the signature of ``entry``."""
        raise NotImplementedError

    def sign_both(self, entry: LogEntry) -> tuple[Signature, Signature]:
        """Return ``(signature, coarse_signature)`` sharing one masking pass."""
        raise NotImplementedError
