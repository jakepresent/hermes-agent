"""The status effort belongs to this session and the displayed runtime route."""
from datetime import datetime
from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.session import SessionEntry, build_session_key
from tests.gateway.test_status_command import _make_event, _make_runner, _make_source


def _runner():
    source = _make_source()
    key = build_session_key(source)
    entry = SessionEntry(
        session_key=key, session_id="status-reasoning", created_at=datetime.now(),
        updated_at=datetime.now(), platform=Platform.TELEGRAM, chat_type="dm",
    )
    return _make_runner(entry), key


def _agent(effort):
    return SimpleNamespace(
        model="gpt-6.1-sol", provider="openai-codex", api_mode="codex_responses",
        reasoning_config=effort,
        context_compressor=SimpleNamespace(last_prompt_tokens=10, context_length=1000),
    )


@pytest.mark.asyncio
@pytest.mark.parametrize("config,override,expected", [
    ({"agent": {"reasoning_effort": "high"}}, None, "high"),
    ({"agent": {"reasoning_effort": "high", "reasoning_overrides": {"gpt-6.1-sol": "xhigh"}}}, None, "xhigh"),
    ({"agent": {"reasoning_effort": "high"}}, {"enabled": True, "effort": "low"}, "low"),
    ({"agent": {"reasoning_effort": False}}, None, "none (disabled)"),
    ({}, None, "default"),
    ({"agent": {"reasoning_effort": "ultra"}}, None, "ultra (sends max on this route)"),
])
async def test_idle_status_resolves_session_effort_not_stale_cached_agent(monkeypatch, config, override, expected):
    runner, key = _runner()
    config["model"] = {"default": "gpt-6.1-sol", "provider": "openai-codex"}
    monkeypatch.setattr("gateway.run._load_gateway_config", lambda **kw: config)
    monkeypatch.setattr(runner, "_cached_agent_for", lambda _: _agent({"enabled": True, "effort": "medium"}))
    if override is not None:
        runner._set_session_reasoning_override(key, override)
    result = await runner._handle_status_command(_make_event("/status"))
    assert f"**Reasoning:** {expected}" in result
    assert result.index("**Model:**") < result.index("**Reasoning:**") < result.index("**Context:**")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode,expected", [
    ("codex_responses", "ultra (sends max on this route)"),
    ("codex_app_server", "ultra"),
])
async def test_running_status_uses_actual_effort_and_transport(monkeypatch, mode, expected):
    runner, key = _runner()
    agent = _agent({"enabled": True, "effort": "ultra"})
    agent.api_mode = mode
    runner._running_agents[key] = agent
    runner._set_session_reasoning_override(key, {"enabled": True, "effort": "low"})
    result = await runner._handle_status_command(_make_event("/status"))
    assert f"**Reasoning:** {expected}" in result
