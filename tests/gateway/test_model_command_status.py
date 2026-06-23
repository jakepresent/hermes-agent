"""Regression tests for gateway /model status."""

from types import SimpleNamespace

import pytest
import yaml

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import GatewayRunner
from gateway.session import SessionSource


class _FakePickerAdapter:
    def __init__(self):
        self.called = False

    async def send_model_picker(self, **kwargs):
        self.called = True
        self.kwargs = kwargs
        return SimpleNamespace(success=True, message_id="picker-1")


def _make_runner(adapter=None):
    runner = object.__new__(GatewayRunner)
    runner.adapters = {Platform.DISCORD: adapter} if adapter is not None else {}
    runner._voice_mode = {}
    runner._session_model_overrides = {}
    runner._running_agents = {}
    return runner


def _make_event(text="/model status"):
    return MessageEvent(
        text=text,
        message_type=MessageType.TEXT,
        source=SessionSource(platform=Platform.DISCORD, chat_id="12345", chat_type="channel"),
    )


def _setup_home(tmp_path, monkeypatch, model_cfg=None):
    import gateway.run as gateway_run

    hermes_home = tmp_path / ".hermes"
    hermes_home.mkdir()
    (hermes_home / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "model": model_cfg
                or {
                    "default": "gpt-5.5",
                    "provider": "copilot",
                    "base_url": "https://api.githubcopilot.com",
                },
                "providers": {},
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_run, "_hermes_home", hermes_home)
    monkeypatch.setattr("agent.models_dev.fetch_models_dev", lambda: {})
    return hermes_home


@pytest.mark.asyncio
async def test_model_status_shows_current_model_without_opening_discord_picker(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    adapter = _FakePickerAdapter()

    result = await _make_runner(adapter)._handle_model_command(_make_event("/model status"))

    assert result is not None
    assert "Current:" in result
    assert "gpt-5.5" in result
    assert "github-copilot" in result
    assert "`/model`" in result
    assert "open model picker" in result
    assert adapter.called is False


@pytest.mark.asyncio
async def test_model_status_uses_session_override(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    runner = _make_runner()
    event = _make_event("/model current")
    session_key = runner._session_key_for_source(event.source)
    runner._session_model_overrides[session_key] = {
        "model": "claude-opus-4.8",
        "provider": "anthropic",
        "base_url": "https://api.anthropic.com",
        "api_key": "sk-test",
        "api_mode": "chat_completions",
    }

    result = await runner._handle_model_command(event)

    assert result is not None
    assert "claude-opus-4.8" in result
    assert "anthropic" in result
    assert "session only" in result


@pytest.mark.asyncio
async def test_bare_model_still_opens_discord_picker(tmp_path, monkeypatch):
    _setup_home(tmp_path, monkeypatch)
    adapter = _FakePickerAdapter()

    monkeypatch.setattr(
        "hermes_cli.model_switch.list_picker_providers",
        lambda **kwargs: [
            {
                "slug": "copilot",
                "name": "GitHub Copilot",
                "models": ["gpt-5.5"],
                "total_models": 1,
                "is_current": True,
            }
        ],
    )

    result = await _make_runner(adapter)._handle_model_command(_make_event("/model"))

    assert result is None
    assert adapter.called is True
    assert adapter.kwargs["current_model"] == "gpt-5.5"
    assert adapter.kwargs["current_provider"] == "copilot"
