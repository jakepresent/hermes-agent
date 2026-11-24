"""Fork (§3): session-local 413 image-shrink cache.

Upstream shrinks an oversized inline image once per turn, on the request clone only, so the next
model/tool round in the same session rebuilds the original image and re-earns the identical
rejection. The fork remembers a provider-confirmed rewrite per (provider, model) and replays it at
the single request seam (``build_api_kwargs``) without touching durable history.
"""

import base64
import copy
import io

import pytest

from agent import chat_completion_helpers
from agent.vision_message_prep import VisionMessagePrepMixin

PIL = pytest.importorskip("PIL.Image")


def _wide_png_data_url() -> str:
    buf = io.BytesIO()
    PIL.new("RGB", (9000, 900), (200, 10, 10)).save(buf, format="PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


class _Agent(VisionMessagePrepMixin):
    def __init__(self, provider="anthropic", model="claude-x"):
        self.provider, self.model = provider, model


@pytest.fixture(scope="module")
def big_url():
    return _wide_png_data_url()


def _history(url):
    data = url.partition(",")[2]
    return [
        {"role": "user", "content": [{"type": "text", "text": "look"}, {"type": "image_url", "image_url": {"url": url}}]},
        {"role": "user", "content": [{"type": "image", "source": {"type": "base64", "media_type": "image/png", "data": data}}]},
    ]


def test_confirmed_rewrite_is_replayed_on_later_rounds_without_mutating_history(big_url):
    agent, history = _Agent(), _history(big_url)
    pristine = copy.deepcopy(history)

    first = copy.deepcopy(history)
    assert agent._try_shrink_image_parts_in_messages(first, max_dimension=8000) is True
    shrunk_url = first[0]["content"][1]["image_url"]["url"]
    assert shrunk_url != big_url

    later = copy.deepcopy(history)
    assert agent._apply_cached_image_shrinks(later) == 2
    assert later[0]["content"][1]["image_url"]["url"] == shrunk_url
    assert later[0]["content"][0] == {"type": "text", "text": "look"}
    assert later[1]["content"][0]["source"]["data"] != history[1]["content"][0]["source"]["data"]
    assert history == pristine


def test_cache_is_scoped_to_provider_and_model(big_url):
    agent, history = _Agent(), _history(big_url)
    assert agent._try_shrink_image_parts_in_messages(copy.deepcopy(history), max_dimension=8000) is True
    agent.model = "other-model"
    assert agent._apply_cached_image_shrinks(copy.deepcopy(history)) == 0
    agent.model = "claude-x"
    assert agent._apply_cached_image_shrinks(copy.deepcopy(history)) == 2


def test_build_api_kwargs_is_the_replay_seam(big_url, monkeypatch):
    agent, history = _Agent(), _history(big_url)
    first = copy.deepcopy(history)
    assert agent._try_shrink_image_parts_in_messages(first, max_dimension=8000) is True
    assert first[0]["content"][1]["image_url"]["url"] != big_url
    monkeypatch.setattr(chat_completion_helpers, "_build_api_kwargs_for_mode",
                        lambda _agent, msgs, _tools: {"messages": msgs})
    kwargs = chat_completion_helpers.build_api_kwargs(agent, copy.deepcopy(history))
    assert kwargs["messages"][0]["content"][1]["image_url"]["url"] == first[0]["content"][1]["image_url"]["url"]
