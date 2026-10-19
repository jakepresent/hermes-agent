"""Full compaction invariants at the real compressor/auxiliary transport boundary."""
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch
import time

import pytest

from agent.auxiliary_client import AuxiliaryExplicitCancellation
from agent.context_compressor import ContextCompressor, SUMMARY_PREFIX
from agent.prompt_builder import STEER_MARKER_OPEN, STEER_MARKER_CLOSE
from hermes_state import SessionDB


BODY = "body-start " + "unshortened text " * 12_000 + " BODY-MIDDLE " + "body-end " * 2_000
ARGS = '{"payload":"' + "full argument " * 4_000 + ' ARGUMENT-MIDDLE"}'
OLD = "old result " * 3_000 + " OUTCOME-OLD"
RECENT = "recent result " * 600 + " OUTCOME-RECENT"
CORRECTION = "Use the new plan; the earlier proposal is superseded."
STEERING = f"{STEER_MARKER_OPEN}\nDo not rerun the earlier action.\n{STEER_MARKER_CLOSE}"
RAW_SUMMARY = "## Current state\nOUTCOME-OLD; OUTCOME-RECENT. New plan supersedes the proposal.\nHANDOFF-GENERATION"


def _response(text=RAW_SUMMARY, finish="stop"):
    return SimpleNamespace(choices=[SimpleNamespace(
        finish_reason=finish, message=SimpleNamespace(content=text, reasoning_content=None),
    )])


def _engine(mode):
    engine = ContextCompressor(
        model="main-test", summary_model_override="summary-test", quiet_mode=True,
        config_context_length=1_000_000, protect_first_n=1, protect_last_n=4,
        tail_mode=mode, proactive_prune_tokens=1,
        proactive_prune_min_reclaim_tokens=1, abort_on_summary_failure=False,
    )
    engine.tail_token_budget = 12_000
    return engine


def _round(index, content="round complete", arguments='{"action":"inspect"}'):
    call_id = f"round-{index}"
    return [
        {"role": "assistant", "content": "historical action", "tool_calls": [{
            "id": call_id, "type": "function", "function": {"name": "terminal", "arguments": arguments},
        }]},
        {"role": "tool", "tool_call_id": call_id, "content": content},
    ]


def _history():
    messages = [
        {"role": "system", "content": "STANDING-SYSTEM-MUST-NOT-BE-RESUMMARIZED"},
        {"role": "user", "content": f"{SUMMARY_PREFIX}\nPRIOR-HANDOFF-ONCE"},
        {"role": "assistant", "content": "Earlier proposal."},
        {"role": "user", "content": "Inspect the earlier result."},
    ]
    messages += _round(0, OLD + "\n" + STEERING, ARGS)
    messages.append({"role": "user", "content": BODY})
    for i in range(1, 24):
        messages += _round(i, RECENT if i == 15 else "round complete")
    messages += [{"role": "assistant", "content": "Latest progress."}, {"role": "user", "content": CORRECTION}]
    return messages


