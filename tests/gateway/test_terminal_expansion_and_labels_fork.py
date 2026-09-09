"""Fork gate: terminal command expansion and progress labels (area 5a/5b).

5a: ``display.expand_terminal_commands`` renders the FULL shell command in
tool-progress bubbles while every other tool stays at its summary level.
Upstream only offers verbose mode, which additionally dumps every tool's args.

5b: two tools the user sees constantly (``memory_search``, and the pre-rename
``todo`` alias) had no friendly verb upstream and rendered as raw registry
identifiers in progress lines.
"""
import dataclasses
import importlib
import sys
import types
from types import SimpleNamespace

import pytest
import yaml

import gateway.turn_context as turn_context
from agent.display import get_tool_verb, tool_verb_connector, verb_drops_preview
from gateway.config import Platform
from gateway.display_config import resolve_display_setting
from gateway.session import SessionSource
from gateway.run_turn_runner import TurnRunner
from hermes_cli.config_defaults import DEFAULT_CONFIG
from tests.gateway.test_run_progress_topics import (
    CodeBlockProgressAdapter,
    TerminalCommandAgent,
    _make_runner,
)


class TestExpandTerminalCommands:
    def test_defaults_to_off(self):
        assert resolve_display_setting({}, "discord", "expand_terminal_commands", False) is False
        assert DEFAULT_CONFIG["display"]["expand_terminal_commands"] is False

    def test_global_setting_is_honored(self):
        cfg = {"display": {"expand_terminal_commands": True}}
        assert bool(resolve_display_setting(cfg, "discord", "expand_terminal_commands", False)) is True

    def test_string_booleans_are_coerced(self):
        """Registered in _NORMALISERS, so config spellings normalise like siblings."""
        cfg = {"display": {"expand_terminal_commands": "yes"}}
        assert bool(resolve_display_setting(cfg, "discord", "expand_terminal_commands", False)) is True

    def test_can_be_scoped_to_a_single_platform(self):
        """The point of the setting: full commands where you read them, not everywhere."""
        cfg = {
            "display": {
                "expand_terminal_commands": False,
                "platforms": {"discord": {"expand_terminal_commands": True}},
            }
        }
        assert bool(resolve_display_setting(cfg, "discord", "expand_terminal_commands", False)) is True
        assert bool(resolve_display_setting(cfg, "telegram", "expand_terminal_commands", False)) is False

    def test_turn_context_carries_the_flag_defaulted_off(self):
        """Without the context field the resolved setting never reaches the renderer."""
        found = [
            {f.name: f.default for f in dataclasses.fields(obj)}
            for name in dir(turn_context)
            if dataclasses.is_dataclass(obj := getattr(turn_context, name))
            and "expand_terminal_commands" in {f.name for f in dataclasses.fields(obj)}
        ]
        assert found, "turn context is missing expand_terminal_commands"
        assert all(f["expand_terminal_commands"] is False for f in found)

    @pytest.mark.asyncio
    async def test_all_mode_expands_terminal_only_end_to_end(self, monkeypatch, tmp_path):
        """The resolved Discord flag must survive the display/context seams and reach rendering."""
        monkeypatch.delenv("HERMES_TOOL_PROGRESS_MODE", raising=False)
        (tmp_path / "config.yaml").write_text(
            yaml.safe_dump(
                {
                    "display": {
                        "platforms": {
                            "discord": {
                                "tool_progress": "all",
                                "expand_terminal_commands": True,
                            }
                        }
                    }
                }
            ),
            encoding="utf-8",
        )

        fake_dotenv = types.ModuleType("dotenv")
        fake_dotenv.load_dotenv = lambda *args, **kwargs: None
        monkeypatch.setitem(sys.modules, "dotenv", fake_dotenv)
        fake_run_agent = types.ModuleType("run_agent")
        fake_run_agent.AIAgent = TerminalCommandAgent
        monkeypatch.setitem(sys.modules, "run_agent", fake_run_agent)
        import tools.terminal_tool  # noqa: F401 - register terminal emoji

        adapter = CodeBlockProgressAdapter(platform=Platform.DISCORD)
        runner = _make_runner(adapter)
        gateway_run = importlib.import_module("gateway.run")
        monkeypatch.setattr(gateway_run, "_hermes_home", tmp_path)
        monkeypatch.setattr(gateway_run, "get_hermes_home_override", lambda: None)
        monkeypatch.setattr(
            gateway_run, "_resolve_runtime_agent_kwargs", lambda: {"api_key": "***"}
        )
        source = SessionSource(
            platform=Platform.DISCORD,
            chat_id="12345",
            chat_type="dm",
            thread_id=None,
        )

        result = await runner._run_agent(
            message="hello",
            context_prompt="",
            history=[],
            source=source,
            session_id="fork-terminal-expansion",
            session_key="agent:main:discord:dm:12345",
        )

        assert result["final_response"] == "done"
        rendered = " ".join(call["content"] for call in adapter.sent + adapter.edits)
        assert "node --version" in rendered
        assert "npm install -g hyperframes@latest" in rendered
        assert "set -euo pipefail" not in rendered


class TestProgressLabels:
    def test_memory_search_has_a_friendly_verb(self):
        assert get_tool_verb("memory_search") == "Searching memory"

    def test_memory_search_reads_as_a_search(self):
        verb = get_tool_verb("memory_search")
        assert f"{verb}{tool_verb_connector('memory_search')}fork cleanup" == (
            "Searching memory for fork cleanup"
        )

    def test_todo_alias_matches_the_renamed_tool(self):
        assert get_tool_verb("todo") == get_tool_verb("todo_list") == "Updating tasks"

    def test_todo_without_argument_preview_still_uses_friendly_label(self):
        ctx = SimpleNamespace(
            source=None,
            last_was_terminal_block=[False],
            progress_mode="all",
            expand_terminal_commands=False,
        )
        runner = SimpleNamespace(_adapter_for_source=lambda source: None)
        assert TurnRunner(runner, ctx)._progress_build_message("todo", "", {}) == "⚙️ Updating tasks"

    def test_upstream_verbs_are_untouched(self):
        assert get_tool_verb("web_search") == "Searching the web"
        assert get_tool_verb("session_search") == "Searching past sessions"
        assert verb_drops_preview("session_search") is True

    def test_unknown_tools_still_fall_back(self):
        """Unknown tools keep their registry name so debugging stays possible."""
        assert get_tool_verb("nonexistent_tool") is None
