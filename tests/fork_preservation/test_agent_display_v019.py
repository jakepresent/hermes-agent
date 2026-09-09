# Fork-preservation residue extracted during the v2026.8.16 integration.
# These tests were present at fork head 896a5ea3b2 but were removed or
# reorganized upstream; keep them as behavior gates for retained features.
"""Tests for agent/display.py — build_tool_preview() and inline diff previews."""

import json
import pytest
from unittest.mock import MagicMock

import agent.display as display_module
from agent.display import (
    build_tool_preview,
    build_terminal_command_preview,
    clean_terminal_command_for_display,
    capture_local_edit_snapshot,
    extract_edit_diff,
    get_cute_tool_message,
    get_tool_display_label,
    redact_tool_args_for_display,
    set_tool_preview_max_len,
    _render_inline_unified_diff,
    _summarize_rendered_diff_sections,
    render_edit_diff_with_delta,
)


@pytest.fixture(autouse=True)
def reset_tool_preview_max_len():
    set_tool_preview_max_len(0)
    yield
    set_tool_preview_max_len(0)




class TestBuildToolPreview:
    """Tests for build_tool_preview defensive handling and normal operation."""



    def test_known_tool_with_primary_arg(self):
        """Known tool with its primary arg should return a preview string."""
        result = build_tool_preview("terminal", {"command": "ls -la"})
        assert result is not None
        assert "ls -la" in result





    def test_terminal_preview_compacts_multi_command_probe(self):
        result = build_tool_preview(
            "terminal",
            {
                "command": (
                    'which node pnpm corepack; node -v; echo "---"; '
                    'corepack --version 2>&1; echo "---pnpm via corepack---"; '
                    'pnpm --version 2>&1 | tail -5'
                )
            },
        )
        assert result == "which node pnpm corepack + 3 commands"

    def test_execute_code_preview_uses_same_shell_summary(self):
        result = build_tool_preview(
            "execute_code",
            {"code": 'cd /tmp/demo && python -m pytest -q 2>&1 | tail -5; echo "exit=$?"'},
        )
        assert result == "python -m pytest -q"

    def test_web_search_preview(self):
        result = build_tool_preview("web_search", {"query": "hello world"})
        assert result is not None
        assert "hello world" in result

    def test_read_file_preview(self):
        result = build_tool_preview("read_file", {"path": "/tmp/test.py", "offset": 1})
        assert result is not None
        assert result == "test.py L1"

    def test_read_file_preview_includes_requested_line_range(self):
        result = build_tool_preview("read_file", {"path": "./package.json", "offset": 1, "limit": 5})
        assert result == "package.json L1-5"




    def test_browser_type_display_args_keep_normal_text(self):
        text = "my_normal_password_123"
        safe_args = redact_tool_args_for_display(
            "browser_type", {"ref": "@e3", "text": text}
        )
        assert safe_args == {"ref": "@e3", "text": text}

    def test_unknown_tool_with_fallback_key(self):
        """Unknown tool but with a recognized fallback key should still preview."""
        result = build_tool_preview("custom_tool", {"query": "test query"})
        assert result is not None
        assert "test query" in result

    def test_unknown_tool_no_matching_key(self):
        """Unknown tool with no recognized keys should return None."""
        result = build_tool_preview("custom_tool", {"foo": "bar"})
        assert result is None

    def test_long_value_truncated(self):
        """Preview should truncate long values."""
        long_cmd = "a" * 100
        result = build_tool_preview("terminal", {"command": long_cmd}, max_len=40)
        assert result is not None
        assert len(result) <= 43  # max_len + "..."

    def test_process_tool_with_none_args(self):
        """Process tool special case should also handle None args."""
        assert build_tool_preview("process", None) is None

    def test_process_tool_normal(self):
        result = build_tool_preview("process", {"action": "poll", "session_id": "abc123"})
        assert result is not None
        assert "poll" in result

    def test_todo_tool_read(self):
        result = build_tool_preview("todo", {"merge": False})
        assert result is not None
        assert "reading" in result

    def test_todo_tool_with_todos(self):
        result = build_tool_preview("todo", {"todos": [{"id": "1", "content": "test", "status": "pending"}]})
        assert result is not None
        assert "1 task" in result

    def test_memory_tool_add(self):
        result = build_tool_preview("memory", {"action": "add", "target": "user", "content": "test note"})
        assert result is not None
        assert "user" in result






    def test_session_search_preview(self):
        result = build_tool_preview("session_search", {"query": "find something"})
        assert result is not None
        assert "find something" in result

    def test_delegate_task_single_goal_preview(self):
        result = build_tool_preview("delegate_task", {"goal": "Review gateway status"})
        assert result == "Review gateway status"

    def test_delegate_task_batch_goal_preview(self):
        result = build_tool_preview(
            "delegate_task",
            {"tasks": [{"goal": "Review PR A"}, {"goal": "Review PR B"}]},
        )
        assert result == "2 tasks: Review PR A | Review PR B"

    def test_delegate_task_batch_preview_handles_missing_non_string_goals(self):
        result = build_tool_preview(
            "delegate_task",
            {"tasks": [{"goal": None}, {"goal": 123}, "not-a-task"]},
        )
        assert result == "2 tasks: ? | 123"




