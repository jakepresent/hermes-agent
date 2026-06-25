"""Voice transcription should not echo the raw transcript back to chat."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from gateway.config import GatewayConfig, Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.session import SessionSource


class _RecordingAdapter:
    def __init__(self):
        self.sent = []

    async def send(self, chat_id, text, metadata=None, **kwargs):
        self.sent.append((chat_id, text, metadata, kwargs))
        return SimpleNamespace(success=True, message_id="sent-1")


@pytest.mark.asyncio
async def test_prepare_inbound_voice_transcribes_without_echoing_raw_transcript(tmp_path):
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    adapter = _RecordingAdapter()
    runner.config = GatewayConfig(
        stt_enabled=True,
        persist_voice_transcripts=True,
        voice_transcripts_dir=str(tmp_path),
    )
    runner.adapters = {Platform.DISCORD: adapter}
    runner._model = "test-model"
    runner._base_url = ""
    runner._has_setup_skill = lambda: False
    runner._pending_native_image_paths_by_session = {}
    runner._session_key_for_source = lambda source: f"{source.platform.value}:{source.chat_id}"
    runner._consume_pending_native_image_paths = lambda session_key: []

    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        chat_type="channel",
        user_id="456",
        user_name="Jake",
    )
    event = MessageEvent(
        text="(The user sent a message with no text content)",
        message_type=MessageType.VOICE,
        source=source,
        media_urls=["/tmp/voice.ogg"],
        media_types=["audio/ogg"],
    )

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={
            "success": True,
            "transcript": "please save this but do not echo it",
            "provider": "local_command",
        },
    ):
        result = await runner._prepare_inbound_message_text(
            event=event,
            source=source,
            history=[],
        )

    assert result is not None
    assert "please save this but do not echo it" in result
    assert adapter.sent == []
    transcript_files = list(tmp_path.glob("*.md"))
    assert len(transcript_files) == 1
    assert "please save this but do not echo it" in transcript_files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_dequeued_voice_transcribes_without_echoing_raw_transcript():
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    adapter = _RecordingAdapter()
    runner.config = GatewayConfig(stt_enabled=True)
    runner.adapters = {Platform.DISCORD: adapter}
    runner._has_setup_skill = lambda: False

    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="123",
        chat_type="channel",
        thread_id="789",
    )
    event = MessageEvent(
        text="",
        message_type=MessageType.VOICE,
        source=source,
        media_urls=["/tmp/pending-voice.ogg"],
        media_types=["audio/ogg"],
    )
    adapter.get_pending_message = lambda session_key: event

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={
            "success": True,
            "transcript": "queued voice should not echo",
            "provider": "local_command",
        },
    ), patch.object(
        GatewayRunner,
        "_persist_voice_transcript",
        new=AsyncMock(),
    ):
        result = await runner._dequeue_pending_with_transcription(
            adapter,
            "discord:123",
            source,
        )

    assert result is not None
    assert "queued voice should not echo" in result
    assert adapter.sent == []
