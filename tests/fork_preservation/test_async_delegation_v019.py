# Fork-preservation residue extracted during the v2026.8.16 integration.
# These tests were present at fork head 896a5ea3b2 but were removed or
# reorganized upstream; keep them as behavior gates for retained features.
"""Tests for async (background) delegation — tools/async_delegation.py.

Covers the dispatch handle, non-blocking behavior, completion-event delivery
onto the shared process_registry.completion_queue, the rich re-injection block
formatting, capacity rejection, and crash handling.
"""

import json
import os
import queue
import subprocess
import sys
import threading
import time

import pytest

from tools import async_delegation as ad
from tools.process_registry import process_registry, format_process_notification


@pytest.fixture(autouse=True)
def _clean_state():
    ad._reset_for_tests()
    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()
    yield
    # Give just-released workers a beat to finalize BEFORE draining, so their
    # completion events land now instead of leaking into the next test's
    # queue (worker threads push events asynchronously; a drain that races an
    # in-flight _finalize misses it).
    deadline = time.monotonic() + 2.0
    while ad.active_count() and time.monotonic() < deadline:
        time.sleep(0.02)
    ad._reset_for_tests()
    while not process_registry.completion_queue.empty():
        process_registry.completion_queue.get_nowait()


def _drain_one(timeout=5.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_registry.completion_queue.empty():
            return process_registry.completion_queue.get_nowait()
        time.sleep(0.02)
    return None


def _drain_for(delegation_id, timeout=5.0):
    """Drain until the event for *delegation_id* appears (discarding others).

    Completion events are pushed asynchronously by worker threads, so a
    straggler from a PREVIOUS test can land after that test's teardown drain
    and leak into the current test's queue. Matching on delegation_id makes
    the assertion immune to that cross-test leak.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if not process_registry.completion_queue.empty():
            evt = process_registry.completion_queue.get_nowait()
            if evt.get("delegation_id") == delegation_id:
                return evt
            continue
        time.sleep(0.02)
    return None














def test_completed_records_pruned_to_cap():
    # Run more than the retention cap quickly; ensure list doesn't grow forever.
    for i in range(ad._MAX_RETAINED_COMPLETED + 10):
        ad.dispatch_async_delegation(
            goal=f"t{i}", context=None, toolsets=None, role="leaf", model="m",
            session_key="", runner=lambda: {"status": "completed", "summary": "ok"},
            max_async_children=ad._MAX_RETAINED_COMPLETED + 20,
        )
    # let workers finish
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline and ad.active_count() > 0:
        time.sleep(0.05)
    assert len(ad.list_async_delegations()) <= ad._MAX_RETAINED_COMPLETED


def test_completion_is_persisted_and_delivery_can_be_acknowledged(tmp_path, monkeypatch):
    """A finished child remains pending on disk until its queue consumer acks it."""
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    dispatched = ad.dispatch_async_delegation(
        goal="durable", context="ctx", toolsets=["terminal"], role="leaf",
        model="m", session_key="owner", parent_session_id="parent",
        runner=lambda: {"status": "completed", "summary": "survived"},
    )
    assert _drain_one() is not None

    restored = queue.Queue()
    assert ad.restore_undelivered_completions(restored) == 1
    row = ad.get_durable_delegation(dispatched["delegation_id"])
    assert row["origin_session"] == "owner"
    assert row["state"] == "completed"
    assert row["result"]["summary"] == "survived"
    assert row["delivery_state"] == "pending"
    # Queue publication/restoration is not a destination delivery attempt.
    assert row["delivery_attempts"] == 0

    assert ad.mark_completion_delivered(dispatched["delegation_id"])
    assert ad.restore_undelivered_completions(queue.Queue()) == 0
    assert ad.get_durable_delegation(dispatched["delegation_id"])["delivery_state"] == "delivered"




def test_submit_failure_removes_durable_running_record(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))

    class _BrokenExecutor:
        def submit(self, *_args, **_kwargs):
            raise RuntimeError("submit failed")

    monkeypatch.setattr(ad, "_get_executor", lambda _max_workers: _BrokenExecutor())
    result = ad.dispatch_async_delegation(
        goal="never ran", context=None, toolsets=None, role="leaf", model="m",
        session_key="owner", runner=lambda: {},
    )

    assert result["status"] == "rejected"
    with ad._DB_LOCK, ad._connect() as conn:
        assert conn.execute("SELECT COUNT(*) FROM async_delegations").fetchone()[0] == 0


def test_pending_retention_prunes_delivered_before_undelivered(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.setattr(ad, "_MAX_RETAINED_COMPLETED", 2)
    for index, delivery_state in enumerate(("pending", "delivered", "pending")):
        delegation_id = f"deleg_{index}"
        record = {
            "delegation_id": delegation_id,
            "session_key": "owner",
            "origin_ui_session_id": "",
            "parent_session_id": None,
            "dispatched_at": float(index + 1),
        }
        ad._persist_dispatch(record)
        ad._persist_completion(
            {
                "delegation_id": delegation_id,
                "status": "completed",
                "completed_at": float(index + 1),
            },
            {"status": "completed", "summary": delegation_id},
        )
        if delivery_state == "delivered":
            ad.mark_completion_delivered(delegation_id)

    ad._prune_durable_records()

    assert ad.get_durable_delegation("deleg_0") is not None
    assert ad.get_durable_delegation("deleg_1") is None
    assert ad.get_durable_delegation("deleg_2") is not None


def test_recover_marks_abandoned_running_record_unknown(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    record = {
        "delegation_id": "deleg_abandoned",
        "session_key": "owner",
        "origin_ui_session_id": "",
        "parent_session_id": None,
        "dispatched_at": 1.0,
    }
    ad._persist_dispatch(record)
    with ad._DB_LOCK, ad._connect() as conn:
        conn.execute(
            "UPDATE async_delegations SET owner_pid=?, owner_started_at=NULL WHERE delegation_id=?",
            (99999999, "deleg_abandoned"),
        )

    assert ad.recover_abandoned_delegations() == 1
    durable = ad.get_durable_delegation("deleg_abandoned")
    assert durable["state"] == "unknown"
    assert durable["delivery_state"] == "pending"
    restored = queue.Queue()
    assert ad.restore_undelivered_completions(restored) == 1
    assert restored.get_nowait()["status"] == "unknown"


def test_durable_delivery_claim_is_exclusive_and_retryable(tmp_path, monkeypatch):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    record = {
        "delegation_id": "deleg_claim", "session_key": "owner",
        "origin_ui_session_id": "", "parent_session_id": None,
        "dispatched_at": 1.0,
    }
    ad._persist_dispatch(record)
    ad._persist_completion(
        {"delegation_id": "deleg_claim", "status": "completed", "completed_at": 2.0},
        {"status": "completed", "summary": "done"},
    )

    assert ad.claim_completion_delivery("deleg_claim", "consumer-a")
    assert not ad.claim_completion_delivery("deleg_claim", "consumer-b")
    assert ad.release_completion_delivery("deleg_claim", "consumer-a")
    assert ad.claim_completion_delivery("deleg_claim", "consumer-b")
    assert ad.complete_completion_delivery("deleg_claim", "consumer-b")
    assert not ad.claim_completion_delivery("deleg_claim", "consumer-c")
    assert ad.get_durable_delegation("deleg_claim")["delivery_state"] == "delivered"


# ---------------------------------------------------------------------------
# Integration: delegate_task(background=True) routing
# ---------------------------------------------------------------------------

























# ---------------------------------------------------------------------------
# Gateway routing: session_key -> platform/chat_id, rich formatting, injection
# ---------------------------------------------------------------------------

def _make_async_evt(**over):
    evt = {
        "type": "async_delegation",
        "delegation_id": "deleg_x1",
        "session_key": "agent:main:telegram:dm:12345:678",
        "goal": "Investigate flaky test",
        "context": "repo /tmp/p",
        "toolsets": ["terminal"],
        "role": "leaf",
        "model": "m",
        "status": "completed",
        "summary": "Found the bug in test_foo",
        "api_calls": 4,
        "duration_seconds": 12.0,
        "dispatched_at": 1000.0,
        "completed_at": 1012.0,
    }
    evt.update(over)
    return evt


def test_gateway_enriches_routing_from_session_key():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    evt = _make_async_evt()
    runner._enrich_async_delegation_routing(evt)
    assert evt["platform"] == "telegram"
    assert evt["chat_id"] == "12345"
    assert evt["thread_id"] == "678"




def test_gateway_watch_drain_requeues_async_without_looping():
    from gateway.run import _drain_gateway_watch_events

    q = queue.Queue()
    async_evt = _make_async_evt()
    watch_evt = {
        "type": "watch_match",
        "session_id": "proc_1",
        "command": "pytest",
        "pattern": "READY",
        "output": "READY",
    }
    q.put(async_evt)
    q.put(watch_evt)

    watch_events = _drain_gateway_watch_events(q)

    assert watch_events == [watch_evt]
    assert q.qsize() == 1
    assert q.get_nowait() == async_evt


def test_gateway_builds_routable_source_from_enriched_event():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    evt = _make_async_evt()
    runner._enrich_async_delegation_routing(evt)
    src = runner._build_process_event_source(evt)
    assert src is not None
    assert src.platform.value == "telegram"
    assert src.chat_id == "12345"
