"""Regression tests for session-scoped model/provider overrides in gateway agents.

These cover the bug where `/model ...` stored a session override, but fresh
agent constructions still resolved model/provider from global config/runtime.
That let helper agents (and cache-miss main agents) route GPT-5.4 to the wrong
provider, e.g. Nous instead of OpenAI Codex.
"""

import asyncio
import sys
import threading
import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import Platform
from gateway.session import SessionSource


class _CapturingAgent:
    """Fake agent that records init kwargs for assertions."""

    last_init = None

    def __init__(self, *args, **kwargs):
        type(self).last_init = dict(kwargs)
        self.tools = []

    def run_conversation(self, user_message: str, conversation_history=None, task_id=None):
        return {
            "final_response": "ok",
            "messages": [],
            "api_calls": 1,
        }


def _make_runner():
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.adapters = {}
    runner.session_store = MagicMock()
    runner.session_store.get_model_override.return_value = None
    runner.config = None
    runner._voice_mode = {}
    runner._ephemeral_system_prompt = ""
    runner._prefill_messages = []
    runner._reasoning_config = None
    runner._show_reasoning = False
    runner._provider_routing = {}
    runner._fallback_model = None
    runner._service_tier = None
    runner._running_agents = {}
    runner._running_agents_ts = {}
    runner._background_tasks = set()
    runner._session_db = None

    runner._session_model_overrides = {}
    runner._session_reasoning_overrides = {}
    runner._pending_model_notes = {}
    runner._pending_approvals = {}
    runner._agent_cache = {}
    runner._agent_cache_lock = threading.Lock()
    runner._get_or_create_gateway_honcho = lambda session_key: (None, None)
    runner.hooks = MagicMock()
    runner.hooks.emit = AsyncMock()
    runner.hooks.loaded_hooks = []
    return runner


def _codex_override():
    return {
        "model": "gpt-5.4",
        "provider": "openai-codex",
        "api_key": "***",
        "base_url": "https://chatgpt.com/backend-api/codex",
        "api_mode": "codex_responses",
    }


def _explode_runtime_resolution():
    raise AssertionError(
        "global runtime resolution should not run when a complete session override exists"
    )


def test_gateway_auth_fallback_uses_fallback_model_from_config(tmp_path, monkeypatch):
    """Regression: fallback provider must not inherit the primary model.

    If primary openai-codex auth fails and fallback_providers selects
    OpenRouter/minimax, the gateway must instantiate AIAgent with the fallback
    model, not the primary config model (e.g. gpt-5.5). Otherwise OpenRouter
    receives an unintended GPT request.
    """
    config = tmp_path / "config.yaml"
    config.write_text(
        """
model:
  default: gpt-5.5
  provider: openai-codex
fallback_providers:
  - provider: openrouter
    model: minimax/minimax-m2.7
""".lstrip(),
        encoding="utf-8",
    )
    monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)

    def fake_resolve_runtime_provider(*, requested=None, explicit_base_url=None, explicit_api_key=None):
        if requested in {None, "", "openai-codex"}:
            from hermes_cli.auth import AuthError
            raise AuthError("No Codex credentials stored. Run `hermes auth` to authenticate.")
        assert requested == "openrouter"
        return {
            "api_key": "sk-openrouter",
            "base_url": "https://openrouter.ai/api/v1",
            "provider": "openrouter",
            "api_mode": "chat_completions",
            "command": None,
            "args": [],
            "credential_pool": None,
        }

    import hermes_cli.runtime_provider as runtime_provider

    monkeypatch.setattr(runtime_provider, "resolve_runtime_provider", fake_resolve_runtime_provider)

    runner = _make_runner()
    model, runtime_kwargs = runner._resolve_session_agent_runtime(
        session_key="agent:main:telegram:group:-1003715515980:63",
        user_config={
            "model": {"default": "gpt-5.5", "provider": "openai-codex"},
            "fallback_providers": [{"provider": "openrouter", "model": "minimax/minimax-m2.7"}],
        },
    )

    assert model == "minimax/minimax-m2.7"
    assert runtime_kwargs["provider"] == "openrouter"
    assert runtime_kwargs["api_key"] == "sk-openrouter"


def test_first_message_router_pins_and_persists_selected_route(monkeypatch):
    runner = _make_runner()
    runner._session_db = AsyncMock()
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "message_router": {
                "enabled": True,
                "routes": {
                    "routine": {"provider": "openrouter", "model": "z-ai/glm-5.3-flash"},
                    "vision_research": {"provider": "openrouter", "model": "deepseek/vision"},
                    "coding": {"provider": "openrouter", "model": "anthropic/sonnet"},
                    "judgment": {"provider": "openrouter", "model": "anthropic/sonnet"},
                },
            }
        },
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs_for_provider",
        lambda provider, target_model=None: {
            "model": target_model,
            "provider": provider,
            "requested_provider": provider,
            "api_key": "secret",
            "base_url": "https://example.test/v1",
            "api_mode": "chat_completions",
        },
    )
    source = SessionSource(platform=Platform.DISCORD, user_id="u", chat_id="c")
    entry = SimpleNamespace(session_key="agent:main:discord:c", session_id="session-1")
    event = SimpleNamespace(
        text="Should I sell some MSFT now?",
        internal=False,
        media_types=[],
        is_command=lambda: False,
    )

    asyncio.run(
        runner._apply_first_message_model_route(event=event, source=source, session_entry=entry)
    )

    override = runner._session_model_overrides[entry.session_key]
    assert override["model"] == "anthropic/sonnet"
    assert override["provider"] == "openrouter"
    runner._session_db.update_session_model.assert_awaited_once_with(
        "session-1", "anthropic/sonnet", provider="openrouter"
    )
    runner.session_store.set_model_override.assert_called_once()
    persisted = runner.session_store.set_model_override.call_args.args[1]
    assert persisted["model"] == "anthropic/sonnet"
    runner.session_store.set_session_metadata.assert_called_with(
        entry.session_key, "message_router_state", "routed"
    )


