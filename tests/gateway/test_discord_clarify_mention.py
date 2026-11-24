"""Fork (area 9): blocking clarify prompts ping the requester on Discord.

The gateway resolves the mention into ``metadata["mention_text"]``
(``gateway/run_turn_runner.py``); these tests cover the adapter half, which
must prepend it to the same content-first prompt, pin ``allowed_mentions`` to
users only, refuse anything that is not a bare numeric user mention, and keep
the message inside Discord's 2,000-unit limit.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import utf16_len
from plugins.platforms.discord.adapter import DiscordAdapter

MENTION = "<@507285061595299871>"


def _adapter_and_capture(monkeypatch=None):
    if monkeypatch is not None:
        import plugins.platforms.discord.adapter as mod
        monkeypatch.setattr(mod.discord, "AllowedMentions", lambda **kw: SimpleNamespace(**kw), raising=False)
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    sent = {}

    async def fake_send(**kwargs):
        sent.update(kwargs)
        return SimpleNamespace(id=1234)

    channel = SimpleNamespace(send=AsyncMock(side_effect=fake_send))
    adapter._client = SimpleNamespace(get_channel=lambda _c: channel, fetch_channel=AsyncMock())
    return adapter, sent


async def _send(adapter, question="Which route?", choices=("Claude Code", "Hermes subagents"), metadata=None):
    return await adapter.send_clarify(
        chat_id="555", question=question, choices=list(choices) if choices else None,
        clarify_id="cl1", session_key="discord:555", metadata=metadata,
    )


@pytest.mark.asyncio
async def test_clarify_prepends_metadata_mention_and_allows_user_ping(monkeypatch):
    adapter, sent = _adapter_and_capture(monkeypatch)
    result = await _send(adapter, metadata={"mention_text": MENTION, "thread_id": "9"})

    assert result.success is True
    assert sent["content"].startswith(f"{MENTION}\n")
    assert "Which route?" in sent["content"]
    assert "1. Claude Code" in sent["content"]
    assert sent["view"] is not None
    am = sent["allowed_mentions"]
    assert am.users is True and am.roles is False and am.everyone is False and am.replied_user is False


@pytest.mark.asyncio
async def test_open_ended_clarify_also_pings():
    adapter, sent = _adapter_and_capture()
    await _send(adapter, choices=None, metadata={"mention_text": MENTION})
    assert sent["content"].startswith(f"{MENTION}\n")
    assert "allowed_mentions" in sent


@pytest.mark.asyncio
async def test_clarify_without_mention_metadata_is_unchanged():
    adapter, sent = _adapter_and_capture()
    await _send(adapter, metadata={"thread_id": "9"})
    assert not sent["content"].startswith("<@")
    assert "allowed_mentions" not in sent


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", ["@everyone", "<@&123>", "hey <@123>", "<@123> <@456>", "<@abc>"])
async def test_unsafe_mention_metadata_never_pings(bad):
    adapter, sent = _adapter_and_capture()
    await _send(adapter, metadata={"mention_text": bad})
    assert not sent["content"].startswith(bad)
    assert "allowed_mentions" not in sent


@pytest.mark.asyncio
async def test_long_mention_bearing_prompt_fits_discord_limit():
    adapter, sent = _adapter_and_capture()
    await _send(adapter, question="x" * 3000, metadata={"mention_text": MENTION})
    assert sent["content"].startswith(f"{MENTION}\n")
    assert utf16_len(sent["content"]) <= adapter.MAX_MESSAGE_LENGTH
