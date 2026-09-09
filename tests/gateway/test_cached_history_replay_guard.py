"""Fork gate: replay-cleanup symmetry in the cached-history guard (area 22).

``_build_gateway_agent_history`` cleans the DISK transcript (drops session_meta
rows, strips interrupted tool tails, removes dangling tool-call tails, expires
stale dangerous confirmations). A cached ``AIAgent`` holds its RAW message list.
Comparing the cleaned disk copy against the raw live copy made intentional
cleanup look like a shorter persisted transcript, so a healthy session emitted a
false "possible FTS write corruption" warning AND the raw list won — putting
back exactly the rows replay cleanup had removed.
"""
import time

from gateway.run import _build_gateway_agent_history, _select_cached_replay_history


def _clean(rows):
    history, _ = _build_gateway_agent_history(rows, channel_prompt=None, inject_timestamps=False)
    return history


def _dangling_read_only_tool_call():
    return [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "read that file"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"id": "c1", "type": "function",
             "function": {"name": "read_file", "arguments": "{}"}}]},
    ]


def test_cleanup_alone_does_not_trigger_the_corruption_guard():
    """A healthy session whose disk copy was merely CLEANED must not swap."""
    raw = _dangling_read_only_tool_call()
    disk = _clean(raw)
    assert len(disk) < len(raw), "fixture must exercise a cleanup that shortens history"
    assert _select_cached_replay_history(disk, list(raw)) is disk


def test_stripped_tool_tail_is_not_reintroduced_from_live_history():
    """Regression (#49201 class): the model must not re-issue a killed call."""
    raw = _dangling_read_only_tool_call()
    selected = _select_cached_replay_history(_clean(raw), list(raw))
    assert not any(msg.get("tool_calls") for msg in selected)


def test_genuinely_lagging_disk_still_prefers_live_history():
    """The guard's real purpose must survive the symmetry fix."""
    raw = _dangling_read_only_tool_call()
    live = raw[:3] + [
        {"role": "assistant", "content": "answer"},
        {"role": "user", "content": "and more"},
        {"role": "assistant", "content": "more answer"},
    ]
    short_disk = _clean(raw[:2])
    selected = _select_cached_replay_history(short_disk, live)
    assert selected is not short_disk
    assert len(selected) > len(short_disk)


def test_stale_dangerous_confirmation_cannot_enter_via_live_history():
    """Security: expiry must apply to the live side too, not just disk."""
    old = time.time() - 86400
    rows = [
        {"role": "user", "content": "yes, delete everything", "timestamp": old},
        {"role": "assistant", "content": "done"},
        {"role": "user", "content": "unrelated follow-up"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "another"},
    ]
    selected = _select_cached_replay_history(_clean(rows[:2]), rows)
    assert not any("delete everything" in str(m.get("content", "")) for m in selected)


def test_non_list_live_history_is_ignored():
    disk = _clean(_dangling_read_only_tool_call())
    assert _select_cached_replay_history(disk, None) is disk
    assert _select_cached_replay_history(disk, "not a list") is disk
