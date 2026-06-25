from types import SimpleNamespace

from gateway.config import Platform
from gateway.run import (
    _apply_long_turn_mention_to_response,
    _long_turn_mention_text_for_source,
)


def _discord_source(user_id="123456789"):
    return SimpleNamespace(platform=Platform.DISCORD, user_id=user_id)


def _config(rules, **overrides):
    return {
        "display": {
            "platforms": {
                "discord": {
                    "long_turn_mention": {
                        "enabled": True,
                        "rules": rules,
                        **overrides,
                    }
                }
            }
        }
    }


def test_long_turn_mention_elapsed_only_rule_mentions_final_response():
    cfg = _config([{"elapsed_seconds": 90}])

    mention = _long_turn_mention_text_for_source(
        _discord_source(),
        cfg,
        "discord",
        elapsed_seconds=91,
        tool_calls=0,
        surface="final",
    )

    assert mention == "<@123456789>"
    assert _apply_long_turn_mention_to_response("done", mention) == "<@123456789> done"


def test_long_turn_mention_ignores_tool_call_thresholds_for_final_response():
    cfg = _config([{"elapsed_seconds": 45, "tool_calls": 999}])
    source = _discord_source()

    assert not _long_turn_mention_text_for_source(
        source, cfg, "discord", elapsed_seconds=44.9, tool_calls=999, surface="final"
    )
    assert _long_turn_mention_text_for_source(
        source, cfg, "discord", elapsed_seconds=45, tool_calls=0, surface="final"
    ) == "<@123456789>"


def test_long_turn_mention_default_disabled_and_unsupported_platform_do_not_ping():
    source = _discord_source()

    assert not _long_turn_mention_text_for_source(
        source, {}, "discord", elapsed_seconds=999, tool_calls=999, surface="final"
    )
    assert not _long_turn_mention_text_for_source(
        SimpleNamespace(platform=Platform.TELEGRAM, user_id="123"),
        _config([{"elapsed_seconds": 1}]),
        "telegram",
        elapsed_seconds=999,
        tool_calls=999,
        surface="final",
    )


def test_long_turn_mention_surface_gates_final_vs_approval():
    cfg = _config(
        [{"elapsed_seconds": 45}],
        on_final=False,
        on_approval=True,
    )
    source = _discord_source()

    assert not _long_turn_mention_text_for_source(
        source, cfg, "discord", elapsed_seconds=45, tool_calls=0, surface="final"
    )
    assert _long_turn_mention_text_for_source(
        source, cfg, "discord", elapsed_seconds=0, tool_calls=0, surface="approval"
    ) == "<@123456789>"


def test_apply_long_turn_mention_is_idempotent():
    assert _apply_long_turn_mention_to_response("<@123> already done", "<@123>") == "<@123> already done"
    assert _apply_long_turn_mention_to_response("", "<@123>") == ""
    assert _apply_long_turn_mention_to_response("done", "") == "done"


def test_long_turn_mention_top_level_elapsed_aliases_work():
    cfg = _config([], ping_after_seconds=30)

    assert not _long_turn_mention_text_for_source(
        _discord_source(), cfg, "discord", elapsed_seconds=29.9, surface="final"
    )
    assert _long_turn_mention_text_for_source(
        _discord_source(), cfg, "discord", elapsed_seconds=30, surface="final"
    ) == "<@123456789>"
