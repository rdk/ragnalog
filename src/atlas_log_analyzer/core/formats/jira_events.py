"""Event rules of Jira Data Center logs (``JiraFormat.event_rules``).

Ordered from specific to generic: the first rule that matches a group decides its
kind (an ``[INDEX-REPLAY]`` line that mentions restoring the index is a replay, a
cluster lock taken by a job runner is a lock). Loggers are matched with packages
abbreviated (:func:`~atlas_log_analyzer.core.events.abbreviate`), so
``com.atlassian.jira.index.ha.X`` also matches Jira's ``c.a.j.index.ha.X``.

Bump :data:`~atlas_log_analyzer.core.events.EVENT_RULES_VERSION` when changing them.
"""

from __future__ import annotations

from atlas_log_analyzer.core.events import SUBJECT_LOGGER, EventRule

__all__ = ["JIRA_EVENT_RULES"]

_JIRA = "com.atlassian.jira."
_STARTUP = "com.atlassian.jira.startup."

JIRA_EVENT_RULES: tuple[EventRule, ...] = (
    # index lifecycle (DefaultNodeReindexService, IndexUtils, DefaultCompositeIndexLifecycleManager)
    EventRule(
        "index_replay",
        _JIRA + "index.ha.DefaultNodeReindexService",
        r"^\[INDEX-REPLAY\](?:.*?\bsourceNodeId=(?P<node>[^,;\s]+))?",
        "node",
    ),
    EventRule(
        "index_restore",
        _JIRA,
        r"(?i)\brestor(?:e|es|ed|ing)\b.{0,40}\bindex|\bindex(?:es)?\b.{0,40}\brestor(?:e|ed|ing)\b",
    ),
    EventRule(
        "reindex",
        _JIRA,
        r"(?i)^(?:re-?index ?all\b|re-?indexing (?:started|finished|failed)\b|re-?index took\b)",
    ),
    EventRule("index_snapshot", _JIRA, r"(?i)\bindex snapshot\b"),
    EventRule("index_snapshot", _JIRA + "util.compression.ArchiveUtils", r"^Creating archive\b"),
    EventRule(
        "index_replication", _JIRA, r"(?i)\bindex replication\b|\breplicat\w*\b.{0,40}\bindex"
    ),
    # cache replication between nodes
    EventRule(
        "cache_replication",
        "com.atlassian.troubleshooting.jira.healthcheck.cluster.ClusterReplicationHealthCheck",
        r"\bNode (?P<node>\S+) does not seem to replicate",
        "node",
    ),
    EventRule(
        "cache_peer",
        _JIRA + "cluster.distribution.localq.rmi.CachingRMICachePeerManager",
        r"(?i)\bre-?creating cache peer for node:? ?(?P<node>[^\s,]+)",
        "node",
    ),
    # health checks
    EventRule(
        "health_failed",
        "com.atlassian.troubleshooting.healthcheck.concurrent.SupportHealthCheckProcess",
        r"^Health check '(?P<check>[^']+)' failed",
        "check",
    ),
    # cluster locks (any logger, after the index snapshot lock above)
    EventRule(
        "cluster_lock",
        "",
        r"(?i)\bcluster lock\b(?: acquired)?(?: for (?P<lock>[^,;\s]+))?",
        "lock",
    ),
    # start and stop of the node
    EventRule(
        "stop",
        _STARTUP + "DefaultJiraLauncher",
        r"(?i)\b(?:stopping|shutting down|shut down|shutdown)\b",
    ),
    EventRule(
        "stop",
        _STARTUP + "LauncherContextListener",
        r"(?i)\b(?:stopping|shutting down|shut down|shutdown)\b",
    ),
    EventRule("start", _STARTUP + "JiraStartupLogger", ""),
    EventRule(
        "start", _STARTUP + "LauncherContextListener", r"(?i)startup is complete|ready to serve"
    ),
    # scheduled jobs: a job runner's lines, else lines naming a job id, else job start/end lines
    EventRule("job_run", "", "", SUBJECT_LOGGER, logger_re=r"JobRunner(?:Impl)?$"),
    EventRule(
        "job_run",
        "",
        r"\bjob ?[Ii]d[=:] ?(?P<job>[^\s,\]][^,\]]*?)"
        r"(?=,|\]|\.?$|\.\s| - \w+[:=]| \w+=| finished\b| started\b)",
        "job",
    ),
    EventRule(
        "job_run",
        "",
        r"(?i)^(?:running|starting|started|finished)\b.*\bjob\b"
        r"|\bjob\b.*\b(?:started|finished|completed)\b|\bskipping job\b",
        SUBJECT_LOGGER,
    ),
)
"""Event rules of Jira DC, most specific first.

Kinds: ``index_replay`` (subject: ``sourceNodeId``), ``index_restore``, ``reindex``
(full re-index start and end), ``index_snapshot`` (snapshot creation, its cluster lock
and archive), ``index_replication``, ``cache_replication`` (subject: the node that
does not replicate), ``cache_peer`` (subject: the node whose peer is re-created),
``health_failed`` (subject: the check), ``cluster_lock`` (subject: the lock),
``stop``, ``start``, ``job_run`` (subject: the job key: the job runner's class, the
``jobId``, or the logger's class).
"""