def test_first_message_router_is_disabled_by_default(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr(gateway_run, "_load_gateway_config", lambda: {})
    source = SessionSource(platform=Platform.DISCORD, user_id="u", chat_id="c")
    entry = SimpleNamespace(session_key="agent:main:discord:c", session_id="session-1")
    event = SimpleNamespace(
        text="Should I sell MSFT?",
        internal=False,
        media_types=[],
        is_command=lambda: False,
    )

    asyncio.run(
        runner._apply_first_message_model_route(event=event, source=source, session_entry=entry)
    )

    assert entry.session_key not in runner._session_model_overrides
    runner.session_store.set_session_metadata.assert_called_once_with(
        entry.session_key, "message_router_state", "disabled"
    )


def test_first_message_router_detects_mime_less_photo(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "message_router": {
                "enabled": True,
                "routes": {
                    "routine": {"provider": "openrouter", "model": "glm"},
                    "vision_research": {"provider": "openrouter", "model": "deepseek-vision"},
                },
            }
        },
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs_for_provider",
        lambda provider, target_model=None: {"provider": provider},
    )
    source = SessionSource(platform=Platform.DISCORD, user_id="u", chat_id="c")
    entry = SimpleNamespace(session_key="agent:main:discord:c", session_id="session-photo")
    event = SimpleNamespace(
        text="What is this?",
        internal=False,
        media_types=[],
        media_urls=["/tmp/photo.bin"],
        message_type=SimpleNamespace(value="photo"),
        is_command=lambda: False,
    )

    asyncio.run(
        runner._apply_first_message_model_route(event=event, source=source, session_entry=entry)
    )

    assert runner._session_model_overrides[entry.session_key]["model"] == "deepseek-vision"


def test_routed_override_rehydrates_in_a_new_runner(monkeypatch):
    class Store:
        def __init__(self):
            self.override = None
            self.metadata = {}

        def set_model_override(self, session_key, override):
            self.override = {
                key: value
                for key, value in override.items()
                if key in {"model", "provider", "base_url"}
            }

        def set_session_metadata(self, session_key, key, value):
            self.metadata[(session_key, key)] = value

        def get_model_override(self, session_key):
            return dict(self.override) if self.override else None

    store = Store()
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {
            "message_router": {
                "enabled": True,
                "routes": {
                    "judgment": {"provider": "openrouter", "model": "anthropic/sonnet"}
                },
            }
        },
    )
    monkeypatch.setattr(
        gateway_run,
        "_resolve_runtime_agent_kwargs_for_provider",
        lambda provider, target_model=None: {
            "provider": provider,
            "api_key": "fresh-secret",
            "base_url": "https://example.test/v1",
        },
    )
    source = SessionSource(platform=Platform.DISCORD, user_id="u", chat_id="c")
    entry = SimpleNamespace(session_key="agent:main:discord:c", session_id="session-restart")
    event = SimpleNamespace(
        text="Should I sell some MSFT now?",
        internal=False,
        media_types=[],
        is_command=lambda: False,
    )
    before = _make_runner()
    before.session_store = store
    asyncio.run(
        before._apply_first_message_model_route(event=event, source=source, session_entry=entry)
    )

    after = _make_runner()
    after.session_store = store
    after._rehydrate_session_model_override(entry.session_key)

    restored = after._session_model_overrides[entry.session_key]
    assert restored["model"] == "anthropic/sonnet"
    assert restored["provider"] == "openrouter"
    assert restored["api_key"] == "fresh-secret"


def test_internal_event_does_not_consume_pending_router_state(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"message_router": {"enabled": True, "routes": {}}},
    )
    source = SessionSource(platform=Platform.DISCORD, user_id="u", chat_id="c")
    entry = SimpleNamespace(
        session_key="agent:main:discord:c",
        session_id="session-internal",
        metadata={"message_router_state": "pending"},
    )
    event = SimpleNamespace(text="wake", internal=True, is_command=lambda: False)

    asyncio.run(
        runner._apply_first_message_model_route(event=event, source=source, session_entry=entry)
    )

    runner.session_store.set_session_metadata.assert_not_called()
    assert entry.metadata["message_router_state"] == "pending"


def test_command_does_not_consume_pending_router_state(monkeypatch):
    runner = _make_runner()
    monkeypatch.setattr(
        gateway_run,
        "_load_gateway_config",
        lambda: {"message_router": {"enabled": True, "routes": {}}},
    )
    source = SessionSource(platform=Platform.DISCORD, user_id="u", chat_id="c")
    entry = SimpleNamespace(
        session_key="agent:main:discord:c",
        session_id="session-command",
        metadata={"message_router_state": "pending"},
    )
    event = SimpleNamespace(text="/status", internal=False, is_command=lambda: True)

    asyncio.run(
        runner._apply_first_message_model_route(event=event, source=source, session_entry=entry)
    )

    runner.session_store.set_session_metadata.assert_not_called()
    assert entry.metadata["message_router_state"] == "pending"


