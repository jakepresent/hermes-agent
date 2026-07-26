"""Fork regression tests for the complementary Discord REST liveness probe.

Upstream v2026.7.20 owns the WebSocket ACK/open-state watchdog.  The fork keeps
an independent REST probe because Gateway and REST are separate transports: a
healthy WebSocket does not prove that message-delivery REST calls still work.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from tests.gateway.test_discord_connect import _ensure_discord_mock

_ensure_discord_mock()

from gateway.config import PlatformConfig  # noqa: E402
from plugins.platforms.discord.adapter import DiscordAdapter  # noqa: E402


def _adapter(*, interval: float = 0.0, threshold: int = 2) -> DiscordAdapter:
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={
                "rest_liveness_interval_seconds": 60.0,
                "rest_liveness_failure_threshold": threshold,
            },
        )
    )
    # Drive the loop without wall-clock waits.
    adapter._rest_liveness_interval_seconds = interval
    adapter._running = True
    adapter._disconnecting = False
    return adapter


def test_rest_probe_has_independent_config_and_task():
    adapter = DiscordAdapter(
        PlatformConfig(
            enabled=True,
            token="test-token",
            extra={
                "websocket_liveness_interval_seconds": 15,
                "rest_liveness_interval_seconds": 45,
                "rest_liveness_failure_threshold": 4,
            },
        )
    )

    assert adapter._liveness_interval_seconds == 15
    assert adapter._rest_liveness_interval_seconds == 45
    assert adapter._rest_liveness_failure_threshold == 4
    assert adapter._liveness_task is None
    assert adapter._rest_liveness_task is None


@pytest.mark.asyncio
async def test_healthy_rest_probe_does_not_trip_websocket_watchdog_state():
    adapter = _adapter(interval=0.0, threshold=2)
    client = SimpleNamespace(
        user=SimpleNamespace(id=42),
        is_closed=lambda: False,
        fetch_user=AsyncMock(return_value=SimpleNamespace(id=42)),
    )
    adapter._client = client

    task = asyncio.create_task(adapter._rest_liveness_loop())
    for _ in range(5):
        await asyncio.sleep(0)
    adapter._running = False
    await asyncio.wait_for(task, timeout=1.0)

    assert client.fetch_user.await_count >= 1
    assert adapter.has_fatal_error is False
    assert adapter._liveness_task is None


@pytest.mark.asyncio
async def test_repeated_rest_failures_trigger_retryable_fatal_reconnect(monkeypatch):
    adapter = _adapter(interval=0.0, threshold=2)
    client = SimpleNamespace(
        user=SimpleNamespace(id=42),
        ws=None,
        _closing_task=None,
        is_closed=lambda: False,
        fetch_user=AsyncMock(side_effect=RuntimeError("REST unavailable")),
        close=AsyncMock(),
    )
    adapter._client = client
    notified = AsyncMock()
    monkeypatch.setattr(adapter, "_notify_fatal_error", notified)

    await asyncio.wait_for(adapter._rest_liveness_loop(), timeout=1.0)
    if adapter._liveness_notification_task is not None:
        await asyncio.wait_for(adapter._liveness_notification_task, timeout=1.0)

    assert client.fetch_user.await_count == 2
    assert adapter.has_fatal_error is True
    assert adapter.fatal_error_code == "discord_rest_health_stale"
    assert adapter.fatal_error_retryable is True
    notified.assert_awaited_once()


@pytest.mark.asyncio
async def test_cancel_rest_probe_does_not_cancel_websocket_probe():
    adapter = _adapter(interval=60.0)
    adapter._start_rest_liveness_probe()
    rest_task = adapter._rest_liveness_task
    websocket_task = asyncio.create_task(asyncio.sleep(60))
    adapter._liveness_task = websocket_task

    await adapter._cancel_rest_liveness_task()

    assert rest_task is not None and rest_task.done()
    assert adapter._rest_liveness_task is None
    assert adapter._liveness_task is websocket_task
    websocket_task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await websocket_task
