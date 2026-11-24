"""Fork: auxiliary.background_review.fallback_chain backs a routed review model.

Jake routes the background review to a subscription model (gpt-6-luna via openai-codex) with a
paid OpenRouter DeepSeek backup for when the subscription is over its usage limit. Other aux tasks
read ``auxiliary.<task>.fallback_chain`` through the aux client, but the review runs as a forked
AIAgent, which only falls back through its ``fallback_model`` constructor argument; without this
the chain was silently ignored and an over-limit review simply failed.
"""

from types import SimpleNamespace

from agent.background_review import _fork_init_kwargs

CHAIN = [{"provider": "custom:openrouter-deepinfra-zdr-deepseek-v4", "model": "~deepseek/deepseek-flash-latest"}]


def _parent():
    return SimpleNamespace(
        model="claude-opus-5-5", provider="anthropic", platform="discord", session_id="s",
        enabled_toolsets=None, disabled_toolsets=None,
    )


def _routed_rt():
    return {"provider": "openai-codex", "model": "gpt-6-luna", "api_mode": "codex_responses", "routed": True}


def test_routed_review_gets_the_configured_fallback_chain():
    task = {"provider": "openai-codex", "model": "gpt-6-luna", "fallback_chain": CHAIN}
    kwargs = _fork_init_kwargs(_parent(), _routed_rt(), True, 16, task)
    assert kwargs["model"] == "gpt-6-luna"
    assert kwargs["fallback_model"] == CHAIN


def test_incomplete_or_missing_chain_entries_are_dropped():
    task = {"fallback_chain": [{"provider": "openrouter"}, "junk", {"model": "x"}]}
    assert "fallback_model" not in _fork_init_kwargs(_parent(), _routed_rt(), True, 16, task)
    assert "fallback_model" not in _fork_init_kwargs(_parent(), _routed_rt(), True, 16, {})
