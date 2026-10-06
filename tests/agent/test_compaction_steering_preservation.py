"""User steering rides tool results, but is not disposable tool output."""

import copy
import json
from types import SimpleNamespace

import pytest

from agent.context_compressor import ContextCompressor
from agent.conversation_compression import _ensure_compressed_has_user_turn
from agent.prompt_builder import STEER_MARKER_CLOSE, STEER_MARKER_OPEN, format_steer_marker


CORRECTION = "If two nodes later turn out to be one person, we should probably merge both notes right?"
STEERS = format_steer_marker(CORRECTION) + format_steer_marker("  Keep both notes.\n한국어: 합치기 — don't discard either.  ")
BODY = "ordinary disposable output\n" * 1000


def _compressor(mode="legacy"):
    compressor = ContextCompressor(
        model="test/model", config_context_length=100_000, protect_first_n=1,
        protect_last_n=2, quiet_mode=True, tail_mode=mode,
    )
    compressor.tail_token_budget = 200
    return compressor


def _round(call_id, content):
    return [
        {"role": "assistant", "content": "Working.", "tool_calls": [{
            "id": call_id, "type": "function", "function": {
                "name": "write_file", "arguments": json.dumps({"path": "/tmp/notes.py", "content": "a\nb"}),
            },
        }]},
        {"role": "tool", "tool_call_id": call_id, "content": content},
    ]


def _text(content):
    if isinstance(content, str):
        return content
    if isinstance(content, dict):
        return _text(content.get("content") or content.get("text_summary", ""))
    return "\n".join(part if isinstance(part, str) else part.get("text", "") for part in content)


def _content(shape, suffix):
    if shape == "string":
        return BODY + suffix
    parts = [{"type": "text", "text": BODY + suffix}]
    if shape == "parts_appended":
        return [{"type": "text", "text": BODY}, {"type": "text", "text": suffix.lstrip()}]
    if shape == "parts":
        return parts
    if shape == "envelope_summary":
        parts = [{"type": "text", "text": BODY}]
    return {"_multimodal": True, "content": parts, "text_summary": BODY + suffix}


@pytest.mark.parametrize("path", ["ordinary", "pressure", "duplicate", "lean"])
@pytest.mark.parametrize("shape", ["string", "parts", "parts_appended", "envelope", "envelope_summary"])
@pytest.mark.parametrize("suffix", [
    STEERS,
    "\n\n" + STEER_MARKER_OPEN + "\nmissing close" + STEERS,
    "\n\n[OUT-OF-BAND USER MESSAGE]\nlookalike only\n" + STEER_MARKER_CLOSE,
    "\n\n" + STEER_MARKER_OPEN + "\nmissing close",
    "\n\n" + STEER_MARKER_OPEN.replace("delivered once", "delivered twice") + "\nlookalike only\n" + STEER_MARKER_CLOSE,
], ids=["canonical-multiple", "malformed-before-canonical", "short-lookalike", "unclosed", "wording-lookalike"])
def test_pruning_keeps_only_complete_canonical_steering_in_place(path, shape, suffix):
    compressor = _compressor()
    messages = [{"role": "user", "content": "Keep taking notes."}]
    messages += _round("older", _content(shape, suffix))
    messages[-1]["api_content"] = "stale outbound payload"
    if path == "duplicate":
        messages += _round("newer", _content(shape, suffix))
    messages += [{"role": "user", "content": "Continue."}, {"role": "assistant", "content": "Continuing."}]
    original = copy.deepcopy(messages)
    if path == "lean":
        for index in range(8):
            messages += _round(f"later-{index}", f"short output {index}")
        original = copy.deepcopy(messages)
        pruned = compressor._demote_stale_tail_tools(messages, 0)
    else:
        pruned, count = compressor._prune_old_tool_results(
            messages, protect_tail_count=len(messages) if path == "pressure" else 2,
            protect_tail_tokens=200 if path == "pressure" else None,
        )
        assert count > 0
    assert messages == original, "pruning mutated the source transcript"
    assert [(m["role"], m.get("tool_call_id")) for m in pruned] == [
        (m["role"], m.get("tool_call_id")) for m in messages
    ]
    older = next(m for m in pruned if m.get("tool_call_id") == "older")
    text = _text(older["content"])
    assert len(text) < len(BODY) // 2, "tool body became unprunable"
    expected = "\n" + STEERS.lstrip() if shape == "parts_appended" and suffix == STEERS else STEERS
    if suffix.endswith(STEERS):
        assert text.endswith(expected)
        assert text.count(CORRECTION) == 1
        if path == "duplicate":
            newer = next(m for m in pruned if m.get("tool_call_id") == "newer")
            assert _text(newer["content"]).endswith(expected), "dedupe relocated an earlier user delivery"
    else:
        assert "lookalike only" not in text and "missing close" not in text
        assert STEER_MARKER_OPEN not in text, "malformed output acquired steering protection"
    assert "api_content" not in older, "rewriting retained a stale outbound payload"
    before_anchor = copy.deepcopy(pruned)
    assert _ensure_compressed_has_user_turn(original, pruned) == "already_present"
    assert pruned == before_anchor, "preservation must not rely on synthesizing user turns"
    if path != "lean":
        again, _ = compressor._prune_old_tool_results(pruned, protect_tail_count=2)
        assert [(m["role"], m.get("tool_call_id")) for m in again] == [
            (m["role"], m.get("tool_call_id")) for m in pruned
        ]
        if suffix.endswith(STEERS):
            older_again = next(m for m in again if m.get("tool_call_id") == "older")
            assert _text(older_again["content"]).endswith(expected), "another pruning pass damaged the suffix"


