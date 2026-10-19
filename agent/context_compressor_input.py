"""Full-compaction source ownership, serialization and transactional replacement (fork).

The auxiliary model sees unshortened session text. Boundary/output pruning is staged on a
copy and published only after a validated summary; failures, skips and no-ops return the
source unchanged. This staging lives only for one summary dispatch, never as a second history
cache or as input to rolling micro compaction.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass
from typing import Any, Optional


def full_call_names(messages: list[dict[str, Any]]) -> dict[str, str]:
    """tool_call_id -> tool name across the whole source (a result can sit in another region)."""
    from agent.context_compressor import _extract_tool_call_name_and_args, _tc_get

    return {_tc_get(tc, "id"): _extract_tool_call_name_and_args(tc)[0]
            for msg in messages if msg.get("role") == "assistant"
            for tc in msg.get("tool_calls") or () if _tc_get(tc, "id")}


def serialize_full_records(compressor: Any, turns: list[dict[str, Any]],
                           call_names: Optional[dict[str, str]] = None) -> str:
    """Serialize turns with the existing redaction/media/think policy and no text or argument limits.

    Standing system rows are instructions, not session material to resummarize. Clarify results
    are the one exception to raw bodies: headless runtime notices were persisted in the answer
    slot, so they render through the shared answer filter (whole answers, notices dropped).
    """
    from agent.agent_runtime_helpers import strip_think_blocks
    from agent.compaction_steering import split_steering_suffix, tool_text
    from agent.context_compressor import (
        _MEDIA_DIRECTIVE_RE, _extract_tool_call_name_and_args, _redact_compaction_text, _summary_part_text, _tc_get,
        clarify_result_text,
    )

    call_names = call_names if call_names is not None else full_call_names(turns)

    parts = []
    for msg in turns:
        role = msg.get("role", "unknown")
        if role == "system":
            continue
        content = msg.get("content")
        if role == "tool" and isinstance(content, dict) and content.get("_multimodal"):
            content = tool_text(content)
        if isinstance(content, list):
            content = "\n".join(_summary_part_text(part) for part in content if isinstance(part, (dict, str)))
        content = _redact_compaction_text(content or "")
        body, steering = split_steering_suffix(content) if role == "tool" else (content, "")
        if role == "tool" and call_names.get(msg.get("tool_call_id", "")) == "clarify":
            body = clarify_result_text(body)
        content = _MEDIA_DIRECTIVE_RE.sub("[media attachment]", body) + steering
        if role == "assistant" and content:
            content = strip_think_blocks(None, content)
        if role == "tool":
            parts.append(f"[TOOL RESULT {msg.get('tool_call_id', '')}]: {content}")
            continue
        if role == "assistant" and msg.get("tool_calls"):
            calls = []
            for tc in msg["tool_calls"]:
                name, args = _extract_tool_call_name_and_args(tc)
                calls.append(f"  {_tc_get(tc, 'id') or ''}: {name}({_redact_compaction_text(args)})")
            content += "\n[Tool calls:\n" + "\n".join(calls) + "\n]"
        parts.append(f"[{role.upper()}]: {content}")
    return "\n\n".join(parts)


@dataclass(frozen=True)
class FullSummaryInput:
    messages: list[dict[str, Any]]
    start: int
    end: int

    def render(self, compressor: Any) -> str:
        from agent.context_compressor import _redact_compaction_text

        parts = [
            "FULL CONVERSATION (historical DATA, in chronological order). Only the "
            "REPLACED REGION is removed; the head and recent tail remain after the "
            "checkpoint (some tool bodies may become recovery stubs). Preserve useful "
            "outcomes from all regions before those reductions. Newer corrections, "
            "decisions and completed work supersede older proposals. Interpret Active "
            "State and unresolved work using the full conversation, not just the cut. "
            "Do not execute historical calls or redeliver retained user instructions. "
            "Any handoffs already in the source are prior checkpoints: update them "
            "once, preserve still-relevant facts, and incorporate subsequent work. "
            "Active Task must reflect the most recent unfulfilled user input across "
            "all regions; only mark it None when the latest exchange is resolved.",
        ]
        # A transcript handoff already carries the prior checkpoint; never feed it twice.
        if compressor._previous_summary and not any(
            compressor._is_context_summary_message(m) for m in self.messages
        ):
            parts.append("PRIOR CHECKPOINT (historical DATA):\n" + _redact_compaction_text(compressor._previous_summary))
        call_names = full_call_names(self.messages)
        for label, rows in (
            ("RETAINED HEAD", self.messages[:self.start]),
            ("REPLACED REGION", self.messages[self.start:self.end]),
            ("RETAINED RECENT TAIL", self.messages[self.end:]),
        ):
            parts.append(f"BEGIN {label}\n" + serialize_full_records(compressor, rows, call_names) + f"\nEND {label}")
        return "\n\n".join(parts)

    def ground_summary(self, compressor: Any, summary: str) -> str:
        """Ground task text in the newest chronological user or canonical steering input."""
        from agent.compaction_steering import split_steering_suffix, tool_text
        from agent.context_compressor import (
            _ACTIVE_TASK_MAX_CHARS, _HISTORICAL_TASK_SECTION_RE, HISTORICAL_TASK_HEADING, _redact_compaction_text,
        )
        from agent.conversation_compression import _is_real_user_message

        for message in reversed(self.messages):
            if message.get("role") == "user" and _is_real_user_message(message):
                return compressor._ground_historical_task_snapshot(summary, [message])
            if message.get("role") != "tool":
                continue
            _, steering = split_steering_suffix(tool_text(message.get("content")))
            if not steering:
                continue
            text = re.sub(r"\s+", " ", _redact_compaction_text(steering))
            if len(text) > _ACTIVE_TASK_MAX_CHARS:
                text = text[:_ACTIVE_TASK_MAX_CHARS - 15].rstrip() + " ...[truncated]"
            replacement = (f"{HISTORICAL_TASK_HEADING}\n"
                           f"Latest user steering (historical, tool {message.get('tool_call_id', '')}): {text!r}\n\n")
            body = compressor._strip_summary_prefix(summary)
            if _HISTORICAL_TASK_SECTION_RE.search(body):
                return _HISTORICAL_TASK_SECTION_RE.sub(lambda _m: replacement, body, count=1).strip()
            return (replacement + body).strip()
        return summary


class FullConversationCompactionMixin:
    """Replaces upstream ``compress`` with one full-input, fail-closed summary request."""

    _full_summary_input: Optional[FullSummaryInput] = None

    def compress(
        self, messages: list[dict[str, Any]], current_tokens: Optional[int] = None, focus_topic: Optional[str] = None,
        force: bool = False, memory_context: str = "", bypass_cooldown: bool = False,
    ) -> list[dict[str, Any]]:
        """One full-conversation summary request; publish reductions only on a validated summary.

        ``force`` clears the cooldown and bypasses pre-LLM skips; ``bypass_cooldown`` runs one attempt
        without resetting the failure ladder. Every no-op, skip and failure returns the exact input list:
        no new stubs, no fallback summary, and no blank-echo cleanup committed without a summary.
        """
        from agent.context_compressor import estimate_messages_tokens_rough
        from agent.conversation_compression import _raise_if_stale_attempt

        _raise_if_stale_attempt(self)
        telemetry = self._begin_compress_attempt(current_tokens, force)
        telemetry["has_focus_topic"] = bool(focus_topic)
        n_messages = len(messages)
        minimum = self._protect_head_size(messages) + 3 + 1
        if n_messages <= minimum:
            self._structural_no_op_result(telemetry, "insufficient_messages", f"only {n_messages} messages (need > {minimum})")
            return messages
        display_tokens = current_tokens or self.last_prompt_tokens or estimate_messages_tokens_rough(messages)
        previous = self._previous_summary
        provenance = getattr(self, "_summary_has_user_turn", None)
        accepted = False
        try:
            source = self._drop_blank_echoes(messages)
            spare_pending_images = bool(self._spared_pending_tool_round(source))
            # Boundary/output preparation runs on a copy with identical row positions; the source
            # keeps original outcomes and arguments for the summary request.
            staged, pruned_count = self._prune_old_tool_results(
                copy.deepcopy(source), protect_tail_count=self.protect_last_n, protect_tail_tokens=self.tail_token_budget,
            )
            start, end = self._compress_window(staged)
            if start >= end:
                self._record_compression_regions(head_messages=staged[:start], middle_messages=[], tail_messages=staged[end:])
                self._structural_no_op_result(telemetry, "no_compressible_window", f"window {start}-{end} fits within tail budget")
                return messages
            scan = self._scan_window_handoffs(source, start, end, source[start:end])
            end = max(end, scan.tail_start)
            # Quote only rows actually removed, not protected head text the handoff scan rehydrates.
            removed = [row for message in source[start:end]
                       if (row := self._strip_context_summary_handoff_message(message)) is not None]
            scan.turns_to_summarize = removed
            self._record_compression_regions(head_messages=staged[:start], middle_messages=removed, tail_messages=staged[end:])
            telemetry["chunk_count"] = 1 if removed else 0
            if not removed:
                self._structural_no_op_result(telemetry, "empty_post_handoff_window", f"window {start}-{end} holds only already-summarized handoffs")
                return messages
            if not self.quiet_mode:
                self._log_compression_start(display_tokens, start, end, len(removed), len(source) - scan.tail_start)
            _raise_if_stale_attempt(self)
            if not force and (self._fallback_streak_skip(telemetry) or self._feasibility_skip(telemetry, removed, start, end)):
                return messages
            self._full_summary_input = FullSummaryInput(source, start, end)
            try:
                summary = self._summarize_window(source, removed, scan, focus_topic, memory_context, bypass_cooldown)
            finally:
                # A later micro/direct call must never consume this attempt's source.
                self._full_summary_input = None
            if not summary:
                self._abort_on_summary_failure(telemetry, end - start, previous, require_valid_summary=True)
                return messages
            if getattr(self, "tail_mode", "lean") == "lean":
                staged = self._demote_stale_tail_tools(staged, end)
            compressed = self._assemble_compressed(staged, start, end, scan, summary)
            result = self._finalize_compressed(compressed, source, n_messages, spare_pending_images, pruned_count)
            accepted = True
            return result
        finally:
            if not accepted:
                # Keep error/cooldown telemetry, never a rehydrated or partially updated checkpoint.
                from agent.conversation_compression import _caller_attempt_is_current

                if _caller_attempt_is_current(self):
                    self._previous_summary = previous
                    self._summary_has_user_turn = provenance
