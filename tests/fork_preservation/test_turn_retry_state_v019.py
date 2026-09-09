"""Unit tests for TurnRetryState (god-file Phase 1b).

The dataclass holds the inner-retry-loop's one-shot recovery guards + restart
signals. These tests pin its shape and default semantics — the behavioral
guarantee for the loop itself is the existing recovery-branch tests in
tests/run_agent/ which now exercise these fields via `_retry.<flag>`.
"""

from __future__ import annotations

# Fork-preservation residue extracted during the v2026.8.16 integration.
# These tests were present at fork head 896a5ea3b2 but were removed or
# reorganized upstream; keep them as behavior gates for retained features.

import ast
from dataclasses import fields
from pathlib import Path

from agent.turn_retry_state import TurnRetryState


EXPECTED_FIELDS = {
    "codex_auth_retry_attempted",
    "anthropic_auth_retry_attempted",
    "nous_auth_retry_attempted",
    "nous_paid_entitlement_refresh_attempted",
    "copilot_auth_retry_attempted",
    "vertex_auth_retry_attempted",
    "thinking_sig_retry_attempted",
    "invalid_encrypted_content_retry_attempted",
    "image_shrink_retry_attempted",
    "multimodal_tool_content_retry_attempted",
    "oauth_1m_beta_retry_attempted",
    "llama_cpp_grammar_retry_attempted",
    "primary_recovery_attempted",
    "has_retried_429",
    "auth_failover_attempted",
    "restart_with_compressed_messages",
    "restart_with_length_continuation",
    "restart_with_rebuilt_messages",
}


def test_all_guards_default_false():
    s = TurnRetryState()
    for name, value in s:
        assert value is False, f"{name} should default to False"




def test_loop_control_vars_are_not_on_state():
    # retry_count / max_retries / max_compression_attempts stay as loop locals,
    # NOT on the state object (they are while-mechanics, not recovery bookkeeping).
    names = {f.name for f in fields(TurnRetryState)}
    for loop_local in ("retry_count", "max_retries", "max_compression_attempts"):
        assert loop_local not in names
