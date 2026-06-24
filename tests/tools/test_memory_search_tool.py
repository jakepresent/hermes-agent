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


def _write_observation_corpus(tmp_path):
    root = tmp_path / "ChatWorkspace"
    project = root / "mamiya"
    project.mkdir(parents=True)
    (project / "context.md").write_text(
        "# Mamiya Context\n\n"
        "## Observations\n"
        "- [decision] Use local-first storage for privacy #sovereignty #privacy\n"
        "- [status] v5 batch tested and ready for beta\n"
        "- [risk] Burr on tooth 3 needs targeted sanding #qc\n"
        "- [ ] this is a checkbox not an observation\n"
        "- [x] also a checkbox\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)
    return index_path, root


def test_observation_search_returns_individual_facts_with_exact_lines(tmp_path):
    index_path, root = _write_observation_corpus(tmp_path)
    payload = json.loads(
        memory_search_tool(
            "storage privacy",
            granularity="observation",
            index_path=index_path,
            roots=[(str(root), "chatworkspace")],
            freshness_seconds=0,
        )
    )
    assert payload["success"] is True
    assert payload["granularity"] == "observation"
    assert payload["count"] == 1
    hit = payload["results"][0]
    assert hit["category"] == "decision"
    # The decision line is line 4 of the file (1: title, 2: blank, 3: heading).
    assert hit["line"] == 4
    assert set(hit["tags"]) == {"privacy", "sovereignty"}
    assert "local-first" in hit["text"]


def test_observation_search_excludes_markdown_checkboxes(tmp_path):
    index_path, root = _write_observation_corpus(tmp_path)
    payload = json.loads(
        memory_search_tool(
            "",
            granularity="observation",
            path_filter="mamiya",
            limit=25,
            index_path=index_path,
            roots=[(str(root), "chatworkspace")],
            freshness_seconds=0,
        )
    )
    categories = sorted(h["category"] for h in payload["results"])
    assert categories == ["decision", "risk", "status"]
    # Checkbox markers must never be indexed as observation categories.
    assert " " not in categories
    assert "x" not in categories


def test_category_filter_lists_facts_without_a_query(tmp_path):
    index_path, root = _write_observation_corpus(tmp_path)
    payload = json.loads(
        memory_search_tool(
            "",
            category="decision",
            index_path=index_path,
            roots=[(str(root), "chatworkspace")],
            freshness_seconds=0,
        )
    )
    assert payload["success"] is True
    assert payload["granularity"] == "observation"  # category implies observation mode
    assert payload["count"] == 1
    assert payload["results"][0]["category"] == "decision"


def test_chunk_granularity_is_default_and_unchanged(tmp_path):
    index_path, root = _write_observation_corpus(tmp_path)
    payload = json.loads(
        memory_search_tool(
            "targeted sanding",
            index_path=index_path,
            roots=[(str(root), "chatworkspace")],
            freshness_seconds=0,
        )
    )
    assert payload["success"] is True
    assert payload["granularity"] == "chunk"
    assert payload["results"]
    # Chunk results expose passage line ranges, not single-line observations.
    assert "start_line" in payload["results"][0]
    assert "end_line" in payload["results"][0]


def test_schema_exposes_granularity_category_and_mode_params():
    props = MEMORY_SEARCH_SCHEMA["parameters"]["properties"]
    assert "granularity" in props
    assert props["granularity"]["enum"] == ["chunk", "observation"]
    assert "category" in props
    assert props["mode"]["enum"] == ["keyword", "semantic", "hybrid"]


def test_build_index_prunes_deleted_files(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    keep = root / "keep.md"
    gone = root / "gone.md"
    keep.write_text("Keep note about cameras.\n", encoding="utf-8")
    gone.write_text("Stale note about deleted espresso grinder.\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    gone.unlink()
    rebuilt = build_index(index_path=index_path, roots=[(root, "chatworkspace")])

    assert rebuilt["deleted_files"] == 1
    with sqlite3.connect(index_path) as con:
        paths = {row[0] for row in con.execute("SELECT path FROM files")}
    assert str(keep) in paths
    assert str(gone) not in paths
    payload = json.loads(
        memory_search_tool(
            "espresso grinder",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
        )
    )
    assert payload["success"] is True
    assert payload["results"] == []


def test_hybrid_mode_merges_semantic_and_keyword_results(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "agent.md").write_text(
        "# Agent evals\n\n"
        "A disposable sandbox runs a configured agent with isolated tools and files.\n",
        encoding="utf-8",
    )
    (root / "film.md").write_text("# Film\n\nRA-4 darkroom tray workflow.\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    hybrid = json.loads(
        memory_search_tool(
            "configured runtime",
            mode="hybrid",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
        )
    )

    assert hybrid["success"] is True
    assert hybrid["mode"] == "hybrid"
    assert hybrid["semantic"]["backend"].startswith("sklearn_")
    assert hybrid["query_strategy"] == "semantic_lsa+keyword_rrf"
    assert hybrid["results"]
    assert hybrid["results"][0]["path"].endswith("agent.md")


def test_semantic_observation_search_respects_category_and_path_filter(tmp_path):
    index_path, root = _write_observation_corpus(tmp_path)
    payload = json.loads(
        memory_search_tool(
            "production batch",
            mode="semantic",
            granularity="observation",
            category="status",
            path_filter="mamiya",
            index_path=index_path,
            roots=[(str(root), "chatworkspace")],
            freshness_seconds=9999,
        )
    )

    assert payload["success"] is True
    assert payload["mode"] == "semantic"
    assert payload["granularity"] == "observation"
    assert payload["category"] == "status"
    assert payload["results"]
    assert all(hit["category"] == "status" for hit in payload["results"])
    assert "v5 batch" in payload["results"][0]["text"]


def test_search_rejects_unknown_mode(tmp_path):
    payload = json.loads(
        memory_search_tool("RA-4", index_path=tmp_path / "idx.sqlite", mode="magic")
    )

    assert payload["success"] is False
    assert "mode" in payload["error"]
