"""Regression tests for MoA gateway/tool-call behavior.

MoA reference fan-out + aggregator runs through the same outer agent loop as a
normal model.  The aggregator can produce tool calls.  The streaming accumulator
can lose provider-specific tool-call payloads when a Copilot/Claude stream ends
with ``finish_reason='tool_calls'`` but no usable final delta.  Keep MoA on the
complete-response path until that stream shape is recovered safely.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from run_agent import AIAgent


def _empty_tool_defs():
    return []


def _ok_response():
    msg = SimpleNamespace(content="ok", tool_calls=None)
    choice = SimpleNamespace(message=msg, finish_reason="stop")
    usage = SimpleNamespace(prompt_tokens=1, completion_tokens=1, total_tokens=2)
    return SimpleNamespace(choices=[choice], usage=usage, model="moa-test")


def test_moa_uses_complete_response_path_even_with_stream_consumer():
    """Gateway/TUI stream callbacks must not force MoA through streaming.

    The observed bug: Discord `/moa ...` streamed the aggregator, got
    ``finish_reason=tool_calls`` but no executable tool-call payload, then
    surfaced only the model's pre-tool preface.  Non-streaming MoA returns the
    full ChatCompletion with tool_calls intact, so the outer loop can execute
    tools normally.
    """
    with (
        patch("run_agent.get_tool_definitions", return_value=_empty_tool_defs()),
        patch("run_agent.check_toolset_requirements", return_value={}),
        patch("run_agent.OpenAI", return_value=MagicMock()),
    ):
        agent = AIAgent(
            provider="moa",
            model="default",
            api_key="moa-virtual-provider",
            base_url="moa://local",
            api_mode="chat_completions",
            quiet_mode=True,
            skip_context_files=True,
            skip_memory=True,
            max_iterations=1,
        )

    agent.stream_delta_callback = lambda _delta: None
    agent._stream_callback = None

    streaming = MagicMock(side_effect=AssertionError("MoA should not stream"))
    complete = MagicMock(return_value=_ok_response())
    agent._interruptible_streaming_api_call = streaming
    agent._interruptible_api_call = complete

    result = agent.run_conversation(
        "hello",
        moa_config={
            "enabled": True,
            "reference_models": [],
            "aggregator": {"provider": "copilot", "model": "claude-opus-4.8"},
            "reference_temperature": 0.0,
            "aggregator_temperature": 0.0,
        },
    )

    assert result["final_response"] == "ok"
    streaming.assert_not_called()
    complete.assert_called_once()
