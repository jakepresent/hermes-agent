"""Tests for durable voice transcript persistence."""

import asyncio
from typing import Any
from types import SimpleNamespace

from gateway.run import GatewayRunner


def _run(coro):
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


class _Stub:
    config: Any = None
    _persist_voice_transcript = GatewayRunner._persist_voice_transcript


def test_persist_voice_transcript_writes_daily_markdown(tmp_path):
    runner = _Stub()
    runner.config = SimpleNamespace(
        persist_voice_transcripts=True,
        voice_transcripts_dir=str(tmp_path),
    )

    _run(runner._persist_voice_transcript("remember to buy film", "/tmp/voice-note.ogg"))

    files = list(tmp_path.glob("*.md"))
    assert len(files) == 1
    content = files[0].read_text(encoding="utf-8")
    assert "# Voice transcripts - " in content
    assert "remember to buy film" in content
    assert "voice-note.ogg" in content


def test_persist_voice_transcript_respects_disabled_flag(tmp_path):
    runner = _Stub()
    runner.config = SimpleNamespace(
        persist_voice_transcripts=False,
        voice_transcripts_dir=str(tmp_path),
    )

    _run(runner._persist_voice_transcript("should not save", "/tmp/voice-note.ogg"))

    assert list(tmp_path.glob("*.md")) == []
