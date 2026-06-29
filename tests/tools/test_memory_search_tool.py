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


def test_schema_description_positions_memory_as_durable_context_cache():
    desc = MEMORY_SEARCH_SCHEMA["description"]

    assert "cache tier" in desc
    assert "prior/project context" in desc
    assert "ChatWorkspace" in desc


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

    payload = json.loads(memory_search_tool("AutoFilmCrop licensing SQLite", mode="keyword", roots=[(root, "chatworkspace")], index_path=index_path, freshness_seconds=9999))

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
            mode="keyword",
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
            mode="keyword",
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
            mode="keyword",
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
            mode="keyword",
            index_path=index_path,
            roots=[(str(root), "chatworkspace")],
            freshness_seconds=0,
        )
    )
    assert payload["success"] is True
    assert payload["mode"] == "keyword"
    assert payload["granularity"] == "chunk"
    assert payload["results"]
    # Chunk results expose passage line ranges, not single-line observations.
    assert "start_line" in payload["results"][0]
    assert "end_line" in payload["results"][0]


def test_schema_exposes_granularity_category_and_mode_params():
    props = MEMORY_SEARCH_SCHEMA["parameters"]["properties"]
    assert "granularity" in props
    assert props["granularity"]["enum"] == ["chunk", "observation", "all"]
    assert props["action"]["enum"] == ["search", "status", "preindex"]
    assert "max_batches" in props
    assert MEMORY_SEARCH_SCHEMA["parameters"]["required"] == []
    assert "category" in props
    assert props["mode"]["enum"] == ["keyword", "semantic", "hybrid"]
    assert props["semantic_backend"]["enum"] == ["sklearn", "gemini"]
    assert "semantic_model" in props


def test_default_search_mode_is_hybrid(tmp_path):
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "agent.md").write_text(
        "# Agent evals\n\nA configured agent runtime uses a disposable sandbox.\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    payload = json.loads(
        memory_search_tool(
            "configured runtime",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
        )
    )

    assert payload["success"] is True
    assert payload["mode"] == "hybrid"
    assert payload["semantic"]["requested_backend"] == "gemini"
    assert payload["semantic"]["fallback"] == "sklearn"
    assert payload["semantic"]["backend"].startswith("sklearn_")
    assert payload["results"]


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
            semantic_backend="sklearn",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
        )
    )

    assert hybrid["success"] is True
    assert hybrid["mode"] == "hybrid"
    assert hybrid["semantic"]["backend"].startswith("sklearn_")
    assert hybrid["query_strategy"] == "semantic+keyword_rrf"
    assert hybrid["results"]
    assert hybrid["results"][0]["path"].endswith("agent.md")


def test_default_hybrid_skips_cold_gemini_rebuild_for_large_corpus(tmp_path, monkeypatch):
    import tools.memory_search_tool as mst

    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    for idx in range(4):
        (root / f"note-{idx}.md").write_text(
            f"# Note {idx}\n\nAlpha beta project context {idx}.\n",
            encoding="utf-8",
        )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)
    monkeypatch.setattr(mst, "_GEMINI_DEFAULT_MAX_COLD_ROWS", 1)
    monkeypatch.setattr(mst, "_SKLEARN_DEFAULT_MAX_COLD_ROWS", 1)

    payload = json.loads(
        memory_search_tool(
            "alpha beta",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
        )
    )

    assert payload["success"] is True
    assert payload["mode"] == "hybrid"
    assert payload["semantic"]["requested_backend"] == "gemini"
    assert payload["semantic"]["fallback"] == "keyword"
    assert "refusing" in payload["semantic"]["error"]
    assert "refusing" in payload["semantic"]["fallback_error"]
    assert payload["results"]




