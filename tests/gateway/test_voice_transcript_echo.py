"""Voice transcript echo is configurable independently of STT persistence."""

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


def _new_runner(config):
    from gateway.run import GatewayRunner

    runner = GatewayRunner.__new__(GatewayRunner)
    adapter = _RecordingAdapter()
    runner.config = config
    runner.adapters = {Platform.DISCORD: adapter}
    runner._model = "test-model"
    runner._base_url = ""
    runner._has_setup_skill = lambda: False
    runner._pending_native_image_paths_by_session = {}
    runner._session_key_for_source = lambda source: f"{source.platform.value}:{source.chat_id}"
    runner._consume_pending_native_image_paths = lambda session_key: []
    return runner, adapter


def _voice_event(source, *, path="/tmp/voice.ogg"):
    return MessageEvent(
        text="(The user sent a message with no text content)",
        message_type=MessageType.VOICE,
        source=source,
        media_urls=[path],
        media_types=["audio/ogg"],
    )


def _discord_source(**kwargs):
    return SessionSource(
        platform=kwargs.pop("platform", Platform.DISCORD),
        chat_id=kwargs.pop("chat_id", "123"),
        chat_type=kwargs.pop("chat_type", "channel"),
        user_id=kwargs.pop("user_id", "456"),
        user_name=kwargs.pop("user_name", "Jake"),
        **kwargs,
    )


@pytest.mark.asyncio
async def test_prepare_inbound_voice_transcribes_without_echoing_raw_transcript_by_default(tmp_path):
    runner, adapter = _new_runner(
        GatewayConfig(
            stt_enabled=True,
            persist_voice_transcripts=True,
            voice_transcripts_dir=str(tmp_path),
        )
    )
    source = _discord_source()

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={
            "success": True,
            "transcript": "please save this but do not echo it",
            "provider": "local_command",
        },
    ):
        result = await runner._prepare_inbound_message_text(
            event=_voice_event(source),
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
async def test_prepare_inbound_voice_echoes_raw_transcript_when_enabled(tmp_path):
    runner, adapter = _new_runner(
        GatewayConfig(
            stt_enabled=True,
            persist_voice_transcripts=True,
            voice_transcripts_dir=str(tmp_path),
            echo_voice_transcripts=True,
        )
    )
    source = _discord_source()

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={
            "success": True,
            "transcript": "please save this and echo it",
            "provider": "local_command",
        },
    ):
        result = await runner._prepare_inbound_message_text(
            event=_voice_event(source),
            source=source,
            history=[],
        )

    assert result is not None
    assert "please save this and echo it" in result
    assert adapter.sent == [
        ("123", '🎙️ "please save this and echo it"', None, {})
    ]
    transcript_files = list(tmp_path.glob("*.md"))
    assert len(transcript_files) == 1
    assert "please save this and echo it" in transcript_files[0].read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_dequeued_voice_transcribes_without_echoing_raw_transcript_by_default():
    from gateway.run import GatewayRunner

    runner, adapter = _new_runner(GatewayConfig(stt_enabled=True))
    source = _discord_source(thread_id="789")
    event = _voice_event(source, path="/tmp/pending-voice.ogg")
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
        result, transcripts = await runner._transcribe_and_echo_pending_voice(
            event,
            adapter,
            source,
            event.text,
            log_context="Voice-drain",
            metadata={"thread_id": source.thread_id},
        )

    assert result is not None
    assert "queued voice should not echo" in result
    assert transcripts == ["queued voice should not echo"]
    assert adapter.sent == []


@pytest.mark.asyncio
async def test_dequeued_voice_echoes_raw_transcript_when_enabled():
    from gateway.run import GatewayRunner

    runner, adapter = _new_runner(GatewayConfig(stt_enabled=True, echo_voice_transcripts=True))
    source = _discord_source(thread_id="789")
    event = _voice_event(source, path="/tmp/pending-voice.ogg")
    adapter.get_pending_message = lambda session_key: event

    with patch(
        "tools.transcription_tools.transcribe_audio",
        return_value={
            "success": True,
            "transcript": "queued voice should echo",
            "provider": "local_command",
        },
    ), patch.object(
        GatewayRunner,
        "_persist_voice_transcript",
        new=AsyncMock(),
    ):
        result, transcripts = await runner._transcribe_and_echo_pending_voice(
            event,
            adapter,
            source,
            event.text,
            log_context="Voice-drain",
            metadata={"thread_id": source.thread_id},
        )

    assert result is not None
    assert "queued voice should echo" in result
    assert transcripts == ["queued voice should echo"]
    assert adapter.sent == [
        ("123", '🎙️ "queued voice should echo"', {"thread_id": "789"}, {})
    ]


def test_gateway_config_loads_echo_voice_transcripts_from_nested_stt():
    cfg = GatewayConfig.from_dict({"stt": {"echo_voice_transcripts": True}})
    assert cfg.echo_voice_transcripts is True


def test_gateway_config_echo_voice_transcripts_default_false():
    cfg = GatewayConfig.from_dict({"stt": {}})
    assert cfg.echo_voice_transcripts is False
