"""Tests for tools/memory_search_tool.py."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from tools.memory_search_tool import (
    DEFAULT_INDEX_PATH,
    DEFAULT_ROOTS,
    MEMORY_SEARCH_SCHEMA,
    build_index,
    import_openclaw_legacy_memory,
    import_openclaw_legacy_sessions,
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
    assert "render_format" not in payload
    assert "toon_context" not in payload
    hit = payload["results"][0]
    assert hit["source"] == "chatworkspace"
    assert hit["path"].endswith("ngng/context.md")
    assert "RA-4" in hit["snippet"]
    assert hit["start_line"] <= hit["end_line"]


def test_search_can_return_toon_context(tmp_path):
    root = tmp_path / "ChatWorkspace"
    project = root / "ngng"
    project.mkdir(parents=True)
    (project / "context.md").write_text(
        "# NGNG Context\n\n"
        "## Darkroom\n"
        "RA-4 printing notes live here.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    payload = json.loads(
        memory_search_tool("RA-4", index_path=index_path, render_format="toon")
    )

    assert payload["success"] is True
    assert payload["render_format"] == "toon"
    assert "results" not in payload
    assert "hits[" in payload["toon_context"]
    assert "source,path,lines,score,snippet" in payload["toon_context"]
    assert "chatworkspace" in payload["toon_context"]
    assert "ChatWorkspace/ngng/context.md" in payload["toon_context"]
    assert str(root) not in payload["toon_context"]
    assert "RA-4" in payload["toon_context"]


def test_search_rejects_unknown_render_format(tmp_path):
    payload = json.loads(
        memory_search_tool("RA-4", index_path=tmp_path / "idx.sqlite", render_format="yaml")
    )

    assert payload["success"] is False
    assert "render_format" in payload["error"]


def test_search_rejects_unknown_search_mode(tmp_path):
    payload = json.loads(
        memory_search_tool("RA-4", index_path=tmp_path / "idx.sqlite", search_mode="semantic")
    )

    assert payload["success"] is False
    assert "search_mode" in payload["error"]


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
    assert "legacy_sessions" in source_schema["enum"]

    search_mode_schema = MEMORY_SEARCH_SCHEMA["parameters"]["properties"]["search_mode"]
    assert search_mode_schema["enum"] == ["chunks", "facts", "hybrid"]


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


def test_schema_creates_memory_fact_tables(tmp_path):
    index_path = tmp_path / "idx.sqlite"
    build_index(index_path=index_path, roots=[], force=True)

    with sqlite3.connect(index_path) as con:
        names = {
            r[0]
            for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type IN ('table', 'view')"
            )
        }

    assert "memory_facts" in names
    assert "memory_facts_fts" in names
    assert "memory_fact_chunks" in names


def test_build_index_extracts_fact_cards_from_markdown_bullets(tmp_path):
    root = tmp_path / "ChatWorkspace"
    project = root / "ngng"
    project.mkdir(parents=True)
    (project / "context.md").write_text(
        "# NGNG\n\n"
        "## Chemistry\n"
        "- Controlled scan reads outrank AI screenshot impressions.\n"
        "- Reconcile C-41 hypotheses against canonical formulas.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    payload = json.loads(
        memory_search_tool(
            "controlled scan reads",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="facts",
        )
    )

    assert payload["success"] is True
    assert payload["search_mode"] == "facts"
    assert payload["facts"]
    fact = payload["facts"][0]
    assert fact["topic"] == "Chemistry"
    assert fact["source"] == "chatworkspace"
    assert fact["path"].endswith("ngng/context.md")
    assert fact["lines"] == "4-4"
    assert "Controlled scan reads" in fact["fact"]
    assert fact["source_hash"]
    assert fact["extractor_version"]


def test_fact_cards_rebuild_when_source_chunk_changes(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    note = root / "context.md"
    note.write_text("# Project\n\n## Notes\n- Old durable fact about bleach.\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    old_payload = json.loads(
        memory_search_tool(
            "bleach",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="facts",
        )
    )
    assert old_payload["facts"]

    note.write_text("# Project\n\n## Notes\n- New durable fact about fixer.\n", encoding="utf-8")
    build_index(index_path=index_path, roots=[(root, "chatworkspace")])

    stale_payload = json.loads(
        memory_search_tool(
            "bleach",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="facts",
        )
    )
    fresh_payload = json.loads(
        memory_search_tool(
            "fixer",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="facts",
        )
    )

    assert stale_payload["facts"] == []
    assert fresh_payload["facts"]
    assert "New durable fact" in fresh_payload["facts"][0]["fact"]


def test_fact_search_backfills_relaxed_or_matches(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "context.md").write_text(
        "# Project\n\n## Notes\n- AutoFilmCrop uses Cloudflare Workers for licensing.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)
    with sqlite3.connect(index_path) as con:
        con.execute("DELETE FROM memory_facts_fts")
        con.execute("DELETE FROM memory_facts")
        con.execute("DELETE FROM memory_fact_chunks")
        con.commit()

    payload = json.loads(
        memory_search_tool(
            "AutoFilmCrop SQLite",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="facts",
        )
    )

    assert payload["success"] is True
    assert payload["facts"]
    assert "AutoFilmCrop uses Cloudflare Workers" in payload["facts"][0]["fact"]


def test_fact_cards_removed_when_source_file_is_deleted(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    note = root / "context.md"
    note.write_text("# Project\n\n## Notes\n- Temporary fact about removal.\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)
    assert json.loads(
        memory_search_tool(
            "temporary removal",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="facts",
        )
    )["facts"]

    note.unlink()
    build_index(index_path=index_path, roots=[(root, "chatworkspace")])

    payload = json.loads(
        memory_search_tool(
            "temporary removal",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="facts",
        )
    )

    assert payload["success"] is True
    assert payload["facts"] == []


def test_fact_search_backfills_missing_fact_cache_from_existing_chunks(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "context.md").write_text(
        "# Project\n\n## Notes\n- Backfilled fact about archived context.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)
    with sqlite3.connect(index_path) as con:
        con.execute("DELETE FROM memory_facts_fts")
        con.execute("DELETE FROM memory_facts")
        con.execute("DELETE FROM memory_fact_chunks")
        con.commit()

    payload = json.loads(
        memory_search_tool(
            "backfilled archived",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="facts",
        )
    )

    assert payload["success"] is True
    assert payload["facts"]
    assert "Backfilled fact" in payload["facts"][0]["fact"]


def test_hybrid_toon_search_returns_fact_and_chunk_context(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "context.md").write_text(
        "# Project\n\n"
        "## Notes\n"
        "- Hybrid fact about camera scanning.\n\n"
        "Camera scanning also appears in raw prose for fallback context.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    payload = json.loads(
        memory_search_tool(
            "camera scanning",
            roots=[(root, "chatworkspace")],
            index_path=index_path,
            freshness_seconds=9999,
            search_mode="hybrid",
            render_format="toon",
        )
    )

    assert payload["success"] is True
    assert payload["search_mode"] == "hybrid"
    assert payload["render_format"] == "toon"
    assert "results" not in payload
    assert "facts" not in payload
    assert "context[" in payload["toon_context"]
    assert "kind,topic,source,path,lines,score,text,use_when" in payload["toon_context"]
    assert "fact," in payload["toon_context"]
    assert "chunk," in payload["toon_context"]


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


def test_import_openclaw_legacy_sessions_indexes_jsonl_messages(tmp_path):
    sessions_root = tmp_path / "agents"
    session_dir = sessions_root / "main" / "sessions"
    session_dir.mkdir(parents=True)
    session_file = session_dir / "session-123.jsonl"
    session_file.write_text(
        '\n'.join([
            json.dumps({"type": "session", "id": "session-123", "timestamp": "2026-05-23T15:03:26Z"}),
            json.dumps({
                "type": "message",
                "id": "m1",
                "timestamp": "2026-05-23T15:03:27Z",
                "message": {
                    "role": "user",
                    "content": [{"type": "text", "text": "Please compare Granola to Aside for meeting notes."}],
                },
            }),
            json.dumps({
                "type": "message",
                "id": "m2",
                "timestamp": "2026-05-23T15:03:28Z",
                "message": {
                    "role": "assistant",
                    "content": [{"type": "text", "text": "Aside is local-first and Granola is a hosted AI notepad."}],
                },
            }),
        ]) + '\n',
        encoding="utf-8",
    )
    # Trajectory files are noisier tool traces and should not be indexed by this importer.
    (session_dir / "session-123.trajectory.jsonl").write_text(
        json.dumps({"type": "message", "message": {"role": "tool", "content": "SHOULD_NOT_APPEAR"}}) + "\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"

    imported = import_openclaw_legacy_sessions(sessions_root=sessions_root, index_path=index_path)

    assert imported["success"] is True
    assert imported["imported_files"] == 1
    assert imported["imported_chunks"] == 1

    payload = json.loads(memory_search_tool("Granola Aside", source="legacy_sessions", index_path=index_path, freshness_seconds=9999))
    assert payload["success"] is True
    assert payload["results"]
    hit = payload["results"][0]
    assert hit["source"] == "legacy_sessions"
    assert hit["path"] == "openclaw-session://main/session-123"
    assert "Granola" in hit["snippet"]
    assert "SHOULD_NOT_APPEAR" not in hit["snippet"]
