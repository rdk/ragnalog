"""Event rules (``core/events.py``) and the Jira table (``core/formats/jira_events.py``)."""

from __future__ import annotations

import pytest

from ragnalog.core import formats
from ragnalog.core.events import (
    EVENT_KINDS,
    SUBJECT_LOGGER,
    EventRule,
    abbreviate,
    classify,
    event_rules,
    has_placeholder,
)
from ragnalog.core.formats.jira import JiraFormat
from ragnalog.core.formats.jira_events import JIRA_EVENT_RULES


def test_abbreviate_compares_jira_shortened_loggers() -> None:
    assert abbreviate("com.atlassian.jira.index.ha.DefaultNodeReindexService") == (
        "c.a.j.i.h.DefaultNodeReindexService"
    )
    assert abbreviate("c.a.j.index.ha.DefaultNodeReindexService") == (
        "c.a.j.i.h.DefaultNodeReindexService"
    )
    assert abbreviate("com.atlassian.jira.") == "c.a.j."
    assert abbreviate("Plain") == "Plain" and abbreviate("") == ""


def test_placeholders() -> None:
    assert has_placeholder("NODE<NUM>") and has_placeholder("<*>")
    assert not has_placeholder("NODE1") and not has_placeholder("a < b > c")


def test_rule_matches_logger_prefix_regex_and_template() -> None:
    rule = EventRule(
        "cache_peer", "com.acme.cluster.PeerManager", r"peer for (?P<node>\S+)", "node"
    )
    assert rule.matches("c.a.cluster.PeerManager", "Re-creating peer for N2") is not None
    assert rule.matches("c.a.cluster.Other", "Re-creating peer for N2") is None
    assert rule.matches("c.a.cluster.PeerManager", "something else") is None
    any_logger = EventRule("job_run", "", "", SUBJECT_LOGGER, logger_re=r"JobRunner$")
    m = any_logger.matches("org.acme.jobs.CleanupJobRunner", "Cleanup finished")
    assert m is not None
    assert any_logger.subject("org.acme.jobs.CleanupJobRunner", m, None) == "CleanupJobRunner"
    assert any_logger.matches("org.acme.jobs.Cleanup", "x") is None


def test_subject_comes_from_the_message_when_the_template_masked_it() -> None:
    rule = EventRule("cache_replication", "", r"\bNode (?P<node>\S+) does not", "node")
    template = "Node NODE<NUM> does not seem to replicate its cache"
    m = rule.matches("x.Check", template)
    assert m is not None
    message = "Node NODE1 does not seem to replicate its cache"
    assert rule.subject("x.Check", m, message) == "NODE1"
    assert rule.subject("x.Check", m, None) == "NODE<NUM>"
    unmasked = rule.matches("x.Check", "Node DEVNODE does not seem to replicate its cache")
    assert rule.subject("x.Check", unmasked, "ignored") == "DEVNODE"
    assert EventRule("start", "", "").subject("x", m, message) is None


def test_jira_format_exposes_its_rules() -> None:
    assert JiraFormat.event_rules is JIRA_EVENT_RULES
    assert event_rules(formats.get("jira")) == JIRA_EVENT_RULES
    assert event_rules(object()) == ()
    assert {r.kind for r in JIRA_EVENT_RULES} <= set(EVENT_KINDS)
    assert {r.kind for r in JIRA_EVENT_RULES} >= {
        "start",
        "stop",
        "health_failed",
        "cache_replication",
        "cache_peer",
        "index_replication",
        "index_snapshot",
        "index_restore",
        "index_replay",
        "cluster_lock",
        "job_run",
    }


