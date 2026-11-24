"""Fork (§4): Aside-compatible ACP behavior on upstream's gateway-attached ACP adapter.

Aside runs ``hermes acp`` over SSH from macOS. It sends its own host's cwd (``/Users/...``), which
does not exist in WSL, and it sets reasoning effort with ``session/set_config_option`` id
``thought_level``, skipping ids the agent does not advertise.
"""
import os

import pytest

from acp_adapter import session as acp_session
from tests.acp_adapter.test_gateway_sessions import editor

REMOTE_HOST_CWD = "/Users/aside-remote-host-not-on-this-machine"


def test_missing_remote_posix_cwd_falls_back_to_home():
    assert not os.path.isdir(REMOTE_HOST_CWD)
    assert acp_session._tool_cwd_for_this_host(REMOTE_HOST_CWD) == os.path.expanduser("~")


def test_existing_cwd_is_kept(tmp_path):
    assert acp_session._tool_cwd_for_this_host(str(tmp_path)) == str(tmp_path)


def test_translated_windows_drive_is_trusted_without_existence_check(monkeypatch):
    monkeypatch.setattr(acp_session, "_translate_acp_cwd", lambda cwd: "/mnt/e/Projects" if cwd == r"E:\Projects" else cwd)
    assert acp_session._tool_cwd_for_this_host(r"E:\Projects") == "/mnt/e/Projects"


@pytest.mark.platforms("linux")
@pytest.mark.asyncio
async def test_remote_host_session_opens_and_thought_level_is_settable_by_id(daemon, tmp_path):
    async with editor(daemon, tmp_path) as acp:
        await acp.rpc("initialize", protocolVersion=1, clientCapabilities={})
        created = await acp.rpc("session/new", cwd=REMOTE_HOST_CWD, mcpServers=[])
        assert "result" in created, created
        result = created["result"]
        options = {option["id"]: option for option in result["configOptions"]}
        assert "thought_level" in options, options
        assert options["thought_level"]["category"] == "thought_level"
        assert "high" in {choice["value"] for choice in options["thought_level"]["options"]}

        changed = await acp.rpc("session/set_config_option", sessionId=result["sessionId"],
                                configId="thought_level", value="high")
        assert "result" in changed, changed
        picked = {option["id"]: option["currentValue"] for option in changed["result"]["configOptions"]}
        assert picked["thought_level"] == "high"
