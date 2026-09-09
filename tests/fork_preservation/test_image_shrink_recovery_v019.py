"""Tests for reactive image-shrink recovery.

Covers the full chain for Anthropic's 5 MB per-image ceiling (and any
future provider that returns an image-too-large error):

  1. agent/error_classifier.py: 400 with "image exceeds 5 MB maximum"
     gets FailoverReason.image_too_large, not context_overflow.
  2. run_agent._try_shrink_image_parts_in_messages mutates the API
     payload in-place, re-encoding native data: URL image parts to fit
     under 4 MB using vision_tools._resize_image_for_vision.

The end-to-end wiring in the retry loop is not unit-tested here — it's
covered by the live E2E in the PR description. These tests lock in the
two pieces that matter independently: the classifier signal and the
payload rewriter.
"""

from __future__ import annotations

# Fork-preservation residue extracted during the v2026.8.16 integration.
# These tests were present at fork head 896a5ea3b2 but were removed or
# reorganized upstream; keep them as behavior gates for retained features.

import base64
import sys
from types import SimpleNamespace


from agent.conversation_loop import _image_error_max_dimension
from agent.error_classifier import FailoverReason, classify_api_error


class _FakeApiError(Exception):
    """Stand-in for an openai.BadRequestError with status_code + body."""

    def __init__(self, status_code: int, message: str, body: dict | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body or {"error": {"message": message}}
        self.response = None  # required by some code paths


# ─── Classifier ──────────────────────────────────────────────────────────────


class TestImageTooLargeClassification:

    def test_generic_image_too_large_no_status(self):
        """No status_code path: message text alone triggers classification."""
        err = Exception("image too large for this endpoint")
        result = classify_api_error(err, provider="some-provider", model="some-model")
        assert result.reason == FailoverReason.image_too_large
        assert result.retryable is True

    def test_image_too_large_not_confused_with_context_overflow(self):
        """'image exceeds' must NOT be mis-classified as context_overflow.

        The context_overflow patterns include 'exceeds the limit' which is a
        superstring risk — verify the image-too-large check fires first.
        """
        err = _FakeApiError(
            status_code=400,
            message="image exceeds the limit for this model",
        )
        result = classify_api_error(err, provider="anthropic", model="claude-sonnet-4-6")
        assert result.reason == FailoverReason.image_too_large

    def test_regular_context_overflow_unaffected(self):
        """Context-overflow errors without image keywords still classify correctly."""
        err = _FakeApiError(
            status_code=400,
            message="prompt is too long: context length 300000 exceeds max of 200000",
        )
        result = classify_api_error(err, provider="anthropic", model="claude-sonnet-4-6")
        assert result.reason == FailoverReason.context_overflow

    def test_anthropic_many_image_dimension_limit(self):
        """OpenRouter-wrapped Anthropic many-image limits recover via shrink."""
        err = _FakeApiError(
            status_code=400,
            message=(
                "messages.21.content.43.image.source.base64.data: At least one "
                "of the image dimensions exceed max allowed size for many-image "
                "requests: 2000 pixels"
            ),
        )
        result = classify_api_error(err, provider="openrouter", model="anthropic/claude-opus-4.8")
        assert result.reason == FailoverReason.image_too_large
        assert result.retryable is True
        assert _image_error_max_dimension(err) == 2000


# ─── Shrink helper ───────────────────────────────────────────────────────────


def _big_png_data_url(size_kb: int) -> str:
    """Build a data URL with a plausible large base64 payload."""
    # Use real PNG header so MIME detection works; fill to target size.
    raw = b"\x89PNG\r\n\x1a\n" + b"X" * (size_kb * 1024)
    return "data:image/png;base64," + base64.b64encode(raw).decode("ascii")


def _install_fake_pillow(
    monkeypatch,
    size: tuple[int, int],
    *,
    shrunk_size: tuple[int, int] | None = None,
    sizes: list[tuple[int, int]] | None = None,
) -> None:
    """Install the tiny subset of Pillow used by the shrink preflight.

    The shrink helper decodes pixel dimensions twice for the dimension path:
    once on the *original* data URL (to decide it's oversized) and once on the
    *re-encoded* result (to confirm the downscale landed under the cap).  To
    model that honestly, ``_FakeImage`` can return a sequence of sizes across
    successive ``open()`` calls:

    * ``sizes=[...]``        — explicit per-call size list (clamped to last).
    * ``shrunk_size=(w, h)`` — shorthand for ``[size, shrunk_size]``: first
      decode is the oversized original, second is the in-cap re-encode.
    * neither                — every decode returns ``size`` (legacy behaviour).
    """
    call_count = {"n": 0}
    target_sizes = sizes or [
        size,
        shrunk_size if shrunk_size is not None else size,
    ]

    class _FakeImage:
        def __init__(self):
            self.size = target_sizes[min(call_count["n"], len(target_sizes) - 1)]
            call_count["n"] += 1

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

    class _FakeImageModule:
        @staticmethod
        def open(_data):
            return _FakeImage()

    monkeypatch.setitem(sys.modules, "PIL", SimpleNamespace(Image=_FakeImageModule))
    monkeypatch.setitem(sys.modules, "PIL.Image", _FakeImageModule)


def _make_agent():
    """Build a bare AIAgent for method-level testing, no provider setup."""
    from run_agent import AIAgent
    agent = object.__new__(AIAgent)
    agent.provider = "anthropic"
    agent.model = "claude-sonnet-4-6"
    return agent


class TestShrinkImagePartsHelper:

    def test_no_image_parts_returns_false(self):
        agent = _make_agent()
        msgs = [
            {"role": "user", "content": "plain text"},
            {"role": "assistant", "content": "ack"},
        ]
        assert agent._try_shrink_image_parts_in_messages(msgs) is False



    def test_many_image_dimension_limit_rewritten(self, monkeypatch):
        """A 2000px many-image rejection must shrink images below the cap."""
        agent = _make_agent()
        # Original decodes oversized (2501px); the re-encode decodes in-cap.
        _install_fake_pillow(monkeypatch, (2501, 100), shrunk_size=(1500, 60))
        oversized_for_many = _big_png_data_url(100)
        shrunk = "data:image/jpeg;base64," + "M" * 1000
        seen = {}

        def _fake_resize(path, mime_type=None, max_base64_bytes=None, max_dimension=None):
            seen["max_dimension"] = max_dimension
            return shrunk

        monkeypatch.setattr(
            "tools.vision_tools._resize_image_for_vision",
            _fake_resize,
            raising=False,
        )

        msgs = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": oversized_for_many}},
            ],
        }]
        changed = agent._try_shrink_image_parts_in_messages(
            msgs,
            max_dimension=2000,
        )
        assert changed is True
        assert seen["max_dimension"] == 2000
        assert msgs[0]["content"][0]["image_url"]["url"] == shrunk


    def test_oversized_input_image_string_shape_rewritten(self, monkeypatch):
        """OpenAI Responses shape: {type: input_image, image_url: "data:..."}."""
        agent = _make_agent()
        oversized_url = _big_png_data_url(5000)
        shrunk = "data:image/jpeg;base64," + "B" * 1000

        monkeypatch.setattr(
            "tools.vision_tools._resize_image_for_vision",
            lambda *a, **kw: shrunk,
            raising=False,
        )

        msgs = [{
            "role": "user",
            "content": [
                {"type": "input_text", "text": "look"},
                {"type": "input_image", "image_url": oversized_url},
            ],
        }]
        changed = agent._try_shrink_image_parts_in_messages(msgs)
        assert changed is True
        assert msgs[0]["content"][1]["image_url"] == shrunk






    def test_shrink_that_makes_it_bigger_rejected(self, monkeypatch):
        """If the 'shrink' somehow produces a larger payload, skip it."""
        agent = _make_agent()
        oversized_url = _big_png_data_url(5000)
        even_bigger = "data:image/png;base64," + "Z" * (10 * 1024 * 1024)

        monkeypatch.setattr(
            "tools.vision_tools._resize_image_for_vision",
            lambda *a, **kw: even_bigger,
            raising=False,
        )

        msgs = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": oversized_url}},
            ],
        }]
        assert agent._try_shrink_image_parts_in_messages(msgs) is False
        # Original URL still in place, not replaced by the bigger one.
        assert msgs[0]["content"][0]["image_url"]["url"] == oversized_url


    # ------------------------------------------------------------------
    # #48013: the dimension path must accept a pixel-correct downscale even
    # when the re-encoded PNG grew in bytes.  Before the fix, the byte gate
    # (`len(resized) >= len(url)`) discarded the dimension-correct result and
    # left the image oversized, bricking the session on the Anthropic
    # many-image 2000px path.
    # ------------------------------------------------------------------





    def test_byte_oversized_with_no_dim_cap_accepts_byte_shrink(self, monkeypatch):
        """Bytes path with the default 8000px cap still accepts a byte shrink.

        Guards the fix above against over-reach: when no tight dimension cap is
        active (default 8000px) and the byte-shrunk re-encode is comfortably
        within it, the byte path must keep accepting on byte-shrinkage alone.
        """
        agent = _make_agent()
        # Byte path → single _decode_pixels call on the resized blob; report
        # in-cap dims so the byte-shrink is accepted under the default 8000 cap.
        _install_fake_pillow(monkeypatch, (1250, 50), sizes=[(1250, 50)])
        oversized_url = _big_png_data_url(5000)
        shrunk = "data:image/jpeg;base64," + "L" * 1000

        monkeypatch.setattr(
            "tools.vision_tools._resize_image_for_vision",
            lambda *a, **kw: shrunk,
            raising=False,
        )

        msgs = [{
            "role": "user",
            "content": [
                {"type": "image_url", "image_url": {"url": oversized_url}},
            ],
        }]
        # Default cap (8000) — no explicit max_dimension passed.
        assert agent._try_shrink_image_parts_in_messages(msgs) is True
        assert msgs[0]["content"][0]["image_url"]["url"] == shrunk