@pytest.mark.parametrize("mode", ["legacy", "lean"])
@pytest.mark.parametrize("summary_succeeds", [True, False])
def test_compress_boundary_preserves_steering_for_summary_and_retained_tail(monkeypatch, mode, summary_succeeds):
    compressor = _compressor(mode)
    compressor.protect_last_n = 8  # Enter the real protected-tail pressure pass.
    # Force both per-message clipping (steering longer than the ordinary tail slice)
    # and aggregate clipping/sampling, with a correction in the omitted middle.
    compressor._CONTENT_MAX = 300
    compressor._CONTENT_HEAD = 100
    compressor._CONTENT_TAIL = 40
    monkeypatch.setattr(ContextCompressor, "_SUMMARY_INPUT_MAX_CHARS", 1200)
    long_steers = format_steer_marker("  " + "preserve this exact user wording\n" * 80 + "  ") + STEERS
    messages = [{"role": "user", "content": "Keep taking notes."}]
    for index in range(18):
        # Steers are appended at runtime, not embedded inside ordinary output.
        content = BODY + f"\nunique {index}" + (long_steers if index in (8, 17) else "")
        messages += _round(f"call-{index}", content)
    original = copy.deepcopy(messages)
    prompts = []

    def summary_client(**kwargs):
        prompts.append(kwargs["messages"][0]["content"])
        if not summary_succeeds:
            raise RuntimeError("offline summary stub failure")
        return SimpleNamespace(choices=[SimpleNamespace(
            finish_reason="stop", message=SimpleNamespace(content="## Goal\nContinue taking notes."),
        )])

    monkeypatch.setattr("agent.context_compressor.call_llm", summary_client)
    compressed = compressor.compress(messages, current_tokens=50_000, force=True)
    assert prompts, "real summary-client boundary was not exercised"
    assert all(long_steers in prompt for prompt in prompts), "summary preprocessing lost user steering"
    assert all("[TOOL RESULT call-8]:" in prompt for prompt in prompts), "summary slicing lost the source tool identity"
    assert messages == original
    tail = next(m for m in compressed if m.get("tool_call_id") == "call-17")
    assert tail["role"] == "tool" and _text(tail["content"]).endswith(long_steers)
    if summary_succeeds:
        assert "ordinary disposable output" not in _text(tail["content"])
    else:
        assert compressed is messages  # No destructive shortening without a checkpoint.
    # The historical correction must survive even if the model omits it or fails.
    summary_text = "\n".join(_text(m.get("content", "")) for m in compressed if m.get("tool_call_id") != "call-17")
    assert long_steers in summary_text
    assert not any(m["role"] == "user" and m.get("content") == CORRECTION for m in compressed)
    before_anchor = copy.deepcopy(compressed)
    assert _ensure_compressed_has_user_turn(original, compressed) == "already_present"
    assert compressed == before_anchor
