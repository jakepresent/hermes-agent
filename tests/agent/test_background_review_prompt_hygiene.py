"""Regression tests for conservative background-review curation prompts."""

from agent.background_review import (
    _COMBINED_REVIEW_PROMPT,
    _MEMORY_REVIEW_PROMPT,
    _SKILL_REVIEW_PROMPT,
)


_PRESSURE_PHRASES = (
    "most sessions produce at least one skill update",
    "a pass that does nothing is a missed learning opportunity",
    "should NOT be the default",
    "don't reach for that conclusion as a default",
)


def test_skill_review_treats_noop_as_normal():
    prompt = _SKILL_REVIEW_PROMPT.lower()
    assert all(phrase not in prompt for phrase in _PRESSURE_PHRASES)
    assert "doing nothing is the normal outcome" in prompt
    assert "skills_list" in prompt
    assert "near-duplicate" in prompt
    assert "160 characters" in prompt


def test_memory_review_requires_novel_durable_user_fact():
    prompt = _MEMORY_REVIEW_PROMPT.lower()
    assert "genuinely new" in prompt
    assert "already present" in prompt
    assert "project status" in prompt
    assert "review metadata" in prompt
    assert "nothing to save" in prompt


def test_combined_review_carries_both_hygiene_policies():
    prompt = _COMBINED_REVIEW_PROMPT.lower()
    assert all(phrase not in prompt for phrase in _PRESSURE_PHRASES)
    assert "doing nothing is the normal outcome" in prompt
    assert "genuinely new" in prompt
    assert "near-duplicate" in prompt
    assert "160 characters" in prompt
