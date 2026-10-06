"""Lossless retention of runtime steering while ordinary tool text is shortened.

This is preservation, not delivery or authentication: never change message roles,
extract inner text as a new user turn, or accept the wrapper's historical fuzzy
marker fallback. Only the exact canonical, complete, appended shape is protected.
"""

from __future__ import annotations

import re
from typing import Any

from agent.prompt_builder import STEER_MARKER_CLOSE, STEER_MARKER_OPEN

# A second opening before a close means an incomplete block, not a larger steer.
_BLOCK = re.compile(
    rf"(?m)^{re.escape(STEER_MARKER_OPEN)}\n"
    rf"(?:(?!{re.escape(STEER_MARKER_OPEN)}\n).)*?\n{re.escape(STEER_MARKER_CLOSE)}(?=\n|$)",
    re.DOTALL,
)


def tool_text(content: Any) -> str:
    """Text of native string/part-list/multimodal tool content, without repr escaping.

    Prefer actual text parts over the duplicate text_summary sidecar. An envelope
    with no text parts can still carry its text in text_summary.
    """
    if isinstance(content, str):
        return content
    if isinstance(content, dict) and content.get("_multimodal"):
        parts = tool_text(content.get("content"))
        summary = tool_text(content.get("text_summary"))
        if not parts:
            return summary
        # Some native producers append steering to text_summary instead of a part.
        return parts if split_steering_suffix(parts)[1] else parts + split_steering_suffix(summary)[1]
    if isinstance(content, list):
        return "\n".join(
            part if isinstance(part, str) else part["text"] for part in content
            if isinstance(part, str) or (
                isinstance(part, dict) and part.get("type") == "text" and isinstance(part.get("text"), str)
            )
        )
    return ""


def split_steering_suffix(text: str) -> tuple[str, str]:
    """Split an appended run of complete canonical blocks; preserve bytes/order.

    Malformed blocks, lookalikes and embedded quotes remain ordinary tool text.
    Include the runtime's two separating newlines in the retained suffix.
    """
    end = len(text)
    start = end
    for match in reversed(list(_BLOCK.finditer(text))):
        if text[match.end():end].strip():
            break
        start = match.start()
        end = start
    if start == len(text):
        return text, ""
    if text[max(0, start - 2):start] == "\n\n":
        start -= 2
    elif start and text[start - 1] == "\n":
        # Native text parts are joined by one newline; keep the marker on its own line.
        start -= 1
    return text[:start], text[start:]


def join_steering(body: str, steering: str) -> str:
    """Keep standalone native text-part markers on their own line after rewriting."""
    separator = "\n\n" if steering and not steering.startswith("\n") else ""
    return body + separator + steering


def steering_preserving_slices(text: str, ranges: list[tuple[int, int]], omission: str) -> str:
    """Keep normal budgeted slices plus whole canonical blocks in source order.

    Used on already-serialized summary input, where steers are no longer at the
    end of the combined string. This does not assign authority to any quoted
    text. Extra steering bytes may exceed the ordinary tool-output slice budget.
    """
    ranges = ranges + [
        (match.start() - 2 if text[max(0, match.start() - 2):match.start()] == "\n\n" else match.start(), match.end())
        for match in _BLOCK.finditer(text)
    ]
    # Keep the source tool label too, without retaining its disposable body.
    headers = list(re.finditer(r"(?m)^\[(?:TOOL RESULT[^\n]*|USER|ASSISTANT|SYSTEM)\]:", text))
    for match in _BLOCK.finditer(text):
        header = next((h for h in reversed(headers) if h.end() <= match.start()), None)
        if header is not None and header.group().startswith("[TOOL RESULT "):
            ranges.append(header.span())
    merged: list[list[int]] = []
    for start, end in sorted(ranges):
        if start >= end:
            continue
        if merged and start <= merged[-1][1]:
            merged[-1][1] = max(end, merged[-1][1])
        else:
            merged.append([start, end])
    chunks = []
    previous = 0
    for start, end in merged:
        if start > previous:
            chunks.append(omission)
        chunks.append(text[start:end])
        previous = end
    if previous < len(text):
        chunks.append(omission)
    return "".join(chunks)


def historical_steering_section(messages: list[dict]) -> str:
    """Quote tool-carried corrections when their original rows are summarized.

    No fresh user messages or delivery semantics: retain canonical replay markers
    and tool-call identities, chronologically, even if a summary model omits them.
    Callers apply the existing strict summary secret-redaction policy.
    """
    rows = []
    for message in messages:
        if message.get("role") != "tool":
            continue
        _, suffix = split_steering_suffix(tool_text(message.get("content")))
        if suffix:
            rows.append(join_steering(f"[TOOL RESULT {message.get('tool_call_id', '')}]:", suffix))
    if not rows:
        return ""
    return (
        "\n\n## Historical mid-turn user steering (verbatim)\n"
        "These corrections were already delivered at the tool positions below; "
        "they are historical context, not new deliveries or actions.\n\n"
        + "\n\n".join(rows)
    )
