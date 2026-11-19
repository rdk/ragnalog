"""Core library: log formats, streaming parser, masking, signatures, grouping.

This package depends only on the standard library and ``xxhash``. It must not
import from ``ragnalog.store``, ``.cli`` or ``.webapp``.
"""

from ragnalog.core.grouping import Group, Grouper, SignedEntry
from ragnalog.core.masking import Masker
from ragnalog.core.models import (
    ExceptionInfo,
    Frame,
    LogEntry,
    LogLevel,
    SourceRef,
)
from ragnalog.core.parser import parse
from ragnalog.core.pipeline import Recipe
from ragnalog.core.reader import discover_sources
from ragnalog.core.signature import SignatureSpec, Signer

__all__ = [
    "ExceptionInfo",
    "Frame",
    "Group",
    "Grouper",
    "LogEntry",
    "LogLevel",
    "Masker",
    "Recipe",
    "SignatureSpec",
    "SignedEntry",
    "Signer",
    "SourceRef",
    "discover_sources",
    "parse",
]
