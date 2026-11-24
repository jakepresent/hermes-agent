"""Fork-preservation gate (§2a): child budgets are visible in the model-facing delegate_task schema.

Iteration and inactivity limits that stay only in config are invisible to the model, so a parent
sizes tasks blindly and children die at max_iterations mid-work with no summary. The description is
rebuilt per get_definitions() pass from the live delegation config.
"""

from unittest.mock import patch


def _description(max_iterations=50, child_timeout=600.0):
    from tools.delegate_tool import _build_dynamic_schema_overrides

    with (
        patch("tools.delegate_tool_config._get_child_timeout", return_value=child_timeout),
        patch("tools.delegate_tool_config._load_config", return_value={"max_iterations": max_iterations}),
    ):
        return _build_dynamic_schema_overrides()["description"]


def test_description_states_child_iteration_and_inactivity_budgets():
    desc = _description()
    assert "50 iterations" in desc
    assert "600s without progress" in desc
    assert "Reserve room to summarize" in desc
    # The late-result continuation budget feature was dropped; its wording must not leak in.
    assert "continuation" not in desc


def test_budget_text_tracks_config_changes():
    desc = _description(max_iterations=12, child_timeout=None)
    assert "12 iterations" in desc
    assert "no inactivity cap" in desc


def test_live_registry_definition_carries_budget():
    from tools.registry import registry

    with (
        patch("tools.delegate_tool_config._get_child_timeout", return_value=600.0),
        patch("tools.delegate_tool_config._load_config", return_value={"max_iterations": 50}),
    ):
        definition = registry.get_definitions({"delegate_task"})[0]["function"]
    assert "50 iterations" in definition["description"]
    assert "600s without progress" in definition["description"]
