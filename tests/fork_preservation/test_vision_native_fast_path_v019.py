"""Tests for the native-vision fast path inside vision_analyze.

When the active main model supports native vision AND the provider supports
image content inside tool-result messages, ``_handle_vision_analyze`` skips
the auxiliary LLM and returns a multimodal envelope so the main model sees
the pixels directly on its next turn.
"""

from __future__ import annotations

# Fork-preservation residue extracted during the v2026.8.16 integration.
# These tests were present at fork head 896a5ea3b2 but were removed or
# reorganized upstream; keep them as behavior gates for retained features.

import asyncio
import base64
import json
from unittest.mock import patch


from tools.vision_tools import (
    VISION_ANALYZE_SCHEMA,
    _build_native_vision_tool_result,
    _handle_vision_analyze,
    _supports_media_in_tool_results,
    _vision_analyze_native,
)


# Minimal valid 1x1 PNG bytes.
_TINY_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


# ─── Prompt-facing schema ────────────────────────────────────────────────────




# ─── _supports_media_in_tool_results ─────────────────────────────────────────




# ─── _build_native_vision_tool_result ────────────────────────────────────────




# ─── _vision_analyze_native ──────────────────────────────────────────────────


class TestVisionAnalyzeNative:

    def test_missing_file_returns_error_string(self, tmp_path):
        result = asyncio.get_event_loop().run_until_complete(
            _vision_analyze_native(str(tmp_path / "nope.png"), "?")
        )
        # tool_error returns a JSON string, not the multimodal envelope
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed.get("success") is False
        # Unified resolver: local backend reports a clean not-found.
        err = parsed.get("error", "").lower()
        assert (
            "image file not found" in err
            or "media file not found" in err
            or "no active sandbox" in err
        )

    def test_empty_image_url_returns_error(self):
        result = asyncio.get_event_loop().run_until_complete(
            _vision_analyze_native("", "?")
        )
        assert isinstance(result, str)
        parsed = json.loads(result)
        assert parsed.get("success") is False
        assert "image_url is required" in parsed.get("error", "")




# ─── _handle_vision_analyze fast-path gating ─────────────────────────────────
