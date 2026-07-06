"""Regression tests for the gateway process-level event-loop watchdog.

The Discord websocket watchdog runs on the gateway's asyncio loop, so it cannot
fire if the loop itself is wedged.  The process-level watchdog has an out-of-loop
thread that observes a tiny in-loop heartbeat and exits with the service-restart
code when the heartbeat goes stale.
"""

import asyncio
import os
import threading
import time

import pytest

from gateway.run import GATEWAY_SERVICE_RESTART_EXIT_CODE, GatewayRunner


class _ExitCalled(RuntimeError):
    def __init__(self, code: int):
        super().__init__(f"os._exit({code})")
        self.code = code


def _bare_runner() -> GatewayRunner:
    runner = object.__new__(GatewayRunner)
    runner._running = True
    runner._draining = False
    runner._event_loop_watchdog_stop_event = threading.Event()
    runner._event_loop_watchdog_last_tick = time.monotonic()
    runner._event_loop_watchdog_task = None
    runner._event_loop_watchdog_thread = None
    return runner


def test_event_loop_watchdog_fires_service_restart_when_heartbeat_stale(monkeypatch):
    runner = _bare_runner()
    runner._event_loop_watchdog_last_tick = time.monotonic() - 999.0

    wrote_forensics = {"called": False}

    def _write_forensics(*, stale_for, threshold_seconds):
        wrote_forensics["called"] = True
        assert stale_for >= threshold_seconds

    def _fake_exit(code):
        raise _ExitCalled(code)

    monkeypatch.setattr(runner, "_write_event_loop_watchdog_forensics", _write_forensics)
    monkeypatch.setattr(os, "_exit", _fake_exit)

    with pytest.raises(_ExitCalled) as exc_info:
        runner._event_loop_watchdog_thread_main(
            threshold_seconds=1.0,
            check_seconds=0.0,
        )

    assert exc_info.value.code == GATEWAY_SERVICE_RESTART_EXIT_CODE
    assert wrote_forensics["called"] is True


def test_event_loop_watchdog_does_not_fire_when_heartbeat_fresh(monkeypatch):
    runner = _bare_runner()
    runner._event_loop_watchdog_last_tick = time.monotonic()
    exits = []

    def _fake_exit(code):
        exits.append(code)
        raise _ExitCalled(code)

    monkeypatch.setattr(os, "_exit", _fake_exit)

    thread = threading.Thread(
        target=runner._event_loop_watchdog_thread_main,
        kwargs={"threshold_seconds": 3600.0, "check_seconds": 0.001},
    )
    thread.start()
    time.sleep(0.01)
    runner._event_loop_watchdog_stop_event.set()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert exits == []


def test_event_loop_watchdog_does_not_fire_while_draining(monkeypatch):
    runner = _bare_runner()
    runner._draining = True
    runner._event_loop_watchdog_last_tick = time.monotonic() - 999.0
    exits = []

    def _fake_exit(code):
        exits.append(code)
        raise _ExitCalled(code)

    monkeypatch.setattr(os, "_exit", _fake_exit)

    thread = threading.Thread(
        target=runner._event_loop_watchdog_thread_main,
        kwargs={"threshold_seconds": 1.0, "check_seconds": 0.001},
    )
    thread.start()
    time.sleep(0.01)
    runner._event_loop_watchdog_stop_event.set()
    thread.join(timeout=1.0)

    assert not thread.is_alive()
    assert exits == []


@pytest.mark.asyncio
async def test_event_loop_watchdog_heartbeat_updates_last_tick():
    runner = _bare_runner()
    before = runner._event_loop_watchdog_last_tick

    task = asyncio.create_task(runner._event_loop_watchdog_heartbeat(0.001))
    await asyncio.sleep(0.01)
    runner._event_loop_watchdog_stop_event.set()
    await asyncio.wait_for(task, timeout=1.0)

    assert runner._event_loop_watchdog_last_tick > before
