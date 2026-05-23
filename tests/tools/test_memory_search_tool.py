"""Tests for tools/memory_search_tool.py."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.memory_search_tool import (
    DEFAULT_INDEX_PATH,
    DEFAULT_ROOTS,
    MEMORY_SEARCH_SCHEMA,
    build_index,
    import_openclaw_legacy_memory,
    memory_search_tool,
)


def test_build_index_and_search_markdown_files(tmp_path):
    root = tmp_path / "ChatWorkspace"
    project = root / "ngng"
    project.mkdir(parents=True)
    context = project / "context.md"
    context.write_text(
        "# NGNG Context\n\n"
        "## Film scanning\n"
        "Camera scanning uses Lightroom and NLP sharpening notes.\n\n"
        "## Darkroom\n"
        "RA-4 printing notes live here.\n",
        encoding="utf-8",
    )

    index_path = tmp_path / "memory_search.sqlite"
    result = build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    assert result["success"] is True
    assert result["indexed_files"] == 1
    assert result["indexed_chunks"] >= 2

    payload = json.loads(memory_search_tool("RA-4 printing", limit=5, index_path=index_path))
    assert payload["success"] is True
    assert payload["results"]
    hit = payload["results"][0]
    assert hit["source"] == "chatworkspace"
    assert hit["path"].endswith("ngng/context.md")
    assert "RA-4" in hit["snippet"]
    assert hit["start_line"] <= hit["end_line"]


def test_search_updates_index_incrementally(tmp_path):
    root = tmp_path / "memories"
    root.mkdir()
    note = root / "MEMORY.md"
    note.write_text("Initial note about Python.\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"

    first = json.loads(memory_search_tool("Python", roots=[(root, "memories")], index_path=index_path))
    assert first["success"] is True
    assert first["results"]

    note.write_text("Updated note about espresso grinders.\n", encoding="utf-8")
    second = json.loads(memory_search_tool("espresso", roots=[(root, "memories")], index_path=index_path))
    assert second["success"] is True
    assert second["results"]
    assert "espresso" in second["results"][0]["snippet"].lower()


def test_path_filter_limits_results(tmp_path):
    root = tmp_path / "ChatWorkspace"
    (root / "a").mkdir(parents=True)
    (root / "b").mkdir(parents=True)
    (root / "a" / "context.md").write_text("Alpha camera scanning note.\n", encoding="utf-8")
    (root / "b" / "context.md").write_text("Beta camera scanning note.\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    payload = json.loads(memory_search_tool("camera", path_filter="/a/", index_path=index_path, freshness_seconds=9999))

    assert payload["success"] is True
    assert payload["results"]
    assert all("/a/" in hit["path"] for hit in payload["results"])


def test_default_roots_include_neutral_chatworkspace_and_hermes_memories():
    root_strings = {(str(path), source) for path, source in DEFAULT_ROOTS}
    assert (str(Path.home() / "ChatWorkspace"), "chatworkspace") in root_strings
    assert (str(Path.home() / ".hermes" / "memories"), "memories") in root_strings
    assert DEFAULT_INDEX_PATH == Path.home() / ".hermes" / "memory_search.sqlite"


def test_schema_lists_all_default_indexed_sources():
    source_schema = MEMORY_SEARCH_SCHEMA["parameters"]["properties"]["source"]

    assert "localops" in source_schema["enum"]


def test_default_roots_include_localops_operator_notes():
    root_strings = {(str(path), source) for path, source in DEFAULT_ROOTS}

    assert (str(Path.home() / "LocalOps"), "localops") in root_strings


def test_search_returns_clear_error_for_empty_query(tmp_path):
    payload = json.loads(memory_search_tool("   ", index_path=tmp_path / "idx.sqlite"))
    assert payload["success"] is False
    assert "query" in payload["error"].lower()


def test_search_retries_with_or_terms_when_strict_and_query_has_multiple_terms(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "a.md").write_text("AutoFilmCrop uses Cloudflare Workers for licensing.\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    payload = json.loads(memory_search_tool("AutoFilmCrop licensing SQLite", roots=[(root, "chatworkspace")], index_path=index_path, freshness_seconds=9999))

    assert payload["success"] is True
    assert payload["results"]
    assert payload["query_strategy"] == "relaxed_or"
    assert "AutoFilmCrop" in payload["results"][0]["snippet"]


def test_import_openclaw_legacy_memory_chunks(tmp_path):
    legacy_db = tmp_path / "main.sqlite"
    index_path = tmp_path / "memory_search.sqlite"
    import sqlite3
    con = sqlite3.connect(legacy_db)
    con.execute(
        """
        CREATE TABLE chunks (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            source TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            hash TEXT NOT NULL,
            model TEXT NOT NULL,
            text TEXT NOT NULL,
            embedding TEXT NOT NULL,
            updated_at INTEGER NOT NULL
        )
        """
    )
    con.execute(
        """
        INSERT INTO chunks(id, path, source, start_line, end_line, hash, model, text, embedding, updated_at)
        VALUES ('c1', 'memory/old.md', 'memory', 3, 9, 'h1', 'gemini', 'Old OpenClaw note about Mamiya half frame masks.', '[]', 1)
        """
    )
    con.commit()
    con.close()

    imported = import_openclaw_legacy_memory(legacy_db_path=legacy_db, index_path=index_path)
    assert imported["success"] is True
    assert imported["imported_chunks"] == 1

    payload = json.loads(memory_search_tool("Mamiya masks", source="openclaw_legacy", index_path=index_path, freshness_seconds=9999))
    assert payload["success"] is True
    assert payload["results"]
    hit = payload["results"][0]
    assert hit["source"] == "openclaw_legacy"
    assert hit["path"] == "openclaw://memory/old.md"
    assert "Mamiya" in hit["snippet"]