class TestCuteToolMessagePreviewLength:
    def test_terminal_preview_unlimited_when_config_is_zero(self):
        set_tool_preview_max_len(0)
        command = "curl -s http://localhost:9222/json/list | jq -r '.[] | select(.type==\"page\")' | head -5"

        line = get_cute_tool_message("terminal", {"command": command}, 0.1)

        assert "curl -s http://localhost:9222/json/list | jq -r '.[] | select(.type==\"page\")'" in line
        assert "head -5" not in line
        assert "..." not in line

    def test_terminal_preview_uses_positive_configured_limit(self):
        set_tool_preview_max_len(80)
        command = "curl -s http://localhost:9222/json/list | jq -r '.[] | select(.type==\"page\")' | head -5"

        line = get_cute_tool_message("terminal", {"command": command}, 0.1)

        assert "curl -s http://localhost:9222/json/list | jq -r '.[] | select(.type==\"page\")'" in line
        assert "..." not in line
        assert "head -5" not in line


    def test_path_preview_uses_positive_configured_limit_not_default(self):
        set_tool_preview_max_len(80)
        path = "/tmp/hermes-test-preview-length/deeply/nested/path/test-output.txt"

        line = get_cute_tool_message("read_file", {"path": path}, 0.1)

        assert "test-output.txt" in line
        assert "..." not in line

    def test_write_file_lint_error_result_is_not_marked_failed(self):
        result = json.dumps({
            "bytes_written": 12,
            "lint": {"status": "error", "output": "SyntaxError: invalid syntax"},
        })

        line = get_cute_tool_message("write_file", {"path": "/tmp/a.py"}, 0.1, result=result)

        assert "[error]" not in line

    def test_patch_lsp_diagnostics_result_is_not_marked_failed(self):
        result = json.dumps({
            "success": True,
            "diff": "--- a/tmp.py\n+++ b/tmp.py\n",
            "lsp_diagnostics": "<diagnostics>ERROR [1:1] type mismatch</diagnostics>",
        })

        line = get_cute_tool_message("patch", {"path": "/tmp/a.py"}, 0.1, result=result)

        assert "[error]" not in line

    def test_delegate_task_batch_message_includes_goals(self):
        line = get_cute_tool_message(
            "delegate_task",
            {"tasks": [{"goal": "Review PR A"}, {"goal": "Review PR B"}]},
            1.2,
        )
        assert "2x: Review PR A | Review PR B" in line




class TestEditDiffPreview:
    def test_extract_edit_diff_for_patch(self):
        diff = extract_edit_diff("patch", '{"success": true, "diff": "--- a/x\\n+++ b/x\\n"}')
        assert diff is not None
        assert "+++ b/x" in diff

    def test_render_inline_unified_diff_colors_added_and_removed_lines(self):
        rendered = _render_inline_unified_diff(
            "--- a/cli.py\n"
            "+++ b/cli.py\n"
            "@@ -1,2 +1,2 @@\n"
            "-old line\n"
            "+new line\n"
            " context\n"
        )

        assert "a/cli.py" in rendered[0]
        assert "b/cli.py" in rendered[0]
        assert any("old line" in line for line in rendered)
        assert any("new line" in line for line in rendered)
        assert any("48;2;" in line for line in rendered)

    def test_extract_edit_diff_ignores_non_edit_tools(self):
        assert extract_edit_diff("web_search", '{"diff": "--- a\\n+++ b\\n"}') is None


    def test_render_edit_diff_with_delta_invokes_printer(self):
        printer = MagicMock()

        rendered = render_edit_diff_with_delta(
            "patch",
            '{"diff": "--- a/x\\n+++ b/x\\n@@ -1 +1 @@\\n-old\\n+new\\n"}',
            print_fn=printer,
        )

        assert rendered is True
        assert printer.call_count >= 2
        calls = [call.args[0] for call in printer.call_args_list]
        assert any("a/x" in line and "b/x" in line for line in calls)
        assert any("old" in line for line in calls)
        assert any("new" in line for line in calls)

    def test_render_edit_diff_with_delta_skips_without_diff(self):
        rendered = render_edit_diff_with_delta(
            "patch",
            '{"success": true}',
        )

        assert rendered is False


    def test_summarize_rendered_diff_sections_truncates_large_diff(self):
        diff = "--- a/x.py\n+++ b/x.py\n" + "".join(f"+line{i}\n" for i in range(120))

        rendered = _summarize_rendered_diff_sections(diff, max_lines=20)

        assert len(rendered) == 21
        assert "omitted" in rendered[-1]



