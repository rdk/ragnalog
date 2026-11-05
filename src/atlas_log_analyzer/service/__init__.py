"""Service layer: builds every JSON document of the CLI and the web API.

The front ends are thin adapters: they parse their input into the request models of
:mod:`~atlas_log_analyzer.service.params`, call a builder and render the document
(:mod:`~atlas_log_analyzer.cli` as json/jsonl/table, :mod:`~atlas_log_analyzer.webapp`
as HTTP responses). Failures are :class:`~atlas_log_analyzer.service.errors.OpError`,
which carries the ``error.v1`` code, exit code and HTTP status of both front ends.

Modules: ``documents`` (typed msgspec Structs of every document), ``errors`` (error
model), ``params`` (request models), ``views`` (column presets, text capping,
signature prefixes, ``frames.v1``), ``sets`` (the ``Sets`` protocol and set
references), ``queries`` (sets, stats, groups, entries, context, frames, timeline),
``compare`` (diff), ``ingest`` (ingest, regroup, remove, report file) and ``meta``
(version, formats, schema, health).

This package imports no cyclopts, FastAPI or rich, and nothing is re-exported here:
import from the submodules.
"""
