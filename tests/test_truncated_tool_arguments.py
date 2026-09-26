"""Incomplete historical argument previews must never execute as new tool calls."""

import json
from unittest.mock import patch

import pytest

import model_tools


MARKER = "...[truncated]"


@pytest.mark.parametrize(
    ("name", "args", "field"),
    [
        (
            "execute_code",
            {"code": "import pathlib\nroot = pathlib.Path('/tmp/bp2')\n"
                     "s = s.replace('''protocol OnboardingCompletionRepository: DogfoodRep" + MARKER},
            "code",
        ),
        ("terminal", {"command": "python3 -c 'print(1)' " + MARKER + "\n"}, "command"),
        ("write_file", {"path": "/tmp/never-created", "content": "hello" + MARKER}, "content"),
        ("patch", {"mode": "replace", "path": "/tmp/file", "old_string": "before",
                   "new_string": "after" + MARKER}, "new_string"),
        ("patch", {"mode": "patch", "patch": "*** Begin Patch\n" + MARKER}, "patch"),
        ("patch", {"mode": "replace", "old_string": "source" + MARKER,
                   "new_string": "replacement"}, "old_string"),
    ],
)
def test_truncated_payload_is_rejected_before_dispatch(name, args, field):
    with patch("model_tools.registry.dispatch") as dispatch:
        result = json.loads(model_tools.handle_function_call(name, args))
    dispatch.assert_not_called()
    assert f"{name}.{field}" in result["error"]
    assert "not executed" in result["error"]
    assert "original source" in result["error"]


def test_truncated_write_never_creates_file(tmp_path):
    target = tmp_path / "not-created.txt"
    result = json.loads(model_tools.handle_function_call(
        "write_file", {"path": str(target), "content": "unfinished" + MARKER}))
    assert "not executed" in result["error"]
    assert not target.exists()


def test_complete_write_still_creates_file(tmp_path):
    target = tmp_path / "complete.txt"
    result = json.loads(model_tools.handle_function_call(
        "write_file", {"path": str(target), "content": "complete payload"}))
    assert "error" not in result
    assert target.read_text() == "complete payload"


def test_nonfinal_marker_inside_code_is_not_blocked():
    code = "marker = '...[truncated]'\nprint(marker)"
    with patch("model_tools.registry.dispatch", return_value='{"ok":true}') as dispatch:
        assert json.loads(model_tools.handle_function_call("execute_code", {"code": code})) == {"ok": True}
    dispatch.assert_called_once()


def test_read_only_search_can_look_for_marker():
    with patch("model_tools.registry.dispatch", return_value='{"ok":true}') as dispatch:
        result = model_tools.handle_function_call("search_files", {"pattern": MARKER})
    assert json.loads(result) == {"ok": True}
    dispatch.assert_called_once()


def test_request_middleware_cannot_introduce_truncated_code():
    with (
        patch("model_tools._apply_request_middleware", return_value=(
            {"code": "bad" + MARKER}, {"code": "valid"}, [])),
        patch("model_tools.registry.dispatch") as dispatch,
    ):
        result = json.loads(model_tools.handle_function_call("execute_code", {"code": "valid"}))
    assert "not executed" in result["error"]
    dispatch.assert_not_called()


def test_pre_tool_hook_cannot_introduce_truncated_code():
    with (
        patch("model_tools._pre_dispatch_guards", return_value=({"code": "bad" + MARKER}, None)),
        patch("model_tools.registry.dispatch") as dispatch,
    ):
        result = json.loads(model_tools.handle_function_call(
            "execute_code", {"code": "valid"}, skip_tool_request_middleware=True))
    assert "not executed" in result["error"]
    dispatch.assert_not_called()


def test_execution_middleware_cannot_introduce_truncated_code():
    def rewrite(_name, _args, next_call, **_kwargs):
        return next_call({"code": "bad" + MARKER})

    with (
        patch("hermes_cli.middleware.run_tool_execution_middleware", side_effect=rewrite),
        patch("model_tools.registry.dispatch") as dispatch,
    ):
        result = json.loads(model_tools.handle_function_call(
            "execute_code", {"code": "valid"}, skip_tool_request_middleware=True))
    assert "not executed" in result["error"]
    dispatch.assert_not_called()
