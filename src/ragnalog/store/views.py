"""Deprecated location of :mod:`ragnalog.service.views` (kept for imports).

The JSON views moved to the service layer, which builds every document of the CLI
and the web API; import from :mod:`ragnalog.service.views` instead.
"""

from ragnalog.service.views import (
    DEFAULT_MAX_RAW,
    DETAIL_BRIEF,
    ENTRY_BRIEF,
    ENTRY_FIELDS,
    GROUP_BRIEF,
    GROUP_FIELDS,
    SET_BRIEF,
    SIGNATURE_PREFIX_MIN,
    AmbiguousSignature,
    cap_text,
    cap_texts,
    diff_warning,
    frames_view,
    resolve_signature,
    select_columns,
    set_item,
    set_summary,
)

__all__ = [
    "DEFAULT_MAX_RAW",
    "DETAIL_BRIEF",
    "ENTRY_BRIEF",
    "ENTRY_FIELDS",
    "GROUP_BRIEF",
    "GROUP_FIELDS",
    "SET_BRIEF",
    "SIGNATURE_PREFIX_MIN",
    "AmbiguousSignature",
    "cap_text",
    "cap_texts",
    "diff_warning",
    "frames_view",
    "resolve_signature",
    "select_columns",
    "set_item",
    "set_summary",
]
