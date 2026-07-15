"""Regression tests for cached-agent transcript replay guarding."""

from gateway.run import (
    _build_gateway_agent_history,
    _select_cached_replay_history,
)


def _tool_call(name: str = "terminal") -> dict:
    return {
        "id": "call_1",
        "type": "function",
        "function": {"name": name, "arguments": "{}"},
    }


def test_replay_guard_does_not_misread_intentional_replay_cleanup_as_disk_lag():
    """An interrupted tool tail is intentionally absent from replay history.

    The persisted transcript includes a session metadata row plus the interrupted
    assistant→tool pair. Both are removed before the next model request. The
    cached-agent guard must compare that same replay shape, rather than treating
    the raw cached list as evidence that SQLite lost messages.
    """
    persisted_raw = [
        {"role": "session_meta", "content": None},
        {"role": "user", "content": "run the command"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call()]},
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "tool_name": "terminal",
            "content": "[Command interrupted] exit_code: 130",
        },
    ]
    persisted_replay, _ = _build_gateway_agent_history(persisted_raw)
    assert persisted_replay == [{"role": "user", "content": "run the command"}]

    live_history = persisted_raw[1:]
    selected = _select_cached_replay_history(persisted_replay, live_history)

    assert selected is persisted_replay


def test_replay_guard_still_keeps_a_genuinely_longer_live_history():
    persisted_replay = [{"role": "user", "content": "before"}]
    live_history = [
        {"role": "user", "content": "before"},
        {"role": "assistant", "content": "", "tool_calls": [_tool_call("read_file")]},
        {
            "role": "tool",
            "tool_call_id": "call_1",
            "tool_name": "read_file",
            "content": "contents",
        },
        {"role": "assistant", "content": "done"},
    ]

    selected = _select_cached_replay_history(persisted_replay, live_history)

    assert [message["role"] for message in selected] == [
        "user",
        "assistant",
        "tool",
        "assistant",
    ]
    assert selected[-1]["content"] == "done"
