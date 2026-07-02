"""Regression tests for the Discord gateway liveness watchdog.

Class-level bug this guards against: discord.py reconnects ordinary gateway
interruptions internally, and its ``Bot.start()`` task only *exits* on a hard
failure (which fires ``_handle_bot_task_done``). Neither path covers a
websocket that is silently dead — half-open TCP after a host network/interop
blip, or an internal reconnect loop that never re-establishes — while the task
stays alive. That leaves the process running, the systemd service "active", and
the bot offline, with no exception anywhere to trigger recovery ("green
service, dead bot"). The watchdog detects that state via discord.py's public
``is_closed()`` / ``latency`` signals and routes it through the SAME
retryable-fatal path a task-exit would, so the gateway's existing reconnect
watcher takes over instead of a human having to restart.

The tests drive ``_liveness_watchdog_loop`` directly with the probe interval
monkeypatched to ~0 so they never sleep on real time.
"""

import asyncio
import math
import sys
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from gateway.config import PlatformConfig


def _ensure_discord_mock():
    """Install a minimal mock ``discord`` module if one isn't present.

    Mirrors the stub in test_discord_connect.py so this module can run in
    isolation as well as in the full suite.
    """
    if sys.modules.get("discord") is None:
        discord_mod = MagicMock()
        discord_mod.Intents.default.return_value = MagicMock()
        discord_mod.Client = MagicMock
        discord_mod.File = MagicMock
        discord_mod.DMChannel = type("DMChannel", (), {})
        discord_mod.Thread = type("Thread", (), {})
        discord_mod.ForumChannel = type("ForumChannel", (), {})
        discord_mod.opus = SimpleNamespace(is_loaded=lambda: True)
        ext_mod = MagicMock()
        commands_mod = MagicMock()
        commands_mod.Bot = MagicMock
        ext_mod.commands = commands_mod
        sys.modules["discord"] = discord_mod
        sys.modules.setdefault("discord.ext", ext_mod)
        sys.modules.setdefault("discord.ext.commands", commands_mod)

    if not hasattr(sys.modules["discord"], "AllowedMentions"):
        sys.modules["discord"].AllowedMentions = MagicMock


_ensure_discord_mock()

import plugins.platforms.discord.adapter as discord_platform  # noqa: E402
from plugins.platforms.discord.adapter import DiscordAdapter  # noqa: E402


class _FakeClient:
    """Stand-in for a discord.py ``Client`` exposing the two public liveness
    signals the watchdog reads: ``is_closed()`` and ``latency``.
    """

    def __init__(self, *, closed: bool = False, latency: float = 0.05):
        self._closed = closed
        self._latency = latency

    def is_closed(self) -> bool:
        return self._closed

    @property
    def latency(self) -> float:
        return self._latency


def _make_adapter(monkeypatch, *, probe_interval=0.0, threshold=1.0):
    """Build a DiscordAdapter wired so the watchdog loop iterates instantly."""
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))
    monkeypatch.setattr(
        discord_platform, "_DISCORD_LIVENESS_PROBE_INTERVAL_SECONDS", probe_interval
    )
    monkeypatch.setattr(
        discord_platform, "_DISCORD_LIVENESS_STALE_THRESHOLD_SECONDS", threshold
    )
    # Give the watchdog the invariants it checks: running, not disconnecting,
    # and a live (not-done) bot task.
    adapter._running = True
    adapter._disconnecting = False
    adapter._bot_task = SimpleNamespace(done=lambda: False)
    return adapter


@pytest.mark.asyncio
async def test_healthy_gateway_never_fires_fatal_error(monkeypatch):
    """A live websocket (not closed, finite latency) must never be reported as
    wedged, no matter how many probe cycles run."""
    adapter = _make_adapter(monkeypatch, threshold=0.001)
    adapter._client = _FakeClient(closed=False, latency=0.05)

    notified = AsyncMock()
    monkeypatch.setattr(adapter, "_notify_fatal_error", notified)

    # Run several probe cycles, then cancel — the loop only exits on wedge or
    # cancellation, so we cancel it ourselves after letting it iterate.
    task = asyncio.create_task(adapter._liveness_watchdog_loop())
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not adapter.has_fatal_error, "healthy gateway must not be marked fatal"
    notified.assert_not_awaited()


