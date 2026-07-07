"""Tests for OCR-specific image extraction tooling."""

from __future__ import annotations

import asyncio
import base64
import json
from unittest.mock import patch

from PIL import Image, ImageDraw

from tools.registry import registry
from tools.vision_tools import _handle_ocr_extract


_TINY_PNG = base64.b64decode(
    b"iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="
)


def test_ocr_extract_registered_under_vision_toolset():
    entries, _checks = registry._snapshot_state()
    entry = next((e for e in entries if e.name == "ocr_extract"), None)

    assert entry is not None
    assert entry.toolset == "vision"


def test_ocr_extract_schema_discourages_native_vision_confidence_checks():
    entries, _checks = registry._snapshot_state()
    entry = next(e for e in entries if e.name == "ocr_extract")
    description = entry.schema["description"]

    assert "not an independent confidence check" in description
    assert "vision-capable main model" in description
    assert "fast auxiliary vision model" in description
    assert "local Tesseract/native OCR is fallback only" in description
    assert "deterministic local OCR when available" not in description


def test_ocr_requirements_true_when_tesseract_exists_without_vision_backend():
    from tools.vision_tools import check_ocr_requirements

    with patch("tools.vision_tools.shutil.which", return_value="/usr/bin/tesseract"), \
            patch("tools.vision_tools.check_vision_requirements", return_value=False):
        assert check_ocr_requirements() is True


