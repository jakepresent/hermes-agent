"""Tests for terminal tool prompt-facing schema guidance."""

from tools.terminal_tool import TERMINAL_TOOL_DESCRIPTION


def test_terminal_description_discourages_pipe_to_interpreter():
    """The model should avoid commands that trigger Tirith approval fatigue."""
    assert "Do NOT pipe command output directly into interpreters" in TERMINAL_TOOL_DESCRIPTION
    assert "cmd | python" in TERMINAL_TOOL_DESCRIPTION
    assert "temp file" in TERMINAL_TOOL_DESCRIPTION
