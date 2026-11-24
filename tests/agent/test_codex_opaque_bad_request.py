"""ChatGPT Codex's bare HTTP 400 ``{"detail": "Bad Request"}`` is retried, not misread.

The Codex backend intermittently rejects valid requests with this body: it names no field, and the
identical body is accepted when resent later (openai/codex#57874, #59621). The generic 400 rules read
it as a malformed request on a short session (subagents aborted on the first hit) and as a context
overflow on a long one (the gateway dropped the user's turn and replied "Your request was not
processed"). It must instead be retried with fresh routing (a new prompt-cache key), then once more
without replayed provider state, and only then surface as a provider-side failure that keeps the turn.
"""
from __future__ import annotations

import sys
import types

import httpx
import openai
import pytest

sys.modules.setdefault("fire", types.SimpleNamespace(Fire=lambda *a, **k: None))
sys.modules.setdefault("firecrawl", types.SimpleNamespace(Firecrawl=object))
sys.modules.setdefault("fal_client", types.SimpleNamespace())

import run_agent
from agent.error_classifier import FailoverReason, classify_api_error, is_codex_opaque_bad_request
from gateway.run import GatewayRunner, _normalize_empty_agent_response
from gateway.run_turn import is_context_overflow_failure_result
from tests.agent.test_run_agent_codex_responses import _codex_message_response, _patch_agent_bootstrap

_CODEX_URL = "https://chatgpt.com/backend-api/codex"


def _codex_400(body: dict) -> openai.BadRequestError:
    """The exact exception the OpenAI SDK raises for a Codex 400 (status, parsed body, str form)."""
    request = httpx.Request("POST", f"{_CODEX_URL}/responses")
    response = httpx.Response(400, request=request, json=body)
    return openai.BadRequestError(f"Error code: 400 - {body}", response=response, body=body)


def _bare_400() -> openai.BadRequestError:
    return _codex_400({"detail": "Bad Request"})


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    import time as _time

    monkeypatch.setattr("agent.retry_utils.jittered_backoff", lambda *a, **k: 0.0)
    monkeypatch.setattr(_time, "sleep", lambda *_a, **_k: None)


# ── Classification ──────────────────────────────────────────────────────


_LONG_SESSION = {"approx_tokens": 200_000, "context_length": 272_000, "num_messages": 300}
_SHORT_SESSION = {"approx_tokens": 12_000, "context_length": 272_000, "num_messages": 11}


@pytest.mark.parametrize("base_url", [_CODEX_URL, ""], ids=["codex-route", "route-unstated"])
@pytest.mark.parametrize("session", [_LONG_SESSION, _SHORT_SESSION], ids=["long", "short"])
def test_bare_codex_400_is_a_provider_side_failure_not_overflow_or_malformed(session, base_url):
    classified = classify_api_error(
        _bare_400(), provider="openai-codex", model="gpt-6.1-sol", base_url=base_url, **session)

    assert is_codex_opaque_bad_request(classified)
    assert classified.reason == FailoverReason.server_error
    assert classified.should_compress is False
    assert classified.retryable is False  # recovery runs its own rungs; no blind backoff loop
    assert classified.should_fallback is True


@pytest.mark.parametrize(
    "session, expected",
    [(_LONG_SESSION, FailoverReason.context_overflow), (_SHORT_SESSION, FailoverReason.format_error)],
    ids=["long", "short"],
)
@pytest.mark.parametrize(
    "provider, base_url",
    [("openrouter", "https://openrouter.ai/api/v1"), ("openai-codex", "https://openrouter.ai/api/v1")],
    ids=["other-provider", "codex-provider-off-route"],
)
def test_same_bare_body_off_the_chatgpt_codex_route_keeps_generic_400_rules(session, expected, provider, base_url):
    classified = classify_api_error(_bare_400(), provider=provider, model="some/model", base_url=base_url, **session)

    assert not is_codex_opaque_bad_request(classified)
    assert classified.reason == expected


@pytest.mark.parametrize(
    "body, expected",
    [
        ({"detail": "The 'claude-opus-5-5' model is not supported when using Codex with a ChatGPT account."},
         FailoverReason.model_entitlement),
        ({"detail": "Unsupported content type"}, FailoverReason.invalid_encrypted_content),
        ({"error": {"message": "The encrypted content gAAA...x could not be verified.",
                    "type": "invalid_request_error", "code": "invalid_encrypted_content"}},
         FailoverReason.invalid_encrypted_content),
    ],
    ids=["entitlement", "unsupported-content", "invalid-encrypted"],
)
def test_other_codex_400_bodies_keep_their_own_rules(body, expected):
    classified = classify_api_error(
        _codex_400(body), provider="openai-codex", model="gpt-6.1-sol", base_url=_CODEX_URL, **_SHORT_SESSION)

    assert not is_codex_opaque_bad_request(classified)
    assert classified.reason == expected