def test_ocr_extract_uses_local_engine_when_available(tmp_path):
    img = tmp_path / "text.png"
    img.write_bytes(_TINY_PNG)

    with patch("tools.vision_tools._ocr_extract_fast_vision", side_effect=RuntimeError("vision unavailable")), \
            patch("tools.vision_tools._ocr_extract_local", return_value={
        "engine": "tesseract",
        "text": "Flight 123\nGate A4",
        "raw": "Flight 123\nGate A4\n",
    }), patch("tools.vision_tools._ocr_extract_native", side_effect=AssertionError("LLM fallback should not run")):
        result = asyncio.get_event_loop().run_until_complete(
            _handle_ocr_extract({"image_url": str(img), "mode": "plain"})
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["engine"] == "tesseract"
    assert parsed["text"] == "Flight 123\nGate A4"
    assert parsed["mode"] == "plain"


def test_ocr_extract_second_pass_for_malformed_local_text(tmp_path):
    img = tmp_path / "text.png"
    img.write_bytes(_TINY_PNG)

    async def _native_sentinel(image_url, mode="plain", context=""):
        return {
            "_multimodal": True,
            "content": [{"type": "text", "text": context}],
            "text_summary": "sentinel",
            "meta": {"ocr": True, "second_pass": True},
        }

    with patch("tools.vision_tools._ocr_extract_fast_vision", side_effect=RuntimeError("vision unavailable")), \
            patch("tools.vision_tools._ocr_extract_local", return_value={
        "engine": "tesseract",
        "text": "Flight 123,\nGate Ad\n\n1100 AM",
        "raw": "Flight 123,\nGate Ad\n\n1100 AM\n",
    }), patch("tools.vision_tools._ocr_extract_native", side_effect=_native_sentinel):
        result = asyncio.get_event_loop().run_until_complete(
            _handle_ocr_extract({"image_url": str(img), "mode": "plain"})
        )

    assert isinstance(result, dict)
    assert result.get("_multimodal") is True
    assert result["meta"]["second_pass"] is True
    context = result["content"][0]["text"]
    assert "Tesseract OCR output looked ambiguous" in context
    assert "Gate Ad" in context
    assert "1100 AM" in context


def test_ocr_extract_second_pass_for_financial_table_artifacts(tmp_path):
    img = tmp_path / "text.png"
    img.write_bytes(_TINY_PNG)

    async def _native_sentinel(image_url, mode="plain", context=""):
        return {"_multimodal": True, "content": [{"type": "text", "text": context}], "meta": {"ocr": True}}

    with patch("tools.vision_tools._ocr_extract_fast_vision", side_effect=RuntimeError("vision unavailable")), \
            patch("tools.vision_tools._ocr_extract_local", return_value={
        "engine": "tesseract",
        "text": "Net Gain/Loss\nMSET MICROSOFT CORP 341,740.41 $1,029.49\n$764,775 “$111.00",
        "raw": "Net Gain/Loss\nMSET MICROSOFT CORP 341,740.41 $1,029.49\n$764,775 “$111.00",
    }), patch("tools.vision_tools._ocr_extract_native", side_effect=_native_sentinel):
        result = asyncio.get_event_loop().run_until_complete(
            _handle_ocr_extract({"image_url": str(img), "mode": "plain", "context": "financial gain/loss table"})
        )

    assert isinstance(result, dict)
    context = result["content"][0]["text"]
    assert "financial gain/loss table" in context
    assert "341,740.41" in context
    assert "MSET" in context


def test_ocr_extract_preprocesses_small_text_for_tesseract(tmp_path):
    import pytest
    from tools.vision_tools import shutil

    if shutil.which("tesseract") is None:
        pytest.skip("tesseract not installed")

    img_path = tmp_path / "small-text.png"
    img = Image.new("RGB", (600, 180), "white")
    draw = ImageDraw.Draw(img)
    draw.text((30, 45), "Flight 123\nGate A4\n11:00 AM", fill="black")
    img.save(img_path)

    with patch("tools.vision_tools._ocr_extract_fast_vision", side_effect=RuntimeError("vision unavailable")):
        result = asyncio.get_event_loop().run_until_complete(
            _handle_ocr_extract({"image_url": str(img_path), "mode": "plain"})
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert "Flight 123" in parsed["text"]
    assert "Gate A4" in parsed["text"]
    assert "11:00 AM" in parsed["text"]


def test_ocr_extract_uses_fast_copilot_vision_as_primary_when_local_not_clean(tmp_path):
    img = tmp_path / "text.png"
    img.write_bytes(_TINY_PNG)

    async def _fast_vision_sentinel(image_url, mode="plain", context=""):
        return {
            "success": True,
            "engine": "copilot:gpt-5.4-mini",
            "mode": mode,
            "text": "Flight 123\nGate A4",
        }

    with patch("tools.vision_tools._ocr_extract_local", return_value=None), \
            patch("tools.vision_tools._ocr_extract_fast_vision", side_effect=_fast_vision_sentinel):
        result = asyncio.get_event_loop().run_until_complete(
            _handle_ocr_extract({"image_url": str(img), "mode": "plain"})
        )

    parsed = json.loads(result)
    assert parsed["success"] is True
    assert parsed["engine"] == "copilot:gpt-5.4-mini"
    assert parsed["text"] == "Flight 123\nGate A4"


def test_ocr_fast_vision_uses_gpt54_mini_with_reasoning_disabled(tmp_path):
    from tools.vision_tools import _ocr_extract_fast_vision

    img = tmp_path / "text.png"
    img.write_bytes(_TINY_PNG)
    captured = {}

    async def fake_call_llm(**kwargs):
        captured.update(kwargs)
        return type("Resp", (), {
            "choices": [type("Choice", (), {
                "message": type("Msg", (), {"content": "Exact text"})()
            })()]
        })()

    with patch("tools.vision_tools.async_call_llm", side_effect=fake_call_llm):
        result = asyncio.get_event_loop().run_until_complete(
            _ocr_extract_fast_vision(str(img), mode="plain", context="focus")
        )

    assert result["success"] is True
    assert result["text"] == "Exact text"
    assert captured["provider"] == "copilot"
    assert captured["model"] == "gpt-5.4-mini"
    assert captured["extra_body"] == {"reasoning": {"enabled": False}}

def test_ocr_extract_is_exposed_in_default_discord_toolset():
    from model_tools import discover_builtin_tools
    from toolsets import resolve_toolset
    from tools.registry import registry

    discover_builtin_tools()

    tool_names = set(resolve_toolset("hermes-discord"))
    definitions = registry.get_definitions(tool_names, quiet=True)
    exposed_names = {item["function"]["name"] for item in definitions}

    assert "vision_analyze" in tool_names
    assert "ocr_extract" in tool_names
    assert "ocr_extract" in exposed_names

def _force_vision_tools_available(monkeypatch):
    from tools.registry import invalidate_check_fn_cache, registry
    import model_tools

    for tool_name in ("vision_analyze", "ocr_extract"):
        entry = registry.get_entry(tool_name)
        assert entry is not None
        monkeypatch.setattr(entry, "check_fn", lambda: True)
    invalidate_check_fn_cache()
    model_tools._clear_tool_defs_cache()
    return model_tools


def test_ocr_extract_hidden_from_tool_schema_when_active_images_route_native(monkeypatch):
    model_tools = _force_vision_tools_available(monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"agent": {"image_input_mode": "native"}},
    )
    model_tools._clear_tool_defs_cache()

    tools = model_tools.get_tool_definitions(
        enabled_toolsets=["vision"],
        quiet_mode=True,
        active_provider="copilot",
        active_model="gpt-5.5",
    )
    names = {tool["function"]["name"] for tool in tools}

    assert "ocr_extract" not in names
    vision_desc = next(
        tool["function"]["description"]
        for tool in tools
        if tool["function"]["name"] == "vision_analyze"
    )
    assert "ocr_extract" not in vision_desc


def test_ocr_extract_stays_exposed_when_images_route_to_text(monkeypatch):
    model_tools = _force_vision_tools_available(monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {"agent": {"image_input_mode": "text"}},
    )
    model_tools._clear_tool_defs_cache()

    tools = model_tools.get_tool_definitions(
        enabled_toolsets=["vision"],
        quiet_mode=True,
        active_provider="copilot",
        active_model="gpt-5.5",
    )
    names = {tool["function"]["name"] for tool in tools}

    assert "ocr_extract" in names


def test_ocr_extract_native_vision_opt_in_keeps_tool_exposed(monkeypatch):
    model_tools = _force_vision_tools_available(monkeypatch)
    monkeypatch.setattr(
        "hermes_cli.config.load_config",
        lambda: {
            "agent": {
                "image_input_mode": "native",
                "expose_ocr_extract_with_native_vision": True,
            }
        },
    )
    model_tools._clear_tool_defs_cache()

    tools = model_tools.get_tool_definitions(
        enabled_toolsets=["vision"],
        quiet_mode=True,
        active_provider="copilot",
        active_model="gpt-5.5",
    )
    names = {tool["function"]["name"] for tool in tools}

    assert "ocr_extract" in names

