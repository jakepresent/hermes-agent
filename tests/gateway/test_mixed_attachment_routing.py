"""Regression tests for mixed image + document gateway attachment routing.

Two complementary layers are covered here:

* End-to-end (fork §13 / commit f93fd7510): a PHOTO event that mixes a real
  image with a document must buffer only the image for native vision and route
  the document to the readable cached-file context-note path — never send the
  document bytes to the vision endpoint (which 400s the whole turn).

* Per-attachment classification (upstream #25935): the ``_event_media_is_*``
  helpers classify each attachment by its OWN mimetype, only falling back to
  the message-level type (PHOTO/VOICE/AUDIO/VIDEO) when the per-file mimetype is
  unknown (empty) — platforms that don't populate media_types.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from gateway.config import Platform
from gateway.platforms.base import MessageEvent, MessageType
from gateway.run import (
    GatewayRunner,
    _build_media_placeholder,
    _event_media_is_audio,
    _event_media_is_image,
    _event_media_is_video,
)
from gateway.session import SessionSource


class _DummySessionStore:
    def _generate_session_key(self, source: SessionSource) -> str:
        return f"{source.platform.value}:{source.chat_id}:{source.user_id}"


@pytest.mark.asyncio
async def test_prepare_inbound_mixed_image_and_log_only_buffers_image(tmp_path, monkeypatch):
    """PHOTO events can include documents; only real images should be buffered."""
    img = tmp_path / "screen.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"X" * 8)
    log = tmp_path / "doc_123_auto_film_crop.log"
    log.write_text("[2026-07-04] batch preview preflight failed", encoding="utf-8")

    runner = object.__new__(GatewayRunner)
    runner.__dict__["config"] = SimpleNamespace(group_sessions_per_user=True, thread_sessions_per_user=False)
    runner.__dict__["session_store"] = _DummySessionStore()
    runner.adapters = {}
    runner._pending_native_image_paths_by_session = {}
    runner._session_db = None

    monkeypatch.setattr(runner, "_decide_image_input_mode", lambda **_: "native")

    source = SessionSource(
        platform=Platform.DISCORD,
        chat_id="c1",
        chat_type="group",
        user_id="u1",
        user_name="Jake",
    )
    event = MessageEvent(
        text="what happened?",
        message_type=MessageType.PHOTO,
        source=source,
        media_urls=[str(img), str(log)],
        media_types=["image/png", "text/plain"],
    )

    message_text = await runner._prepare_inbound_message_text(
        event=event,
        source=source,
        history=[],
    )

    session_key = runner._session_key_for_source(source)
    assert runner._consume_pending_native_image_paths(session_key) == [str(img)]
    assert str(log) not in runner._consume_pending_native_image_paths(session_key)
    assert message_text is not None
    assert "[The user sent a text document: 'auto_film_crop.log'." in message_text
    assert "what happened?" in message_text


def test_prepare_inbound_image_classifier_rejects_text_document():
    from gateway.run import _is_image_media_attachment

    assert _is_image_media_attachment("image/png", "/tmp/screen.png") is True
    assert _is_image_media_attachment("image", "/cache/dingtalk-image") is True
    assert _is_image_media_attachment("text/plain", "/tmp/auto_film_crop.log") is False
    assert _is_image_media_attachment("application/json", "/tmp/state.json") is False
    assert _is_image_media_attachment("application/octet-stream", "/tmp/photo.png") is True
    assert _is_image_media_attachment("application/octet-stream", "/tmp/trace.log") is False


def test_media_placeholder_does_not_label_text_document_as_image():
    event = MessageEvent(
        text="",
        message_type=MessageType.PHOTO,
        media_urls=["/tmp/screen.png", "/tmp/auto_film_crop.log"],
        media_types=["image/png", "text/plain"],
    )

    placeholder = _build_media_placeholder(event)

    assert "[User sent an image: /tmp/screen.png]" in placeholder
    assert "[User sent a file: /tmp/auto_film_crop.log]" in placeholder
    assert "[User sent an image: /tmp/auto_film_crop.log]" not in placeholder


def _evt(media_urls, media_types, message_type):
    return SimpleNamespace(
        media_urls=media_urls,
        media_types=media_types,
        message_type=message_type,
    )


# ─── per-attachment classification helpers (upstream #25935) ─────────────────


def test_image_trusts_own_mime_over_photo_message_type():
    evt = _evt(["/c/pic.png", "/c/brief.md"], ["image/png", "text/markdown"], MessageType.PHOTO)
    assert _event_media_is_image(evt, 0) is True
    # The document must NOT be promoted to an image by the PHOTO fallback.
    assert _event_media_is_image(evt, 1) is False


def test_unknown_mime_falls_back_to_photo_message_type():
    # Platforms that don't populate media_types rely on the message-level type.
    evt = _evt(["/c/photo.jpg"], [""], MessageType.PHOTO)
    assert _event_media_is_image(evt, 0) is True


def test_audio_classified_per_attachment():
    evt = _evt(["/c/clip.ogg", "/c/shot.png"], ["audio/ogg", "image/png"], MessageType.PHOTO)
    assert _event_media_is_audio(evt, 0) is True
    assert _event_media_is_audio(evt, 1) is False
    assert _event_media_is_image(evt, 1) is True


def test_video_classified_per_attachment():
    evt = _evt(["/c/movie.mp4", "/c/notes.md"], ["video/mp4", "text/markdown"], MessageType.PHOTO)
    assert _event_media_is_video(evt, 0) is True
    assert _event_media_is_video(evt, 1) is False


# ─── _build_media_placeholder ────────────────────────────────────────────────


def test_placeholder_document_in_photo_message_is_not_an_image():
    evt = _evt(["/c/product.png", "/c/brief.md"], ["image/png", "text/markdown"], MessageType.PHOTO)
    out = _build_media_placeholder(evt)
    assert "[User sent an image: /c/product.png]" in out
    assert "[User sent an image: /c/brief.md]" not in out
    assert "[User sent a file: /c/brief.md]" in out


def test_placeholder_image_with_unknown_mime_uses_photo_fallback():
    evt = _evt(["/c/photo.jpg"], [""], MessageType.PHOTO)
    out = _build_media_placeholder(evt)
    assert "[User sent an image: /c/photo.jpg]" in out
