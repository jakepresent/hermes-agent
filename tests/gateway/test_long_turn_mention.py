"""Fork gate: long-turn / blocking-prompt Discord mentions (area 9).

Upstream has no equivalent. A long agent turn or a blocking clarify question can
sit unnoticed in a busy channel; an opt-in mention pulls the requester back.

Two deliberate asymmetries encoded here:
  * final responses are TIME-GATED (only past ``elapsed_seconds``)
  * clarify prompts mention IMMEDIATELY, because they block the run

Approval prompts are NOT covered: upstream's ``discord.approval_mentions``
already owns that path. The fork carried an ``on_approval`` key whose branch no
call site ever reached, and it is deliberately not revived.
"""
from types import SimpleNamespace

from gateway.run import (
    _apply_long_turn_mention_to_response,
    _discord_user_mention_from_policy,
    _long_turn_mention_policy,
    _long_turn_mention_text_for_source,
    _metadata_with_long_turn_mention,
)

SRC = SimpleNamespace(platform="discord", user_id="123456789012345")
MENTION = "<@123456789012345>"


def _cfg(**kw):
    return {"display": {"platforms": {"discord": {"long_turn_mention": {"enabled": True, **kw}}}}}


def test_disabled_by_default():
    assert _long_turn_mention_text_for_source(
        SRC, {}, "discord", elapsed_seconds=9999, surface="final") == ""


def test_final_response_respects_elapsed_threshold():
    cfg = _cfg(elapsed_seconds=90)
    assert _long_turn_mention_text_for_source(
        SRC, cfg, "discord", elapsed_seconds=30, surface="final") == ""
    assert _long_turn_mention_text_for_source(
        SRC, cfg, "discord", elapsed_seconds=120, surface="final") == MENTION


def test_final_without_threshold_stays_silent():
    """An enabled policy with no elapsed key must not ping on every turn."""
    assert _long_turn_mention_text_for_source(
        SRC, _cfg(), "discord", elapsed_seconds=9999, surface="final") == ""


def test_clarify_mentions_immediately():
    """A clarify prompt blocks the run, so it ignores the elapsed gate."""
    assert _long_turn_mention_text_for_source(
        SRC, _cfg(), "discord", elapsed_seconds=0, surface="clarify") == MENTION


def test_clarify_can_be_disabled():
    assert _long_turn_mention_text_for_source(
        SRC, _cfg(on_clarify=False), "discord", elapsed_seconds=0, surface="clarify") == ""


def test_approval_surface_is_not_handled_here():
    """Upstream's discord.approval_mentions owns approval pings."""
    assert _long_turn_mention_text_for_source(
        SRC, _cfg(), "discord", surface="approval") == ""


def test_non_discord_platforms_never_mention():
    telegram = SimpleNamespace(platform="telegram", user_id="1")
    assert _long_turn_mention_text_for_source(
        telegram, _cfg(elapsed_seconds=1), "telegram", elapsed_seconds=99, surface="final") == ""


def test_mass_mentions_are_refused_by_construction():
    assert _discord_user_mention_from_policy(SRC, {"mention": "@everyone"}) == ""
    assert _discord_user_mention_from_policy(SRC, {"mention": "@here"}) == ""


def test_explicit_mention_id_is_honored():
    assert _discord_user_mention_from_policy(SRC, {"mention": "<@999888777666>"}) == "<@999888777666>"


def test_malformed_mention_falls_back_to_source_user():
    assert _discord_user_mention_from_policy(SRC, {"mention": "not a mention"}) == MENTION


def test_rules_list_supplies_elapsed_for_back_compat():
    assert _long_turn_mention_text_for_source(
        SRC, _cfg(rules=[{"elapsed_seconds": 10}]), "discord",
        elapsed_seconds=50, surface="final") == MENTION


def test_string_booleans_are_coerced():
    policy = _long_turn_mention_policy(
        {"display": {"long_turn_mention": {"enabled": "true"}}}, "discord")
    assert policy["enabled"] is True


def test_response_prefix_is_idempotent():
    once = _apply_long_turn_mention_to_response("done", "<@1>")
    assert once == "<@1> done"
    assert _apply_long_turn_mention_to_response(once, "<@1>") == once


def test_empty_mention_leaves_response_untouched():
    assert _apply_long_turn_mention_to_response("done", "") == "done"


def test_metadata_only_gains_a_key_when_mentioning():
    assert _metadata_with_long_turn_mention({"a": 1}, "") == {"a": 1}
    assert _metadata_with_long_turn_mention({"a": 1}, "<@1>")["mention_text"] == "<@1>"
