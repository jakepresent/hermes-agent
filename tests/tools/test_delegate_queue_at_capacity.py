"""Fork: a full background pool QUEUES a model delegation instead of running it inline.

Running it inline held the parent's turn for the child's whole run, so the user could not talk to the parent
while it waited (a continue issued with five background units running blocked the chat for twelve minutes).
"""

from __future__ import annotations

import json
import threading
from concurrent.futures import Future

# registry_state is a fixture: importing it makes it available to this module's tests.
from tests.tools.test_delegate_capacity_interrupt import (
    _batch, _ControlledChild, _Parent, registry_state,
)
from tools import async_delegation
from tools.delegate_tool_dispatch import _dispatch_background


class _FinishingChild(_ControlledChild):
    """Runs until the test lets it finish, then completes normally."""

    def run_conversation(self, **_kwargs):
        self.started.set()
        try:
            assert self.allow_finish.wait(30), "test did not release the child"
            return {"final_response": "done", "completed": True, "api_calls": 1, "messages": []}
        finally:
            self.finished.set()


def _occupy(n, session_key="other-session"):
    """Fill *n* pool slots with blockers from another session; returns their release events."""
    releases = []
    for i in range(n):
        started, release = threading.Event(), threading.Event()

        def runner(started=started, release=release):
            started.set()
            assert release.wait(30)
            return {"status": "completed", "summary": "slot released"}

        handle = async_delegation.dispatch_async_delegation(
            goal=f"occupy slot {i}", context=None, toolsets=None, role="leaf", model="m",
            session_key=session_key, runner=runner, max_async_children=n,
        )
        assert handle["status"] == "dispatched" and not handle.get("queued")
        assert started.wait(30)
        releases.append(release)
    return releases


def _dispatch_in_thread(batch):
    """Run ``_dispatch_background`` off-thread so a regression that runs the call inline fails fast instead of
    hanging the test."""
    outcome = Future()

    def run():
        try:
            outcome.set_result(json.loads(_dispatch_background(batch)))
        except BaseException as exc:  # pragma: no cover - surfaced by outcome.result()
            outcome.set_exception(exc)

    threading.Thread(target=run, daemon=True).start()
    return outcome


def _completion_for(completions, delegation_id, timeout=30):
    while True:
        evt = completions.get(timeout=timeout)
        if evt["delegation_id"] == delegation_id:
            return evt


def test_full_pool_queues_the_call_and_returns_at_once(registry_state):
    completions = registry_state
    (release,) = _occupy(1)
    parent, child = _Parent(), _FinishingChild()
    try:
        result = _dispatch_in_thread(_batch(parent, child)).result(timeout=30)
        assert result["status"] == "dispatched" and result["queued"] is True
        assert "QUEUED" in result["note"]
        assert not child.started.is_set(), "a queued child must wait for a slot"
        listed = {item["delegation_id"]: item for item in async_delegation.list_async_delegations()}
        assert listed[result["delegation_id"]].get("queued") is True

        release.set()  # the running unit finishes: its slot goes to the queued call
        assert child.started.wait(30), "queued child never started after a slot freed"
        child.allow_finish.set()
        evt = _completion_for(completions, result["delegation_id"])
        assert evt["results"][0]["status"] == "completed"
        assert child.close_count == 1
        assert parent._active_children == []
    finally:
        release.set()
        child.allow_finish.set()


def test_queued_call_stopped_before_its_slot_opens_never_runs(registry_state):
    completions = registry_state
    (release,) = _occupy(1)
    parent, child = _Parent(), _FinishingChild()
    try:
        result = _dispatch_in_thread(_batch(parent, child)).result(timeout=30)
        assert result["queued"] is True
        # /stop, /new or session end reach background work through the registry, not the parent's turn.
        assert async_delegation.interrupt_for_session(parent_session_id=parent.session_id) == 1
        evt = _completion_for(completions, result["delegation_id"])
        entry = evt["results"][0]
        assert entry["status"] == "interrupted" and "before it started" in entry["error"]
        assert not child.started.is_set()
        assert child.close_count == 1
        assert parent._active_children == []
        # The unit that held the slot was not touched by the other session's stop.
        assert any(item.get("status") == "running" for item in async_delegation.list_async_delegations())
    finally:
        release.set()
        child.allow_finish.set()


def test_full_queue_refuses_without_running_anything(registry_state):
    completions = registry_state
    (release,) = _occupy(1)
    parent = _Parent()
    waiting, overflow = _FinishingChild(), _FinishingChild()
    try:
        queued = _dispatch_in_thread(_batch(parent, waiting)).result(timeout=30)
        assert queued["queued"] is True
        refused = _dispatch_in_thread(_batch(parent, overflow)).result(timeout=30)
        assert "Not started" in refused["error"] and "Nothing ran" in refused["error"]
        assert not overflow.started.is_set()
        assert overflow.close_count == 1
        release.set()
        assert waiting.started.wait(30)
        waiting.allow_finish.set()
        assert _completion_for(completions, queued["delegation_id"])["results"][0]["status"] == "completed"
        assert not overflow.started.is_set()
        assert parent._active_children == []
    finally:
        release.set()
        waiting.allow_finish.set()
        overflow.allow_finish.set()


def test_queued_units_start_first_come_first_served(registry_state):
    releases = _occupy(2)
    order, gates = [], {name: threading.Event() for name in ("first", "second")}
    seen = threading.Condition()

    def runner_for(name):
        def runner():
            with seen:
                order.append(name)
                seen.notify_all()
            assert gates[name].wait(30)
            return {"results": [], "total_duration_seconds": 0}
        return runner

    try:
        for name in ("first", "second"):
            handle = async_delegation.dispatch_async_delegation_batch(
                goals=[name], context=None, toolsets=None, role="leaf", model="m", session_key="queue-order",
                runner=runner_for(name), max_async_children=2, queue_when_full=True,
            )
            assert handle["status"] == "dispatched" and handle["queued"] is True
        releases[0].set()  # one slot frees: only the earlier queued unit may take it
        with seen:
            assert seen.wait_for(lambda: len(order) >= 1, timeout=30)
        assert order == ["first"]
        releases[1].set()
        with seen:
            assert seen.wait_for(lambda: len(order) >= 2, timeout=30)
        assert order == ["first", "second"]
    finally:
        for event in (*releases, *gates.values()):
            event.set()


def test_callers_without_queueing_are_still_rejected_at_capacity(registry_state):
    """Cron and other direct callers keep the original contract: a full pool rejects, nothing is queued."""
    (release,) = _occupy(1)
    try:
        handle = async_delegation.dispatch_async_delegation_batch(
            goals=["x"], context=None, toolsets=None, role="leaf", model="m", session_key="",
            runner=lambda: {"results": []}, max_async_children=1,
        )
        assert handle["status"] == "rejected" and handle["reason"] == "capacity"
    finally:
        release.set()