JIRA_CASES = [
    (
        "c.a.j.index.ha.DefaultNodeReindexService",
        "[INDEX-REPLAY] Re-index after last sync detected: operationName=FULL_REINDEX_END, "
        "sourceNodeId=NODE<NUM>, indexTime=<TS>. Resetting index count and restoring index",
        "index_replay",
        "NODE<NUM>",
    ),
    (
        "c.a.j.index.ha.DefaultNodeReindexService",
        "[INDEX-REPLAY] Starting node re-index service, paused=false",
        "index_replay",
        None,
    ),
    (
        "c.a.j.index.DefaultIndexRecoveryManager",
        "Restoring index from backup",
        "index_restore",
        None,
    ),
    (
        "c.a.j.util.index.DefaultCompositeIndexLifecycleManager",
        "Reindex All starting...",
        "reindex",
        None,
    ),
    ("c.a.j.w.a.admin.index.IndexAdminImpl", "Re-indexing finished", "reindex", None),
    ("c.a.j.w.a.admin.index.IndexAdminImpl", "Re-indexing is <NUM>% complete", None, None),
    (
        "c.a.j.util.index.DefaultCompositeIndexLifecycleManager",
        "Release cluster lock and allow others to run Index snapshot...",
        "index_snapshot",
        None,
    ),
    (
        "c.a.j.index.ha.IndexUtils",
        "Could not acquire cluster lock for index snapshot creation",
        "index_snapshot",
        None,
    ),
    (
        "c.a.j.util.compression.ArchiveUtils",
        "Creating archive of type: SNAPPY in path: <PATH>",
        "index_snapshot",
        None,
    ),
    (
        "c.a.t.j.healthcheck.cluster.ClusterReplicationHealthCheck",
        "Node NODEA does not seem to replicate its cache",
        "cache_replication",
        "NODEA",
    ),
    (
        "c.a.j.c.d.localq.rmi.CachingRMICachePeerManager",
        "[LOCALQ] Re-creating cache peer for node: NODEB and cacheName: org.acme.Cache, old",
        "cache_peer",
        "NODEB",
    ),
    (
        "c.a.t.healthcheck.concurrent.SupportHealthCheckProcess",
        "Health check 'Cluster Index Replication' failed with severity 'warning': 'behind'",
        "health_failed",
        "Cluster Index Replication",
    ),
    (
        "c.a.j.integritychecker.impl.IntegrityCheckerJobRunner",
        "Cluster lock acquired for IntegrityCheckerJobRunner",
        "cluster_lock",
        "IntegrityCheckerJobRunner",
    ),
    ("org.acme.Locks", "Could not acquire cluster lock", "cluster_lock", None),
    ("c.a.jira.startup.JiraStartupLogger", "", "start", None),
    (
        "c.a.jira.startup.LauncherContextListener",
        "Startup is complete. Jira is ready to serve.",
        "start",
        None,
    ),
    ("c.a.jira.startup.DefaultJiraLauncher", "Stopping launchers", "stop", None),
    (
        "org.acme.sync.GroupsSyncJobRunnerImpl",
        "group size: <NUM>",
        "job_run",
        "GroupsSyncJobRunnerImpl",
    ),
    (
        "c.a.j.m.tracking.polling.PollerJobScheduler",
        "PollerJobScheduler job started: jobId=acme-poller-job startTime=Mon Nov",
        "job_run",
        "acme-poller-job",
    ),
    (
        "org.acme.Dispatcher",
        "Event dispatching job started at <TS> jobId=acme-dispatch-job.",
        "job_run",
        "acme-dispatch-job",
    ),
    (
        "org.acme.Runner",
        "Running job - jobId: acme-stats-job - nodeId: NODEA",
        "job_run",
        "acme-stats-job",
    ),
    ("org.acme.Sync", "Sync is turned off, skipping job.", "job_run", "Sync"),
    ("org.acme.Scheduler", "Running nightly cleanup job", "job_run", "Scheduler"),
    ("org.acme.Web", "Request failed", None, None),
]


@pytest.mark.parametrize(("logger", "template", "kind", "subject"), JIRA_CASES)
def test_jira_rules(logger: str, template: str, kind: str | None, subject: str | None) -> None:
    found = classify(JIRA_EVENT_RULES, logger, template)
    if kind is None:
        assert found is None
        return
    assert found is not None
    rule, match = found
    assert rule.kind == kind
    assert rule.subject(logger, match, None) == subject