# ── Recovery through the real conversation loop ─────────────────────────


def _codex_agent(monkeypatch, *, persisted: list | None = None, base_url: str = _CODEX_URL):
    _patch_agent_bootstrap(monkeypatch)
    agent = run_agent.AIAgent(
        model="gpt-6.1-sol", provider="openai-codex", base_url=base_url, api_key="codex-token",
        quiet_mode=True, max_iterations=4, skip_context_files=True, skip_memory=True,
    )
    agent._cleanup_task_resources = lambda task_id: None
    agent._save_trajectory = lambda messages, user_message, completed: None
    agent._persist_session = lambda messages, history=None: (persisted.append(len(messages))
                                                              if persisted is not None else None)
    return agent


def _scripted_calls(agent, monkeypatch, script: list):
    """Replace the network call: each step raises (an exception) or returns (a response)."""
    requests: list[dict] = []

    def _fake_api_call(api_kwargs):
        requests.append(api_kwargs)
        step = script.pop(0) if script else script_default
        if isinstance(step, BaseException):
            raise step
        return step

    script_default = script[-1] if script else None
    monkeypatch.setattr(agent, "_interruptible_api_call", _fake_api_call)
    return requests


def _history_with_replay(turns: int = 2) -> list[dict]:
    history: list[dict] = []
    for i in range(turns):
        history.append({"role": "user", "content": f"question {i}"})
        history.append({
            "role": "assistant", "content": f"answer {i}",
            "codex_reasoning_items": [{"type": "reasoning", "id": f"rs_{i}", "summary": [],
                                       "encrypted_content": f"gAAAAA-blob-{i}"}],
            "codex_message_items": [{"type": "message", "role": "assistant", "id": f"msg_{i}", "status": "completed",
                                     "content": [{"type": "output_text", "text": f"answer {i}"}]}],
        })
    return history


def _reasoning_items(request: dict) -> list:
    return [item for item in request["input"] if isinstance(item, dict) and item.get("type") == "reasoning"]


def test_one_off_bare_400_retries_with_a_new_cache_key_and_answers(monkeypatch):
    agent = _codex_agent(monkeypatch)
    requests = _scripted_calls(agent, monkeypatch, [_bare_400(), _codex_message_response("done")])

    result = agent.run_conversation("Say done")

    assert result["completed"] is True
    assert result["final_response"] == "done"
    assert len(requests) == 2
    first, retry = requests
    assert first["prompt_cache_key"] != retry["prompt_cache_key"]
    assert (first.get("extra_headers") or {}).get("x-client-request-id") != (
        (retry.get("extra_headers") or {}).get("x-client-request-id"))
    # The fresh key sticks for the session instead of returning to the bucket that just failed.
    later = _scripted_calls(agent, monkeypatch, [_codex_message_response("again")])
    agent.run_conversation("Say again", conversation_history=result["messages"])
    assert later[0]["prompt_cache_key"] == retry["prompt_cache_key"]


def test_repeat_bare_400_resends_without_replayed_state_but_keeps_history(monkeypatch):
    agent = _codex_agent(monkeypatch)
    requests = _scripted_calls(agent, monkeypatch, [_bare_400(), _bare_400(), _codex_message_response("done")])

    result = agent.run_conversation("continue", conversation_history=_history_with_replay())

    assert result["completed"] is True
    assert result["final_response"] == "done"
    assert len(requests) == 3
    assert _reasoning_items(requests[0]) and _reasoning_items(requests[1])
    assert _reasoning_items(requests[2]) == []
    assert not any(isinstance(item, dict) and item.get("id") for item in requests[2]["input"])
    # Only the request copy was stripped: the stored turns still carry their replay state.
    stored = [m for m in result["messages"] if m.get("role") == "assistant" and m.get("codex_reasoning_items")]
    assert len(stored) == 2


