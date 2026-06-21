from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from plugins.platforms.discord.adapter import DiscordAdapter


@pytest.mark.asyncio
async def test_exec_approval_prompt_asks_explicit_question_and_shows_request_context():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))

    sent = {}

    async def fake_send(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(id=1234)

    channel = SimpleNamespace(send=AsyncMock(side_effect=fake_send))
    adapter._client = SimpleNamespace(
        get_channel=lambda _chat_id: channel,
        fetch_channel=AsyncMock(),
    )

    command = "python scripts/deploy.py --env prod --force"
    result = await adapter.send_exec_approval(
        chat_id="555",
        command=command,
        session_key="discord:555",
        description="script execution via -c flag",
    )

    assert result.success is True
    assert "embed" not in sent

    prompt_text = sent["content"]

    assert "Do you want Hermes to run this command?" in prompt_text
    assert "Requested command" in prompt_text
    assert command in prompt_text
    assert "Reason" in prompt_text
    assert "script execution via -c flag" in prompt_text


@pytest.mark.asyncio
async def test_exec_approval_prompt_can_ping_for_long_turn_attention():
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))

    sent = {}

    async def fake_send(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(id=1234)

    channel = SimpleNamespace(send=AsyncMock(side_effect=fake_send))
    adapter._client = SimpleNamespace(
        get_channel=lambda _chat_id: channel,
        fetch_channel=AsyncMock(),
    )

    result = await adapter.send_exec_approval(
        chat_id="555",
        command="rm -rf /tmp/example",
        session_key="discord:555",
        description="destructive command",
        metadata={"mention_text": "<@123456789>"},
    )

    assert result.success is True
    assert sent["content"].startswith("<@123456789>\n")
    assert "Do you want Hermes to run this command?" in sent["content"]
    assert "rm -rf /tmp/example" in sent["content"]
    assert "destructive command" in sent["content"]
    assert "embed" not in sent
