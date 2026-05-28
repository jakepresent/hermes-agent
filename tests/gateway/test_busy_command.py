"""Tests for the gateway /busy command."""

from unittest.mock import MagicMock

import pytest
import yaml

from gateway.platforms.base import MessageEvent, MessageType, SessionSource


def _make_event(text: str) -> MessageEvent:
    source = SessionSource(
        platform=MagicMock(value="discord"),
        chat_id="chat1",
        chat_type="group",
        user_id="user1",
    )
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=source,
        message_id="msg1",
    )


def _make_runner():
    from gateway.run import GatewayRunner

    runner = object.__new__(GatewayRunner)
    runner._busy_input_mode = "interrupt"
    runner._busy_ack_ts = {"discord:chat1": 123.0}
    return runner


@pytest.mark.asyncio
async def test_busy_is_gateway_known_command():
    from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS

    assert "busy" in GATEWAY_KNOWN_COMMANDS


@pytest.mark.asyncio
async def test_busy_status_reports_current_mode(tmp_path, monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    runner = _make_runner()
    runner._busy_input_mode = "queue"

    result = await runner._handle_busy_command(_make_event("/busy status"))

    assert "Busy input mode: `queue`" in result
    assert "/busy interrupt" in result


@pytest.mark.asyncio
async def test_busy_queue_sets_runtime_and_persists(tmp_path, monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    (tmp_path / "config.yaml").write_text("display:\n  skin: default\n", encoding="utf-8")
    runner = _make_runner()

    result = await runner._handle_busy_command(_make_event("/busy queue"))

    assert "set to `queue`" in result
    assert runner._busy_input_mode == "queue"
    assert runner._busy_ack_ts == {}
    cfg = yaml.safe_load((tmp_path / "config.yaml").read_text())
    assert cfg["display"]["busy_input_mode"] == "queue"
    assert cfg["display"]["skin"] == "default"


@pytest.mark.asyncio
async def test_busy_unknown_arg_does_not_change_mode(tmp_path, monkeypatch):
    import gateway.run as gateway_run

    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    runner = _make_runner()

    result = await runner._handle_busy_command(_make_event("/busy nope"))

    assert "Unknown busy mode" in result
    assert runner._busy_input_mode == "interrupt"