def test_gemini_persistent_embeddings_use_batched_storage(tmp_path, monkeypatch):
    import tools.memory_search_tool as mst

    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "agent.md").write_text("# Agent\n\nconfigured runtime sandbox\n", encoding="utf-8")
    (root / "film.md").write_text("# Film\n\nRA-4 darkroom workflow\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    calls = []

    def fake_batch(texts, *, model, max_retries=0):
        calls.append((list(texts), model, max_retries))
        vectors = []
        for text in texts:
            vectors.append([1.0, 0.0, 0.0] if "configured" in text else [0.0, 1.0, 0.0])
        return vectors

    def fake_query(texts, *, model):
        assert len(texts) == 1
        return [[1.0, 0.0, 0.0]]

    monkeypatch.setattr(mst, "_embed_gemini_texts_batched", fake_batch)
    monkeypatch.setattr(mst, "_embed_gemini_texts", fake_query)

    first = json.loads(
        memory_search_tool(
            "configured runtime",
            mode="semantic",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
        )
    )
    second = json.loads(
        memory_search_tool(
            "configured runtime",
            mode="semantic",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
        )
    )

    assert first["success"] is True
    assert first["semantic"]["backend"] == "gemini:gemini-embedding-2"
    assert first["semantic"]["rebuilt"] is True
    assert first["semantic"]["vector_index"] == "sqlite-vec"
    assert second["semantic"]["rebuilt"] is False
    assert second["semantic"]["vector_index"] == "sqlite-vec"
    assert len(calls) == 1
    assert second["results"][0]["path"].endswith("agent.md")

    with sqlite3.connect(index_path) as con:
        row = con.execute("SELECT embedding, dim, vec_id FROM semantic_embeddings LIMIT 1").fetchone()
    assert isinstance(row[0], bytes)
    assert row[1] == 3
    assert isinstance(row[2], int)


def test_preindex_semantic_embeddings_syncs_sqlite_vec_table(tmp_path, monkeypatch):
    import tools.memory_search_tool as mst

    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    for idx in range(2):
        (root / f"note-{idx}.md").write_text(f"# Note {idx}\n\nText {idx}\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    def fake_batch(texts, *, model, max_retries=0):
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(mst, "_embed_gemini_texts_batched", fake_batch)

    result = mst.preindex_semantic_embeddings(
        index_path=index_path,
        roots=[(root, "chatworkspace")],
        freshness_seconds=9999,
        retry_429=False,
    )

    assert result["success"] is True
    assert result["sqlite_vec_synced"] is True
    assert result["sqlite_vec"]["table_count"] == 2
    assert result["sqlite_vec"]["persisted_count"] == 2


def test_preindex_semantic_embeddings_processes_limited_batches(tmp_path, monkeypatch):
    import tools.memory_search_tool as mst

    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    for idx in range(3):
        (root / f"note-{idx}.md").write_text(f"# Note {idx}\n\nText {idx}\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)
    monkeypatch.setenv("HERMES_MEMORY_SEARCH_GEMINI_BATCH_SIZE", "2")

    def fake_batch(texts, *, model, max_retries=0):
        return [[float(i), 1.0, 0.0] for i, _text in enumerate(texts)]

    monkeypatch.setattr(mst, "_embed_gemini_texts_batched", fake_batch)

    result = mst.preindex_semantic_embeddings(
        index_path=index_path,
        roots=[(root, "chatworkspace")],
        max_batches=1,
        freshness_seconds=9999,
        retry_429=False,
    )

    assert result["success"] is True
    assert result["processed"] == 2
    assert result["missing_before"] == 3
    assert result["remaining_estimate"] == 1
    with sqlite3.connect(index_path) as con:
        count = con.execute("SELECT COUNT(*) FROM semantic_embeddings").fetchone()[0]
    assert count == 2


def test_memory_search_status_action_reports_missing_prefixes(tmp_path, monkeypatch):
    import tools.memory_search_tool as mst

    root = tmp_path / "ChatWorkspace"
    work = root / "work" / "meeting_notes"
    work.mkdir(parents=True)
    (work / "note.md").write_text("# Meeting\n\nUnembedded cache health note.\n", encoding="utf-8")
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)
    monkeypatch.setattr(mst, "_GEMINI_DEFAULT_MAX_COLD_ROWS", 0)

    payload = json.loads(
        memory_search_tool(
            action="status",
            granularity="chunk",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
        )
    )

    assert payload["success"] is True
    assert payload["action"] == "status"
    chunk_status = payload["granularities"][0]
    assert chunk_status["missing_count"] == 1
    assert chunk_status["needs_preindex"] is True
    assert chunk_status["missing_by_prefix"][0]["value"] == "ChatWorkspace/work/meeting_notes"


def test_memory_search_preindex_action_can_repair_all_granularities(tmp_path, monkeypatch):
    import tools.memory_search_tool as mst

    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "note.md").write_text(
        "# Note\n\nContext for cache repair.\n- [decision] Cache repair should be one click #memory\n",
        encoding="utf-8",
    )
    index_path = tmp_path / "memory_search.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    def fake_batch(texts, *, model, max_retries=0):
        return [[1.0, 0.0, 0.0] for _ in texts]

    monkeypatch.setattr(mst, "_embed_gemini_texts_batched", fake_batch)

    payload = json.loads(
        memory_search_tool(
            action="preindex",
            granularity="all",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            freshness_seconds=9999,
            retry_429=False,
        )
    )

    assert payload["success"] is True
    assert payload["action"] == "preindex"
    assert {item["granularity"] for item in payload["granularities"]} == {"chunk", "observation"}
    assert payload["remaining_estimate"] == 0
    assert payload["processed"] >= 2


def test_semantic_observation_search_respects_category_and_path_filter(tmp_path):
    index_path, root = _write_observation_corpus(tmp_path)
    payload = json.loads(
        memory_search_tool(
            "production batch",
            mode="semantic",
            semantic_backend="sklearn",
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




def test_gemini_query_embedding_cache_reuses_query_vector(monkeypatch):
    import tools.memory_search_tool as mst

    calls = []

    def fake_query(texts, *, model):
        calls.append((list(texts), model))
        return [[1.0, 0.0, 0.0]]

    monkeypatch.setattr(mst, "_embed_gemini_texts", fake_query)
    mst._GEMINI_QUERY_EMBEDDING_CACHE.clear()

    first = mst._query_gemini_embedding("configured runtime", model_name="gemini-embedding-2")
    second = mst._query_gemini_embedding("configured runtime", model_name="gemini-embedding-2")

    assert first == second == [1.0, 0.0, 0.0]
    assert len(calls) == 1

def test_search_rejects_unknown_mode(tmp_path):
    payload = json.loads(
        memory_search_tool("RA-4", index_path=tmp_path / "idx.sqlite", mode="magic")
    )

    assert payload["success"] is False
    assert "mode" in payload["error"]


def test_search_rejects_unknown_semantic_backend(tmp_path):
    payload = json.loads(
        memory_search_tool(
            "RA-4",
            index_path=tmp_path / "idx.sqlite",
            mode="semantic",
            semantic_backend="magic",
        )
    )

    assert payload["success"] is False
    assert "semantic_backend" in payload["error"]


def test_semantic_gemini_backend_error_is_reported_when_key_missing(tmp_path, monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)
    root = tmp_path / "ChatWorkspace"
    root.mkdir()
    (root / "agent.md").write_text("# Agent\n\nconfigured agent runtime\n", encoding="utf-8")
    hermes_home = tmp_path / "hermes"
    hermes_home.mkdir()
    (hermes_home / ".env").write_text("", encoding="utf-8")
    index_path = tmp_path / "idx.sqlite"
    build_index(index_path=index_path, roots=[(root, "chatworkspace")], force=True)

    import tools.memory_search_tool as mst
    monkeypatch.setattr(mst, "get_hermes_home", lambda: hermes_home)
    payload = json.loads(
        memory_search_tool(
            "configured runtime",
            index_path=index_path,
            roots=[(root, "chatworkspace")],
            mode="semantic",
            semantic_backend="gemini",
            freshness_seconds=9999,
        )
    )

    assert payload["success"] is False
    assert "Gemini" in payload["error"]
