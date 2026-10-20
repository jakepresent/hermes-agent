"""Fork: "nothing new" replies to internal notices must stay silent instead of posting to chat.

Background-process/delegation notices re-enter a chat session as internal turns. When the model has
nothing to add it must be able to stay silent, and the notice must say how. Observed failures this
pins: the model answered with a made-up "nothing new" message or with the bounded-delegation
sentinel ``[NO_USER_VISIBLE_UPDATE]`` on an ordinary notice, and the gateway posted it as a reply;
and the bounded continuation's own sentinel was blanked into an empty response, which the
normalizer turned into a visible "no response was generated" warning.
"""

from types import SimpleNamespace

import pytest

import gateway.run
from gateway.response_filters import (
    INTERNAL_NOTIFICATION_DISPLAY_KIND,
    is_intentional_silence_response,
)
from gateway.run_notifications import (
    INTERNAL_NOTIFICATION_FOOTER,
    SILENT_REPLY_HINT,
    _mark_internal_notification,
)
from gateway.run_turn import GatewayTurnMixin

SENTINEL = "[NO_USER_VISIBLE_UPDATE]"


class _Runner(GatewayTurnMixin):
    def __init__(self):
        self.async_session_store = SimpleNamespace(clear_resume_pending=self._noop)

    async def _noop(self, *_a, **_k):
        return None

    async def _clear_restart_failure_count(self, *_a, **_k):
        return None


async def _shape(final_response, *, display_kind, bounded=False):
    agent_result = {"final_response": final_response, "model": "m", "messages": [], "api_calls": 3}
    source = SimpleNamespace(chat_id="c1", platform=SimpleNamespace(value="discord"))
    response, silent, _ = await _Runner()._hmwa_shape_agent_response(
        agent_result, source, history=[], session_entry=SimpleNamespace(session_id="s"), session_key=None,
        _quick_key=None, run_generation=0, _run_start_session_id="s", _platform_name="discord",
        _msg_start_time=0.0, persist_user_display_kind=display_kind,
        bounded_delegation_completion=bounded,
    )
    return response, silent


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


def test_bounded_sentinel_is_a_shared_silence_marker():
    assert is_intentional_silence_response(SENTINEL)
    # Prose that merely mentions it still gets delivered.
    assert not is_intentional_silence_response(f"I would have replied {SENTINEL} but here is a delta.")


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["[SILENT]", SENTINEL])
async def test_internal_notice_turn_answered_with_a_marker_posts_nothing(marker):
    _response, silent = await _shape(marker, display_kind=INTERNAL_NOTIFICATION_DISPLAY_KIND)
    assert silent is True


@pytest.mark.asyncio
async def test_bounded_continuation_sentinel_is_silent_not_a_no_response_warning():
    response, silent = await _shape(SENTINEL, display_kind=INTERNAL_NOTIFICATION_DISPLAY_KIND, bounded=True)
    assert silent is True
    assert "no response was generated" not in response.lower()


@pytest.mark.asyncio
@pytest.mark.parametrize("marker", ["[SILENT]", SENTINEL])
async def test_a_human_turn_never_vanishes_on_a_marker(marker):
    response, silent = await _shape(marker, display_kind=None)
    assert silent is False
    assert response.strip() and response.strip() != marker