@pytest.mark.parametrize("mode", ["lean", "legacy"])
@pytest.mark.parametrize("proactive_first", [False, True])
def test_full_source_proactive_and_iterative_handoff_one_no_tools_call(tmp_path, monkeypatch, mode, proactive_first):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("full-input", source="cli")
    source = _history()
    db.append_messages_batch("full-input", source)
    engine = _engine(mode)
    engine.bind_session_state(db, "full-input")
    before = deepcopy(source)
    durable_before = db.get_messages_as_conversation("full-input")
    proactive = source
    if proactive_first:
        proactive, count = engine.prune_tool_results_only(source, current_tokens=900_000)
        assert proactive is source and count == 0, "early text shortening erases unsummarized outcomes"
    assert db.get_messages_as_conversation("full-input") == durable_before

    with patch("agent.context_compressor.call_llm", return_value=_response()) as transport:
        result = engine.compress(proactive, force=True)
    assert transport.call_count == 1
    kwargs = transport.call_args.kwargs
    assert not kwargs.get("tools") and "tool_choice" not in kwargs
    assert kwargs["task"] == "compression" and kwargs["model"] == "summary-test"
    prompt = kwargs["messages"][0]["content"]
    assert BODY in prompt and ARGS in prompt and OLD in prompt and RECENT in prompt
    assert STEERING in prompt and CORRECTION in prompt
    assert prompt.count("PRIOR-HANDOFF-ONCE") == 1
    assert "STANDING-SYSTEM-MUST-NOT-BE-RESUMMARIZED" not in prompt
    assert "REPLACED REGION" in prompt and "RETAINED RECENT TAIL" in prompt
    replaced = prompt.split("BEGIN REPLACED REGION", 1)[1].split("END REPLACED REGION", 1)[0]
    retained = prompt.split("BEGIN RETAINED RECENT TAIL", 1)[1].split("END RETAINED RECENT TAIL", 1)[0]
    assert OLD in replaced and RECENT in retained
    assert not any(row.get("tool_call_id") == "round-0" for row in result)
    assert any(row.get("tool_call_id") == "round-15" for row in result)
    assert source == before and result is not source and len(result) < len(source)
    assert result[-1]["content"] == CORRECTION
    summary = engine._previous_summary
    assert CORRECTION in summary, "task grounding must use full context, not stale cut-region chronology"
    if mode == "lean":
        # The latest correction remains a real user row, not an extra verbatim delivery.
        appendix = summary.partition("## User Messages (verbatim, newest first)")[2]
        assert CORRECTION not in appendix
    assert engine._full_summary_input is None
    assert db.get_messages_as_conversation("full-input") == durable_before

    # Real persistence/restart seam, then the existing handoff must enter once.
    db.archive_and_compact("full-input", result)
    reloaded = db.get_messages_as_conversation("full-input")
    resumed = _engine(mode)
    resumed.bind_session_state(db, "full-input")
    reloaded += [{"role": "assistant", "content": "continued"}, {"role": "user", "content": "next task " * 8_000}]
    for i in range(24, 42):
        reloaded += _round(i)
    newest_steering = f"{STEER_MARKER_OPEN}\nNEWEST-STEERING-CORRECTION, do not repeat completed work.\n{STEER_MARKER_CLOSE}"
    reloaded += _round(99, "newest result\n" + newest_steering)
    with patch("agent.context_compressor.call_llm", return_value=_response("## State\nSECOND-GENERATION")) as transport:
        updated = resumed.compress(reloaded, force=True)
    assert transport.call_count == 1
    assert transport.call_args.kwargs["messages"][0]["content"].count("HANDOFF-GENERATION") == 1
    assert "SECOND-GENERATION" in str(updated)
    assert "NEWEST-STEERING-CORRECTION" in resumed._previous_summary
    assert "Latest user steering" in resumed._previous_summary
    assert updated[-1]["tool_call_id"] == "round-99"
    if mode == "lean":
        appendix = resumed._previous_summary.partition("## User Messages (verbatim, newest first)")[2]
        assert "NEWEST-STEERING-CORRECTION" not in appendix
    assert resumed._full_summary_input is None
    db.close()


@pytest.mark.parametrize("outcome", ["error", "empty", "truncated", "cancel", "feasibility", "structural", "cooldown"])
def test_unvalidated_compaction_is_immutable_and_staging_never_leaks(tmp_path, monkeypatch, outcome):
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    db = SessionDB(db_path=tmp_path / "state.db")
    db.create_session("failure", source="cli")
    source = _history()
    db.append_messages_batch("failure", source)
    engine = _engine("lean")
    engine.bind_session_state(db, "failure")
    engine._previous_summary = "PRE-ATTEMPT-STATE"
    engine._summary_has_user_turn = False
    before = deepcopy(source)
    durable_before = db.get_messages_as_conversation("failure")
    calls = []

    def transport(**kwargs):
        calls.append(kwargs)
        if outcome == "cancel":
            raise AuxiliaryExplicitCancellation()
        if outcome == "error":
            raise RuntimeError("auxiliary transport rejected input")
        return _response("" if outcome == "empty" else RAW_SUMMARY, "length" if outcome == "truncated" else "stop")

    with patch("agent.context_compressor.call_llm", side_effect=transport):
        if outcome == "feasibility":
            with patch.object(engine, "_feasibility_skip", return_value=True):
                result = engine.compress(source)
        elif outcome == "structural":
            with patch.object(engine, "_compress_window", return_value=(len(source), len(source))):
                result = engine.compress(source, force=True)
        elif outcome == "cooldown":
            engine._summary_failure_cooldown_until = time.monotonic() + 3600
            result = engine.compress(source)
        elif outcome == "cancel":
            with pytest.raises(AuxiliaryExplicitCancellation):
                engine.compress(source, force=True)
            result = source
        else:
            result = engine.compress(source, force=True)
    assert result is source and source == before
    assert engine._previous_summary == "PRE-ATTEMPT-STATE"
    assert engine._summary_has_user_turn is False
    assert getattr(engine, "_full_summary_input", None) is None
    assert not engine._last_summary_fallback_used
    assert len(calls) == (0 if outcome in {"feasibility", "structural", "cooldown"} else 1)
    assert db.get_messages_as_conversation("failure") == durable_before
    # Subsequent direct/micro scope may not see any earlier full-input snapshot.
    engine._previous_summary = None
    with patch("agent.context_compressor.call_llm", return_value=_response("direct summary")) as direct:
        engine._generate_summary([{"role": "user", "content": "DIRECT-SCOPE-ONLY"}], bypass_cooldown=True)
    direct_prompt = direct.call_args.kwargs["messages"][0]["content"]
    assert "DIRECT-SCOPE-ONLY" in direct_prompt and "BODY-MIDDLE" not in direct_prompt
    db.close()
