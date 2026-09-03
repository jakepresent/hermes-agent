"""Behavior tests for deterministic first-message routing."""
import pytest

from gateway.message_router import classify_first_message


def route(text, **kwargs):
    return classify_first_message(text, **kwargs).lane


def test_image_always_uses_vision_lane():
    assert route("what do you see?", has_image=True) == "vision_research"


def test_document_uses_judgment_lane():
    assert route("summarize this", has_document=True) == "judgment"


def test_current_research_uses_research_lane():
    assert route("Find the current Amtrak pet policy and cite it") == "vision_research"


def test_code_change_uses_coding_lane():
    assert route("Fix the regression in the Python gateway and run the tests") == "coding"


def test_code_review_uses_judgment_lane():
    assert route("Review this API architecture and tell me what is risky") == "judgment"


def test_sensitive_decision_uses_judgment_lane():
    assert route("Should I change my retirement allocation? Evaluate the risks") == "judgment"


def test_plain_product_decision_uses_judgment_lane():
    assert route("How should I handle monetization in my app?") == "judgment"


def test_purchase_recommendation_uses_research_lane():
    assert route("Which wallet card should I buy?") == "vision_research"


def test_context_wrapper_routes_only_active_message():
    text = "[Recent channel messages] research current models [New message] Fix the Python test regression"
    assert route(text) == "coding"


def test_current_word_alone_does_not_force_research():
    assert route("Help simplify my current setup") == "routine"


def test_mcp_fix_uses_coding_lane():
    assert route("Fix EngHub MCP") == "coding"


def test_mcp_test_uses_coding_lane():
    assert route("Test Xcode MCP now") == "coding"


def test_os_beta_status_uses_research_lane():
    assert route("Is macOS 27 ready to test on my main MacBook?") == "vision_research"


def test_chemical_instructions_use_judgment_lane():
    assert route("Remind me exactly how to mix this photo developer") == "judgment"


def test_document_beats_incidental_image_marker():
    assert route("[The user sent a document: 'x.pdf'] [screenshot]", has_document=True) == "judgment"


def test_reply_wrapper_uses_active_tail():
    text = '[Replying to: "research current models"] [jake] Test Xcode MCP now'
    assert route(text) == "coding"


def test_work_prioritization_uses_judgment_lane():
    assert route("Anything I should do at work today?") == "judgment"


@pytest.mark.parametrize(
    "text",
    [
        "Should I deploy this app?",
        "Should I delete this database?",
        "Should we revert this migration?",
    ],
)
def test_consequential_code_decisions_use_judgment_lane(text):
    assert route(text) == "judgment"


def test_codex_auth_setup_uses_research_lane():
    assert route("Any way to set up Codex authentication in Hermes?") == "vision_research"


def test_underdetermined_incident_uses_judgment_lane():
    assert route("Decide what caused this incident using only the evidence") == "judgment"


def test_routine_message_stays_routine():
    assert route("Draft a short note saying dinner at seven works") == "routine"


def test_followup_ack_is_not_overinterpreted():
    assert route("Go for it") == "routine"
