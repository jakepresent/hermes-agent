"""Discord voice notes must bypass text batching so busy steer can transcribe them."""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from gateway.config import PlatformConfig
from gateway.platforms.base import MessageType
from plugins.platforms.discord.adapter import DiscordAdapter


class _FakeVoiceAttachment:
    content_type = "audio/ogg"
    filename = "voice.ogg"
    url = "https://cdn.discordapp.example/voice.ogg"
    size = 1234
    duration = 1.0
    waveform = b"abc"

    def is_voice_message(self):
        return True

    async def read(self):
        return b"not real ogg bytes"


@pytest.mark.asyncio
async def test_discord_voice_note_dispatches_immediately_not_text_batch(monkeypatch):
    adapter = DiscordAdapter(PlatformConfig(enabled=True, token="***"))
    adapter._client = SimpleNamespace(user=SimpleNamespace(id=999))
    adapter._text_batch_delay_seconds = 1.0
    adapter._text_batch_split_delay_seconds = 1.0
    adapter._threads = SimpleNamespace(mark=lambda *_args, **_kwargs: None)
    adapter._nonconversational_messages = set()
    adapter._last_self_message_id = {}
    adapter._voice_text_channels = {}
    adapter._pending_text_batches = {}
    adapter._pending_text_batch_tasks = {}
    adapter._resolve_channel_skills = lambda *_args, **_kwargs: None
    adapter._resolve_channel_prompt = lambda *_args, **_kwargs: None
    adapter._discord_history_backfill = lambda: False
    adapter._discord_free_response_channels = lambda: {"*"}
    adapter._discord_require_mention = lambda: False
    adapter._discord_thread_require_mention = lambda: False
    adapter._discord_allow_any_attachment = lambda: False
    adapter._cache_discord_audio = AsyncMock(return_value="/tmp/voice.ogg")
    adapter.handle_message = AsyncMock()
    adapter._enqueue_text_event = lambda _event: (_ for _ in ()).throw(
        AssertionError("voice events must not enter text batching")
    )

    channel = SimpleNamespace(id=123, name="random", guild=SimpleNamespace(id=777, name="Guild"))
    message = SimpleNamespace(
        content="",
        clean_content="",
        attachments=[_FakeVoiceAttachment()],
        message_snapshots=[],
        mentions=[],
        channel=channel,
        author=SimpleNamespace(id=456, display_name="Jake", name="jake", bot=False),
        id=111,
        guild=channel.guild,
        type=SimpleNamespace(default="default"),
        reference=None,
        created_at=None,
    )

    await adapter._handle_message(message)

    adapter.handle_message.assert_awaited_once()
    event = adapter.handle_message.await_args.args[0]
    assert event.message_type == MessageType.VOICE
    assert event.media_urls == ["/tmp/voice.ogg"]
    assert event.media_types == ["audio/ogg"]
