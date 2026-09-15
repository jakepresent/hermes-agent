import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from gateway.config import Platform
from gateway.run import GatewayRunner
from gateway.session import SessionSource


def _source():
    return SessionSource(platform=Platform.TELEGRAM, chat_id="12345", chat_type="dm")


def _runner(strip_fillers=True):
    runner = object.__new__(GatewayRunner)
    runner.config = SimpleNamespace(
        stt_echo_transcripts=True,
        stt_echo_strip_fillers=strip_fillers,
        stt_echo_filler_words=None,
    )
    return runner


@pytest.mark.asyncio
async def test_echo_strips_fillers_when_enabled():
    adapter = SimpleNamespace(send=AsyncMock())
    runner = _runner(strip_fillers=True)
    await runner._echo_stt_transcripts(adapter, _source(), ["Um, I think we should go, uh, left."])
    adapter.send.assert_awaited_once_with("12345", '🎙️ "I think we should go, left."', metadata=None)


@pytest.mark.asyncio
async def test_echo_passthrough_when_disabled():
    adapter = SimpleNamespace(send=AsyncMock())
    runner = _runner(strip_fillers=False)
    await runner._echo_stt_transcripts(adapter, _source(), ["Um, I think we should go."])
    adapter.send.assert_awaited_once_with("12345", '🎙️ "Um, I think we should go."', metadata=None)


@pytest.mark.asyncio
async def test_echo_uses_configured_word_list():
    adapter = SimpleNamespace(send=AsyncMock())
    runner = _runner(strip_fillers=True)
    runner.config.stt_echo_filler_words = ["you know"]
    await runner._echo_stt_transcripts(adapter, _source(), ["I like this and, you know, maybe."])
    adapter.send.assert_awaited_once_with("12345", '🎙️ "I like this and, maybe."', metadata=None)


@pytest.mark.asyncio
async def test_echo_failure_still_non_fatal():
    adapter = SimpleNamespace(send=AsyncMock(side_effect=RuntimeError("send blew up")))
    runner = _runner(strip_fillers=True)
    await runner._echo_stt_transcripts(adapter, _source(), ["um hello"])
    adapter.send.assert_awaited_once()
