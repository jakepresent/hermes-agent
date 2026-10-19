"""Jake fork: inbound voice transcripts are saved to daily markdown logs (manifest §20).

These drive the REAL config loader and the REAL transcription loop, so a future rebuild that keeps
the helper but drops the call site or the config wiring fails here. That gap (helper and config keys
both gone, keys still set in config.yaml) is how this feature silently disappeared once before.
"""
from __future__ import annotations

from unittest.mock import patch

import pytest

from gateway.config import GatewayConfig
from gateway.run import GatewayRunner


def _runner(cfg: GatewayConfig) -> GatewayRunner:
    runner = GatewayRunner.__new__(GatewayRunner)
    runner.config = cfg
    runner._has_setup_skill = lambda: False
    return runner


def test_nested_stt_config_reaches_gateway_config(tmp_path):
    cfg = GatewayConfig.from_dict({"stt": {"persist_voice_transcripts": True, "voice_transcripts_dir": str(tmp_path)}})

    assert cfg.persist_voice_transcripts is True
    assert cfg.voice_transcripts_dir == str(tmp_path)
    assert cfg.to_dict()["persist_voice_transcripts"] is True
    assert GatewayConfig.from_dict(cfg.to_dict()).voice_transcripts_dir == str(tmp_path)


def test_persistence_defaults_off():
    cfg = GatewayConfig.from_dict({})
    assert cfg.persist_voice_transcripts is False
    assert cfg.voice_transcripts_dir == ""


@pytest.mark.asyncio
async def test_successful_transcription_is_appended_to_daily_log(tmp_path):
    runner = _runner(GatewayConfig.from_dict(
        {"stt": {"persist_voice_transcripts": True, "voice_transcripts_dir": str(tmp_path)}}))

    with patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": True, "transcript": "remember to buy film", "provider": "local_command"}):
        text, transcripts = await runner._enrich_message_with_transcription("caption", ["/tmp/voice-note.ogg"])
        await runner._enrich_message_with_transcription("", ["/tmp/second.ogg"])

    assert transcripts == ["remember to buy film"]
    assert '"remember to buy film"' in text
    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    body = files[0].read_text(encoding="utf-8")
    assert body.startswith("# Voice transcripts - ")
    assert body.count("# Voice transcripts - ") == 1  # header once, entries appended
    assert body.count("- Transcript: remember to buy film") == 2
    assert "`voice-note.ogg`" in body and "`second.ogg`" in body


@pytest.mark.asyncio
async def test_disabled_flag_writes_nothing(tmp_path):
    out = tmp_path / "transcripts"
    runner = _runner(GatewayConfig.from_dict({"stt": {"voice_transcripts_dir": str(out)}}))

    with patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": True, "transcript": "should not save", "provider": "local_command"}):
        await runner._enrich_message_with_transcription("", ["/tmp/voice.ogg"])

    assert not out.exists()


@pytest.mark.asyncio
async def test_failed_or_empty_transcription_writes_nothing(tmp_path):
    runner = _runner(GatewayConfig.from_dict(
        {"stt": {"persist_voice_transcripts": True, "voice_transcripts_dir": str(tmp_path)}}))

    with patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": True, "transcript": "   ", "provider": "local_command"}):
        await runner._enrich_message_with_transcription("", ["/tmp/silence.ogg"])
    with patch("tools.transcription_tools.transcribe_audio", return_value={"success": False, "error": "boom"}), \
         patch("tools.transcription_tools.transcribe_audio_local_fallback", return_value={"success": False}):
        await runner._enrich_message_with_transcription("", ["/tmp/broken.ogg"])

    assert list(tmp_path.glob("*.md")) == []


@pytest.mark.asyncio
async def test_disk_failure_never_costs_the_user_their_turn(tmp_path):
    blocker = tmp_path / "not-a-dir"
    blocker.write_text("x", encoding="utf-8")
    runner = _runner(GatewayConfig.from_dict(
        {"stt": {"persist_voice_transcripts": True, "voice_transcripts_dir": str(blocker / "sub")}}))

    with patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": True, "transcript": "still delivered", "provider": "local_command"}):
        text, transcripts = await runner._enrich_message_with_transcription("", ["/tmp/voice.ogg"])

    assert transcripts == ["still delivered"]
    assert '"still delivered"' in text


@pytest.mark.asyncio
async def test_busy_steer_voice_path_also_persists(tmp_path):
    """Mid-run voice steering transcribes through the same pending-event path; it must save too."""
    runner = _runner(GatewayConfig.from_dict(
        {"stt": {"persist_voice_transcripts": True, "voice_transcripts_dir": str(tmp_path)}}))

    class _Event:
        text = ""
        media_urls = ["/tmp/steer.ogg"]

    with patch("gateway.run._event_media_is_stt_input", return_value=True), \
         patch("tools.transcription_tools.transcribe_audio",
               return_value={"success": True, "transcript": "change of plan", "provider": "local_command"}):
        _, transcripts = await runner._transcribe_pending_audio_event_once(_Event())

    assert transcripts == ["change of plan"]
    assert "- Transcript: change of plan" in next(tmp_path.glob("*.md")).read_text(encoding="utf-8")