def test_persistent_bare_400_fails_bounded_keeps_the_turn_and_is_not_an_overflow(monkeypatch):
    persisted: list[int] = []
    agent = _codex_agent(monkeypatch, persisted=persisted)
    requests = _scripted_calls(agent, monkeypatch, [_bare_400()])
    # Long enough that the old 400 + large-session rules called it an overflow and skipped persistence.
    history = _history_with_replay(turns=50)

    result = agent.run_conversation("continue", conversation_history=history)

    assert result["completed"] is False
    assert result.get("failed") is True
    assert result.get("compression_exhausted") is not True
    assert result["failure_reason"] == FailoverReason.server_error.value
    assert len(requests) == 3  # original + fresh cache key + resend without replay, then stop
    assert persisted, "the failed turn must be persisted so 'continue' resends it"
    assert "Your message was kept" in result["final_response"]
    assert not is_context_overflow_failure_result(result, history_len=len(history))


def test_persistent_bare_400_without_replay_state_stops_after_the_cache_key_retry(monkeypatch):
    agent = _codex_agent(monkeypatch)
    requests = _scripted_calls(agent, monkeypatch, [_bare_400()])

    result = agent.run_conversation("Say done")

    assert result["completed"] is False
    assert result["failure_reason"] == FailoverReason.server_error.value
    assert len(requests) == 2  # nothing replayed, so there is no third variant worth sending


def test_replay_strip_keeps_native_compaction_checkpoints():
    """Checkpoints share ``codex_reasoning_items`` but stand in for pruned history; only reasoning goes."""
    from agent.turn_recovery_codex import recover_codex_opaque_bad_request
    from agent.turn_retry_state import TurnRetryState

    agent = types.SimpleNamespace(api_mode="codex_responses", log_prefix="", _vprint=lambda *a, **k: None,
                                  _replace_primary_openai_client=lambda **k: True)
    checkpoint = {"type": "compaction", "encrypted_content": "gAAAAA-checkpoint"}
    api_messages = [
        {"role": "user", "content": "q"},
        {"role": "assistant", "content": "a", "codex_reasoning_items": [
            checkpoint, {"type": "reasoning", "id": "rs_1", "encrypted_content": "gAAAAA-r"}]},
        {"role": "assistant", "content": "b", "codex_reasoning_items": [
            {"type": "reasoning", "id": "rs_2", "encrypted_content": "gAAAAA-r2"}],
         "codex_message_items": [{"type": "message", "role": "assistant", "id": "msg_2"}]},
    ]
    retry = TurnRetryState()

    assert recover_codex_opaque_bad_request(agent, retry, api_messages) is True  # rung 1: cache key
    assert recover_codex_opaque_bad_request(agent, retry, api_messages) is True  # rung 2: strip
    assert recover_codex_opaque_bad_request(agent, retry, api_messages) is False  # bounded

    assert api_messages[1]["codex_reasoning_items"] == [checkpoint]
    assert "codex_reasoning_items" not in api_messages[2] and "codex_message_items" not in api_messages[2]
    assert api_messages[2]["content"] == "b"


def test_fresh_cache_key_salt_stays_on_the_chatgpt_codex_route(monkeypatch):
    """``openai-codex`` pointed elsewhere keeps its own key even if the salt is set on the agent."""
    agent = _codex_agent(monkeypatch, base_url="https://openrouter.ai/api/v1")
    assert agent.api_mode == "codex_responses" and agent._is_codex_backend() is False
    messages = [{"role": "system", "content": "You are Hermes."}, {"role": "user", "content": "Ping"}]
    before = agent._build_api_kwargs(messages)["prompt_cache_key"]

    agent._codex_cache_scope_salt = "abc123"

    assert agent._build_api_kwargs(messages)["prompt_cache_key"] == before


# ── Gateway seams: persistence decision and the reply the user sees ──────


def test_gateway_persists_the_turn_and_shows_the_bare_400_copy(monkeypatch):
    agent = _codex_agent(monkeypatch)
    _scripted_calls(agent, monkeypatch, [_bare_400()])
    history = _history_with_replay(turns=50)
    result = agent.run_conversation("continue", conversation_history=history)

    session_entry = types.SimpleNamespace(session_id="s1")
    failed_early, hidden_incomplete, overflow = GatewayRunner._hmwa_classify_turn_failure(
        types.SimpleNamespace(), result, history, session_entry)
    assert (failed_early, hidden_incomplete, overflow) == (True, False, False)
    reply = _normalize_empty_agent_response(result, result["final_response"], history_len=len(history))
    assert reply == result["final_response"]
    assert "Your message was kept" in reply


def test_gateway_still_treats_an_explicit_overflow_phrase_as_overflow():
    result = {"failed": True, "failure_reason": "server_error",
              "error": "HTTP 400: This model's maximum context length is 272000 tokens"}

    assert is_context_overflow_failure_result(result, history_len=300) is True