@pytest.mark.asyncio
async def test_wedged_gateway_fires_retryable_fatal_error(monkeypatch):
    """A silently-dead websocket (closed, or infinite latency) held past the
    stale threshold must fire a RETRYABLE fatal error and notify the supervisor
    exactly once, then the loop exits."""
    adapter = _make_adapter(monkeypatch, threshold=1.0)
    # Dead ws: discord.py reports latency == inf when there is no heartbeat.
    adapter._client = _FakeClient(closed=False, latency=math.inf)

    notified = AsyncMock()
    monkeypatch.setattr(adapter, "_notify_fatal_error", notified)

    # Seed the last-live timestamp well in the past so the very first probe is
    # already past the stale threshold.
    import time as _time
    adapter._last_liveness_ok = _time.monotonic() - 999.0

    # The loop returns after firing — bounded wait guards against a hang.
    await asyncio.wait_for(adapter._liveness_watchdog_loop(), timeout=2.0)

    assert adapter.has_fatal_error, "wedged gateway must be marked fatal"
    assert adapter.fatal_error_code == "discord_gateway_wedged"
    assert adapter.fatal_error_retryable is True, (
        "wedge must be RETRYABLE so the gateway reconnect watcher re-queues it"
    )
    notified.assert_awaited_once()


@pytest.mark.asyncio
async def test_closed_websocket_is_treated_as_non_live(monkeypatch):
    """``is_closed() == True`` must count as non-live even if a stale latency
    value lingers on the object."""
    adapter = _make_adapter(monkeypatch, threshold=1.0)
    # Closed socket but a leftover finite latency — closed must win.
    adapter._client = _FakeClient(closed=True, latency=0.05)

    notified = AsyncMock()
    monkeypatch.setattr(adapter, "_notify_fatal_error", notified)

    import time as _time
    adapter._last_liveness_ok = _time.monotonic() - 999.0

    await asyncio.wait_for(adapter._liveness_watchdog_loop(), timeout=2.0)

    assert adapter.has_fatal_error
    assert adapter.fatal_error_code == "discord_gateway_wedged"
    notified.assert_awaited_once()


@pytest.mark.asyncio
async def test_non_live_within_grace_window_does_not_fire(monkeypatch):
    """A gateway that is momentarily non-live but WITHIN the stale threshold
    (a routine RESUME / single missed heartbeat) must not be reported."""
    adapter = _make_adapter(monkeypatch, threshold=3600.0)
    adapter._client = _FakeClient(closed=False, latency=math.inf)

    notified = AsyncMock()
    monkeypatch.setattr(adapter, "_notify_fatal_error", notified)

    # last_liveness_ok is "now" → stale_for is ~0, far below the 1h threshold.
    import time as _time
    adapter._last_liveness_ok = _time.monotonic()

    task = asyncio.create_task(adapter._liveness_watchdog_loop())
    for _ in range(5):
        await asyncio.sleep(0)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass

    assert not adapter.has_fatal_error, (
        "a brief non-live blip within the grace window must not trip the watchdog"
    )
    notified.assert_not_awaited()


@pytest.mark.asyncio
async def test_watchdog_exits_when_bot_task_already_done(monkeypatch):
    """If the bot task has exited, ``_handle_bot_task_done`` already owns
    recovery — the watchdog must defer and not double-report."""
    adapter = _make_adapter(monkeypatch, threshold=0.001)
    adapter._client = _FakeClient(closed=True, latency=math.inf)
    adapter._bot_task = SimpleNamespace(done=lambda: True)  # already exited

    notified = AsyncMock()
    monkeypatch.setattr(adapter, "_notify_fatal_error", notified)

    await asyncio.wait_for(adapter._liveness_watchdog_loop(), timeout=2.0)

    assert not adapter.has_fatal_error, (
        "a done bot task is handled by _handle_bot_task_done; watchdog must defer"
    )
    notified.assert_not_awaited()


@pytest.mark.asyncio
async def test_disconnecting_flag_stops_watchdog(monkeypatch):
    """During an intentional disconnect the watchdog must exit without firing,
    even if the client looks dead."""
    adapter = _make_adapter(monkeypatch, threshold=0.001)
    adapter._client = _FakeClient(closed=True, latency=math.inf)
    adapter._disconnecting = True

    notified = AsyncMock()
    monkeypatch.setattr(adapter, "_notify_fatal_error", notified)

    await asyncio.wait_for(adapter._liveness_watchdog_loop(), timeout=2.0)

    assert not adapter.has_fatal_error
    notified.assert_not_awaited()


def test_gateway_is_live_reads_public_signals(monkeypatch):
    """_gateway_is_live must use only is_closed()/latency and classify correctly."""
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="test-token"))

    adapter._client = None
    assert adapter._gateway_is_live() is False, "no client → not live"

    adapter._client = _FakeClient(closed=False, latency=0.05)
    assert adapter._gateway_is_live() is True, "open + finite latency → live"

    adapter._client = _FakeClient(closed=True, latency=0.05)
    assert adapter._gateway_is_live() is False, "closed → not live"

    adapter._client = _FakeClient(closed=False, latency=math.inf)
    assert adapter._gateway_is_live() is False, "inf latency (no heartbeat) → not live"

    adapter._client = _FakeClient(closed=False, latency=math.nan)
    assert adapter._gateway_is_live() is False, "NaN latency → not live"
