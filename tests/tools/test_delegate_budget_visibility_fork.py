"""Fork-preservation gate: delegation budgets are visible in the model-facing schema.

Iteration, inactivity, and late-result continuation limits that stay only in config are
invisible to the model, so a parent sizes tasks blindly and children die at max_iterations
mid-work with no summary. The delegate_task description is rebuilt per get_definitions()
pass from the live delegation config.
"""

from unittest.mock import patch


def _overrides(**cfg):
    from tools.delegate_tool import _build_dynamic_schema_overrides

    timeout = cfg.pop("_child_timeout", 600.0)
    with (
        patch("tools.delegate_tool._get_max_concurrent_children", return_value=7),
        patch("tools.delegate_tool._get_max_spawn_depth", return_value=1),
        patch("tools.delegate_tool._get_orchestrator_enabled", return_value=False),
        patch("tools.delegate_tool._get_child_timeout", return_value=timeout),
        patch("tools.delegate_tool._load_config", return_value=cfg),
    ):
        return _build_dynamic_schema_overrides()


def test_description_states_child_and_parent_budgets():
    desc = _overrides(max_iterations=50, completion_max_turns=10)["description"]
    assert "child 50 iterations" in desc
    assert "600s without progress" in desc
    assert "late-result continuation 10 parent rounds" in desc
    assert "Reserve room to summarize" in desc


def test_budget_text_tracks_config_changes():
    desc = _overrides(max_iterations=12, completion_max_turns=0, _child_timeout=None)["description"]
    assert "child 12 iterations" in desc
    assert "no inactivity cap" in desc
    assert "late-result continuation a full parent turn" in desc


def test_live_registry_definition_carries_budget():
    from tools.registry import registry

    with (
        patch("tools.delegate_tool._get_child_timeout", return_value=600.0),
        patch("tools.delegate_tool._load_config", return_value={"max_iterations": 50, "completion_max_turns": 5}),
    ):
        definition = registry.get_definitions({"delegate_task"})[0]["function"]
    assert "child 50 iterations" in definition["description"]
    assert "late-result continuation 5 parent rounds" in definition["description"]
