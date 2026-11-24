import sys
from pathlib import Path

from gateway.config import GatewayConfig
from gateway.run_inbound import DEFAULT_STT_ECHO_FILLER_WORDS, _strip_stt_filler_words


def test_defaults_off_for_backwards_compatibility():
    cfg = GatewayConfig.from_dict({})
    assert cfg.stt_echo_strip_fillers is False
    assert cfg.stt_echo_filler_words is None
    assert cfg.to_dict()["stt_echo_strip_fillers"] is False
    assert cfg.to_dict()["stt_echo_filler_words"] is None


def test_nested_stt_keys_parse():
    cfg = GatewayConfig.from_dict({
        "stt": {"echo_strip_fillers": "true", "echo_filler_words": ["like", "you know"]},
    })
    assert cfg.stt_echo_strip_fillers is True
    assert cfg.stt_echo_filler_words == ["like", "you know"]


def test_flat_top_level_keys_take_precedence():
    cfg = GatewayConfig.from_dict({
        "stt_echo_strip_fillers": True,
        "stt": {"echo_strip_fillers": False},
    })
    assert cfg.stt_echo_strip_fillers is True


def test_filler_words_accept_comma_string_and_drop_malformed():
    cfg = GatewayConfig.from_dict({
        "stt": {"echo_filler_words": "like, well, "},
    })
    assert cfg.stt_echo_filler_words == ["like", "well"]
    cfg = GatewayConfig.from_dict({"stt": {"echo_filler_words": 42}})
    assert cfg.stt_echo_filler_words is None


def test_strips_basic_fillers_and_tidy():
    assert _strip_stt_filler_words("Um, I think we should go") == "I think we should go"
    assert _strip_stt_filler_words("uh I don't know") == "I don't know"
    assert _strip_stt_filler_words("I think, um, that's it.") == "I think, that's it."
    assert _strip_stt_filler_words("Um") == ""


def test_preserves_words_containing_filler_substrings():
    assert _strip_stt_filler_words("umbrella upshot number") == "Umbrella upshot number"
    assert _strip_stt_filler_words("the umbers are, um, 3.5") == "The umbers are, 3.5"


def test_elongated_variants():
    assert _strip_stt_filler_words("Umm, yeah I uhm thought so") == "Yeah I thought so"
    assert "Umm,".startswith("Umm")


def test_mm_hmm_variants():
    assert _strip_stt_filler_words("Mm-hmm. Okay so um listen") == "Okay so listen"
    assert _strip_stt_filler_words("mm-hm, right") == "Right"


def test_leading_punct_and_sentence_caps():
    assert _strip_stt_filler_words("Uh, so. The um thing. uh") == "So. The thing."
    assert _strip_stt_filler_words("um, and then we left") == "And then we left"


def test_word_list_override():
    got = _strip_stt_filler_words("I like this and, you know, maybe", ("you know",))
    assert got == "I like this and, maybe"
    assert DEFAULT_STT_ECHO_FILLER_WORDS == ("um", "umm", "uh", "uhh", "uhm", "erm", "mm-hmm", "mm-hm")


def test_empty_and_none_safe():
    assert _strip_stt_filler_words("") == ""
    assert _strip_stt_filler_words("   ") == "   "
