"""Tests for gateway /busy command dispatch and persistence."""

from unittest.mock import MagicMock

import pytest
import yaml

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.platforms.base import MessageEvent
from gateway.session import SessionSource


def _make_runner(busy_mode="interrupt"):
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.session_store = None
    runner.config = None
    runner._busy_input_mode = busy_mode
    runner._busy_text_mode = "interrupt"
    runner._busy_ack_ts = {"telegram:chat-test": 123.0}
    return runner


def _make_event(text: str, chat_id: str = "chat-test") -> MessageEvent:
    return MessageEvent(text=text, source=SessionSource(
        platform=Platform.TELEGRAM, user_id=f"user-{chat_id}", chat_id=chat_id,
        user_name="tester", chat_type="dm"))


@pytest.mark.asyncio
async def test_busy_is_gateway_known_command():
    from hermes_cli.commands import GATEWAY_KNOWN_COMMANDS
    assert "busy" in GATEWAY_KNOWN_COMMANDS


@pytest.mark.asyncio
async def test_busy_status_reports_current_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    runner = _make_runner("queue")
    result = await runner._handle_busy_command(_make_event("/busy status"))
    assert "Busy input mode: `queue`" in str(result)
    assert "/busy interrupt" in str(result)


@pytest.mark.asyncio
async def test_busy_queue_sets_runtime_and_persists(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    saved = []
    monkeypatch.setattr("cli.save_config_value", lambda key, value: saved.append((key, value)) or True)
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_runtime_config",
        lambda: {"display": {"busy_input_mode": "queue"}},
    )
    runner = _make_runner()
    result = await runner._handle_busy_command(_make_event("/busy queue"))
    assert "queue" in str(result).lower()
    assert runner._busy_input_mode == "queue"
    assert saved == [("display.busy_input_mode", "queue")]


@pytest.mark.asyncio
async def test_busy_unknown_arg_does_not_change_mode(tmp_path, monkeypatch):
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
    runner = _make_runner()
    result = await runner._handle_busy_command(_make_event("/busy nope"))
    assert "unknown" in str(result).lower()
    assert runner._busy_input_mode == "interrupt"


class TestBusyCommand:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("command", "busy_mode"), [("/busy status", "queue"), ("/busy", "steer")])
    async def test_status_returns_current_mode(self, command, busy_mode):
        result = await _make_runner(busy_mode)._handle_busy_command(_make_event(command))
        assert busy_mode in str(result).lower() and "busy" in str(result).lower()

    @pytest.mark.asyncio
    async def test_busy_invalid_arg(self):
        result = await _make_runner()._handle_busy_command(_make_event("/busy bananas"))
        assert "unknown" in str(result).lower()


class TestBusyCommandPersistence:
    @pytest.mark.asyncio
    @pytest.mark.parametrize(("initial_mode", "new_mode"), [("interrupt", "queue"), ("queue", "steer"), ("queue", "interrupt")])
    async def test_set_mode_persists(self, monkeypatch, initial_mode, new_mode):
        runner = _make_runner(initial_mode)
        monkeypatch.setattr("cli.save_config_value", lambda k, v: True)
        monkeypatch.setattr(gateway_run, "_load_gateway_runtime_config", lambda: {"display": {"busy_input_mode": new_mode}})
        monkeypatch.delenv("HERMES_GATEWAY_BUSY_TEXT_MODE", raising=False)
        monkeypatch.delenv("HERMES_GATEWAY_BUSY_INPUT_MODE", raising=False)
        result = await runner._handle_busy_command(_make_event(f"/busy {new_mode}"))
        assert new_mode in str(result).lower()
        assert runner._busy_input_mode == new_mode
        assert runner._busy_text_mode == ("queue" if new_mode == "queue" else "interrupt")

    @pytest.mark.asyncio
    async def test_save_failure_preserves_mode(self, monkeypatch):
        runner = _make_runner("steer")
        monkeypatch.setattr("cli.save_config_value", lambda k, v: False)
        result = await runner._handle_busy_command(_make_event("/busy queue"))
        assert "unchanged" in str(result).lower()
        assert runner._busy_input_mode == "steer"
