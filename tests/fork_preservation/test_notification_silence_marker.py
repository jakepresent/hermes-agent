"""Fork: "nothing new" replies to internal notices must stay silent instead of posting to chat.

Background-process/delegation notices re-enter a chat session as internal turns. When the model has
nothing to add it must be able to stay silent, and the push notice must say how: without the hint
the model answered with a made-up "nothing new" message, which the gateway posted as a reply.
api_server wakes have no chat to stay silent in and do not get the hint. (Whether [SILENT] is
then suppressed is upstream-owned silence handling, tested upstream.)
"""

from types import SimpleNamespace

import pytest

from gateway.run_notifications import (
    INTERNAL_NOTIFICATION_FOOTER,
    SILENT_REPLY_HINT,
    _mark_internal_notification,
)


def test_push_notice_names_the_exact_silence_marker_above_the_footer():
    text = _mark_internal_notification("[IMPORTANT: Background process p1 completed]", silent_hint=True)
    assert "[SILENT]" in SILENT_REPLY_HINT
    assert SILENT_REPLY_HINT in text
    assert text.endswith(INTERNAL_NOTIFICATION_FOOTER)
    # Idempotent: a retried delivery stacks neither the hint nor the footer.
    again = _mark_internal_notification(text, silent_hint=True)
    assert again == text
    assert again.count(SILENT_REPLY_HINT) == 1


def test_wake_paths_without_chat_silence_do_not_get_the_hint():
    text = _mark_internal_notification("[IMPORTANT: Background process p1 completed]")
    assert SILENT_REPLY_HINT not in text
    assert text.endswith(INTERNAL_NOTIFICATION_FOOTER)


@pytest.mark.asyncio
async def test_real_push_path_carries_the_hint(monkeypatch, tmp_path):
    """Drive the real chat-platform push path, not just the helper."""
    from gateway.config import Platform
    from gateway.session import SessionSource
    from tests.gateway.test_background_process_notifications import _build_runner

    runner = _build_runner(monkeypatch, tmp_path, "all")
    adapter = runner.adapters[Platform.TELEGRAM]
    runner.session_store._entries["agent:main:telegram:dm:123"] = SimpleNamespace(
        origin=SessionSource(platform=Platform.TELEGRAM, chat_id="123", chat_type="dm", user_id="1"))
    await runner._inject_watch_notification(
        "[SYSTEM: Background process matched]",
        {"session_id": "proc_watch", "session_key": "agent:main:telegram:dm:123"})

    adapter.handle_message.assert_awaited_once()
    text = adapter.handle_message.await_args.args[0].text
    assert SILENT_REPLY_HINT in text
    assert text.rstrip().endswith(INTERNAL_NOTIFICATION_FOOTER)


@pytest.mark.asyncio
async def test_api_server_self_post_wake_has_no_hint(monkeypatch, tmp_path):
    from gateway.config import Platform
    from tests.gateway.test_background_process_notifications import AdmittingHandler, _build_runner
    import gateway.wake as wake_mod

    runner = _build_runner(monkeypatch, tmp_path, "all")
    runner.adapters[Platform.API_SERVER] = SimpleNamespace(
        supports_async_delivery=False, handle_message=AdmittingHandler(),
        _host="127.0.0.1", _port=8642, _api_key="k", _model_name="m")
    posts = []

    async def fake_self_post(adapter, *, text, session_id):
        posts.append(text)

    monkeypatch.setattr(wake_mod, "_self_post_chat_completion", fake_self_post)
    assert await runner._inject_watch_notification(
        "[SYSTEM: subagent finished]", {"session_id": "proc_watch", "session_key": "raw-hq-session-id"})
    assert len(posts) == 1
    assert SILENT_REPLY_HINT not in posts[0]
    assert posts[0].rstrip().endswith(INTERNAL_NOTIFICATION_FOOTER)