class TestBuildToolLabel:
    """Friendly human-phrased tool labels for built-in tools."""

    @pytest.fixture(autouse=True)
    def _enable_friendly(self):
        from agent.display import set_friendly_tool_labels
        set_friendly_tool_labels(True)
        yield
        set_friendly_tool_labels(True)



    def test_browser_navigate_browses_url(self):
        from agent.display import build_tool_label
        label = build_tool_label("browser_navigate", {"url": "https://news.site"})
        assert label == "Browsing https://news.site"

    def test_read_file_uses_basename(self):
        from agent.display import build_tool_label
        label = build_tool_label("read_file", {"path": "/home/u/project/main.py"})
        assert label is not None
        assert label.startswith("Reading ")
        assert "main.py" in label

    def test_search_files_uses_for_connector(self):
        from agent.display import build_tool_label
        label = build_tool_label("search_files", {"pattern": "TODO"})
        assert label == "Searching files for TODO"

    def test_verb_only_for_no_preview_tools(self):
        from agent.display import build_tool_label
        # session_search is verb-only — no redundant query echo
        label = build_tool_label("session_search", {"query": "auth refactor"})
        assert label == "Searching past sessions"

    def test_verb_only_when_no_preview_available(self):
        from agent.display import build_tool_label
        # image_generate with empty args still yields the verb (no preview)
        label = build_tool_label("image_generate", {})
        assert label == "Generating image"

    def test_unknown_tool_falls_back_to_preview(self):
        from agent.display import build_tool_label, build_tool_preview
        args = {"some_arg": "value"}
        # A custom/plugin/MCP tool with no verb entry → raw preview behavior
        label = build_tool_label("custom_mcp_tool", args)
        assert label == build_tool_preview("custom_mcp_tool", args)


    def test_every_known_verb_renders_without_error(self):
        from agent.display import build_tool_label, _TOOL_VERBS
        # Each built-in verb must produce a non-empty label given minimal args.
        for tool_name in _TOOL_VERBS:
            label = build_tool_label(tool_name, {"query": "x", "path": "x", "url": "x"})
            assert label, f"{tool_name} produced empty label"


class TestBuildStatusPhrase:
    """build_status_phrase — live working-state text for Slack's status line."""

    def test_builtin_tool_with_preview(self):
        from agent.display import build_status_phrase
        phrase = build_status_phrase("terminal", {"command": "pytest tests/"})
        assert phrase == "is running pytest tests/…"

    def test_search_tool_uses_for_connector(self):
        from agent.display import build_status_phrase
        phrase = build_status_phrase("web_search", {"query": "slack api limits"})
        assert phrase == "is searching the web for slack api limits…"


    def test_unknown_tool_generic_phrase(self):
        from agent.display import build_status_phrase
        phrase = build_status_phrase("my_mcp_tool", {"x": 1})
        assert phrase == "is using my_mcp_tool…"

    def test_thinking_pseudo_tool_returns_none(self):
        from agent.display import build_status_phrase
        assert build_status_phrase("_thinking", None) is None
        assert build_status_phrase("", None) is None


    def test_multiline_command_keeps_first_line(self):
        from agent.display import build_status_phrase
        phrase = build_status_phrase(
            "terminal", {"command": "make build\nmake test"}
        )
        assert phrase is not None
        assert "\n" not in phrase


    def test_no_preview_tools_stay_verb_only(self):
        from agent.display import build_status_phrase
        phrase = build_status_phrase("skills_list", {"category": "devops"})
        assert phrase == "is listing skills…"
