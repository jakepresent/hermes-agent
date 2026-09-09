"""Fork gate: Discord prompt content carries the full payload (area 8).

Buttons and embeds can both fail to render — mobile web, accessibility clients,
notification previews — so the plain ``content`` copy must be self-sufficient.
Upstream puts the question and a hint there but NOT the numbered choices, and
escapes no invisible codepoints.
"""
import plugins.platforms.discord.adapter as adapter

DiscordAdapter = adapter.DiscordAdapter


class _Stub:
    """Minimal stand-in exposing the real content builders."""

    MAX_MESSAGE_LENGTH = 2000
    _self_contained_prompt_content = DiscordAdapter._self_contained_prompt_content
    _clarify_prompt_content = DiscordAdapter._clarify_prompt_content


HINT = "Pick one below, or click ✏️ Other to type a custom answer."


def _content(question, choices, hint=HINT):
    return _Stub()._clarify_prompt_content(question, choices, hint)


def test_numbered_choices_appear_in_plain_content():
    body = _content("Pick a lane", ["Option A", "Option B", "Option C"])
    assert "1. Option A" in body
    assert "2. Option B" in body
    assert "3. Option C" in body


def test_question_survives_in_plain_content():
    assert "Pick a lane" in _content("Pick a lane", ["x"])


def test_no_choices_still_yields_a_reply_hint():
    body = _content("Freeform?", [], hint="Reply in this channel with your answer.")
    assert "Reply in this channel" in body
    assert "**Choices:**" not in body


def test_zero_width_characters_are_escaped_not_dropped():
    """A prompt must not be able to hide part of itself."""
    body = _content("rm -rf /\u200btmp", ["yes\u200b", "no"])
    assert "\u200b" not in body
    assert "u200b" in body.lower()


def test_bidi_override_is_escaped():
    """RTL override can visually reverse a command's meaning."""
    body = _content("delete \u202eelif", ["ok"])
    assert "\u202e" not in body
    assert "u202e" in body.lower()


def test_ordinary_unicode_is_preserved():
    """Only INVISIBLE codepoints get escaped; normal text stays readable."""
    body = _content("Übergrößenträger — naïve café 日本語", ["émoji 🎉"])
    assert "Übergrößenträger" in body
    assert "日本語" in body
    assert "🎉" in body


def test_long_choice_list_stays_within_discord_limit():
    """Regression: the TAIL is not budgeted by _self_contained_prompt_content,
    so an unbounded choice list would push the send over 2000 chars and fail."""
    body = _content("q" * 500, [f"choice {i} " + "x" * 80 for i in range(24)])
    assert len(body) <= _Stub.MAX_MESSAGE_LENGTH


def test_overflow_keeps_the_hint_and_earliest_choices():
    """Dropping later choices is recoverable (buttons still carry them);
    losing the hint or every choice is not."""
    body = _content("q" * 200, [f"choice {i} " + "y" * 60 for i in range(24)])
    assert len(body) <= _Stub.MAX_MESSAGE_LENGTH
    assert HINT in body
    assert "1. choice 0" in body
