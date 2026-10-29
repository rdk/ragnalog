"""Core library: log formats, streaming parser, masking, signatures, grouping.

This package depends only on the standard library and ``xxhash``. It must not
import from ``atlas_log_analyzer.store``, ``.cli`` or ``.webapp``.
"""

from atlas_log_analyzer.core.grouping import Group, Grouper, SignedEntry
from atlas_log_analyzer.core.masking import Masker
from atlas_log_analyzer.core.models import (
    ExceptionInfo,
    Frame,
    LogEntry,
    LogLevel,
    SourceRef,
)
from atlas_log_analyzer.core.parser import parse
from atlas_log_analyzer.core.reader import discover_sources
from atlas_log_analyzer.core.signature import SignatureSpec, Signer

__all__ = [
    "ExceptionInfo",
    "Frame",
    "Group",
    "Grouper",
    "LogEntry",
    "LogLevel",
    "Masker",
    "SignatureSpec",
    "SignedEntry",
    "Signer",
    "SourceRef",
    "discover_sources",
    "parse",
]
