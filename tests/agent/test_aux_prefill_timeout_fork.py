"""Fork gate: prefill-aware auxiliary stream timeouts (area 11).

Upstream applies a flat 60s first-token fence to auxiliary streams. That is
correct for an ordinary dead stream, but a compression request sends hundreds of
thousands of input tokens and the provider cannot emit a first token until it has
read them — so the fence fires on a healthy stream and the caller falls back.

Two coupled behaviors are gated here:
  1. the FIRST-token window scales with prefill size, while the re-arm window
     stays at the base value (a stream that goes quiet mid-flight still fails fast)
  2. a large-prefill no-progress timeout skips the same-provider retry, because
     that retry re-sends the same oversized payload for the same result
"""
from unittest.mock import patch

from agent.auxiliary_client import (
    _AUX_STREAM_NO_PROGRESS_TIMEOUT_SECONDS,
    _CodexStreamGuard,
    _aux_stream_no_progress_timeout,
    _should_skip_same_provider_retry,
)

NO_PROGRESS = TimeoutError(
    "Codex auxiliary Responses stream produced no output within 300.0s "
    "(no-progress timeout, 300.2s elapsed)"
)
STALL = TimeoutError(
    "Codex auxiliary Responses stream stalled: no new output for 60.0s (75.0s elapsed)"
)
SMALL = [{"role": "user", "content": "x" * 400}]
LARGE = [{"role": "user", "content": "x" * 400_004}]


class TestPrefillScaledFirstTokenWindow:
    def test_small_prefill_keeps_the_base_fence(self):
        assert _aux_stream_no_progress_timeout(SMALL, None) == _AUX_STREAM_NO_PROGRESS_TIMEOUT_SECONDS

    def test_window_scales_in_steps_with_prefill(self):
        for tokens, expected in ((100_001, 120.0), (250_001, 180.0), (400_001, 300.0)):
            with patch(
                "agent.auxiliary_client.estimate_messages_tokens_rough", return_value=tokens
            ):
                assert _aux_stream_no_progress_timeout(SMALL, None) == expected

    def test_configured_timeout_still_wins(self):
        """An explicitly shorter task timeout is never widened by scaling."""
        with patch("agent.auxiliary_client.estimate_messages_tokens_rough", return_value=400_001):
            assert _aux_stream_no_progress_timeout(SMALL, 30.0) == 30.0

    def test_only_the_first_window_widens(self):
        """Once the stream is alive, re-arming uses the BASE window — otherwise a
        stalled large-prefill stream would hold the widened window forever."""
        guard = _CodexStreamGuard(None, None, 300.0)
        assert guard.first_progress_timeout == 300.0
        assert guard.no_progress_timeout == _AUX_STREAM_NO_PROGRESS_TIMEOUT_SECONDS

    def test_guard_defaults_to_upstream_behavior(self):
        """Omitting the argument must not change upstream semantics."""
        guard = _CodexStreamGuard(None, None)
        assert guard.first_progress_timeout == guard.no_progress_timeout


class TestLargePrefillRetryGate:
    def test_large_prefill_no_progress_skips_same_provider_retry(self):
        assert _should_skip_same_provider_retry("compression", NO_PROGRESS, LARGE) is True

    def test_small_prefill_keeps_the_upstream_carve_out(self):
        """A genuinely cheap first-token fail is still worth one retry."""
        assert _should_skip_same_provider_retry("compression", NO_PROGRESS, SMALL) is False

    def test_missing_messages_preserves_upstream_behavior(self):
        assert _should_skip_same_provider_retry("compression", NO_PROGRESS, None) is False

    def test_mid_stream_stall_always_skips(self):
        assert _should_skip_same_provider_retry("compression", STALL, SMALL) is True

    def test_vision_is_also_critical_path(self):
        assert _should_skip_same_provider_retry("vision", NO_PROGRESS, LARGE) is True

    def test_non_critical_tasks_are_unaffected(self):
        assert _should_skip_same_provider_retry("title", NO_PROGRESS, LARGE) is False
