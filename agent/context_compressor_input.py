"""Full-conversation summary input for compaction (fork, manifest §10b).

The auxiliary model sees the whole unshortened conversation (head, replaced region and the
retained recent tail) in one request. Upstream's compress() already summarizes before it
archives/publishes reductions; the fork hooks there only set this input for one summary
dispatch and make the attempt fail closed. Never a second history cache or an input to rolling
micro compaction.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Optional


def full_call_names(messages: list[dict[str, Any]]) -> dict[str, str]:
    """tool_call_id -> tool name across the whole source (a result can sit in another region)."""
    from agent.context_compressor import _extract_tool_call_name_and_args, _tc_get

    return {_tc_get(tc, "id"): _extract_tool_call_name_and_args(tc)[0]
            for msg in messages if msg.get("role") == "assistant"
            for tc in msg.get("tool_calls") or () if _tc_get(tc, "id")}


_REHYDRATE_MAX_BYTES = 1_000_000  # per archived original; a larger file keeps its stub


class _RehydrateBudget:
    """Total archived bytes one summary render may read back (beyond it, stubs stay stubs)."""

    def __init__(self, limit: int = 8_000_000) -> None:
        self.left = limit


def rehydrate_archived_original(content: Any, budget: Optional[_RehydrateBudget] = None) -> Any:
    """Summarizer-only: a prune stub's archived original in place of the stub, steering kept last.

    A cold-cache prune leaves ``<stub> — full result: .../cache/pruned/<name>`` in the live transcript;
    the full summary reads the exact original back so the handoff loses nothing. Fail-soft: anything
    but a readable, bounded archive directly in this home's ``cache/pruned`` keeps the stub text. The
    live rows are never changed.
    """
    if not isinstance(content, str):
        return content
    from agent.compaction_steering import split_steering_suffix
    from hermes_constants import get_hermes_home
    from session_rules.media import PRUNED_SUBDIR
    from tools.tool_result_storage import _ARCHIVE_POINTER_RE, has_archive_pointer

    body, steering = split_steering_suffix(content)
    if not has_archive_pointer(body):
        return content
    pointer = _ARCHIVE_POINTER_RE.findall(body)[-1]
    name = pointer.rsplit("/", 1)[-1]
    try:
        archive_dir = (get_hermes_home() / PRUNED_SUBDIR).resolve()
        path = (archive_dir / name).resolve()
        if path.parent != archive_dir or not path.is_file():
            return content
        size = path.stat().st_size
        if size > _REHYDRATE_MAX_BYTES or (budget is not None and size > budget.left):
            return content
        original = path.read_text(encoding="utf-8")
    except (OSError, ValueError, RuntimeError, UnicodeDecodeError):
        return content
    if budget is not None:
        budget.left -= size
    original, _ = split_steering_suffix(_archived_text(original))
    return f"{original}\n[archived original{pointer}]{steering}"


def _archived_text(text: str) -> str:
    """Archives of structured (part-list / multimodal) results hold their JSON: render text and markers."""
    import json

    from agent.compaction_steering import tool_text
    from agent.context_compressor import _summary_part_text

    try:
        parsed = json.loads(text)
    except ValueError:
        return text
    if isinstance(parsed, dict) and parsed.get("_multimodal"):
        return tool_text(parsed)
    if isinstance(parsed, list) and parsed and all(isinstance(p, (dict, str)) for p in parsed) and any(
            isinstance(p, dict) and "type" in p for p in parsed):
        return "\n".join(_summary_part_text(p) for p in parsed)
    return text


def serialize_full_records(compressor: Any, turns: list[dict[str, Any]],
                           call_names: Optional[dict[str, str]] = None,
                           rehydrate: Optional[_RehydrateBudget] = None) -> str:
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
        if role == "tool" and rehydrate is not None:
            content = rehydrate_archived_original(content, rehydrate)
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
        budget = _RehydrateBudget()  # earlier cold-cache prune stubs render their archived originals
        for label, rows in (
            ("RETAINED HEAD", self.messages[:self.start]),
            ("REPLACED REGION", self.messages[self.start:self.end]),
            ("RETAINED RECENT TAIL", self.messages[self.end:]),
        ):
            parts.append(f"BEGIN {label}\n" + serialize_full_records(compressor, rows, call_names, budget) + f"\nEND {label}")
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
