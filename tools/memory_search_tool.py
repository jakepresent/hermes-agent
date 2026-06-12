#!/usr/bin/env python3
"""Local file-backed memory search for durable project context.

This tool indexes auditable markdown files (ChatWorkspace + Hermes memories)
into a rebuildable SQLite FTS5 cache. Files remain the source of truth; the
SQLite DB is just a fast search index.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from hermes_constants import get_hermes_home
from tools.registry import registry, tool_error
from tools.toon_renderer import render_toon_rows

DEFAULT_INDEX_PATH = get_hermes_home() / "memory_search.sqlite"
DEFAULT_ROOTS: list[tuple[Path, str]] = [
    (Path.home() / "ChatWorkspace", "chatworkspace"),
    (get_hermes_home() / "memories", "memories"),
    (Path.home() / "LocalOps", "localops"),
]
_IGNORE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    "node_modules",
    "venv",
    ".venv",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
}
_MARKDOWN_EXTS = {".md", ".mdx", ".txt"}
_MAX_SNIPPET_CHARS = 700


def _connect(index_path: Path) -> sqlite3.Connection:
    index_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(index_path))
    con.row_factory = sqlite3.Row
    return con


def _ensure_schema(con: sqlite3.Connection) -> None:
    con.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS files (
            path TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            hash TEXT NOT NULL,
            mtime REAL NOT NULL,
            size INTEGER NOT NULL,
            indexed_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            source TEXT NOT NULL,
            start_line INTEGER NOT NULL,
            end_line INTEGER NOT NULL,
            hash TEXT NOT NULL,
            text TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            text,
            path UNINDEXED,
            source UNINDEXED,
            content='chunks',
            content_rowid='rowid'
        );
        """
    )
    # Backfill/rebuild is cheap at this scale and keeps external SQLite copies
    # from drifting if a user edited tables manually.
    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    con.commit()


def _iter_indexable_files(root: Path) -> Iterable[Path]:
    if not root.exists():
        return
    if root.is_file():
        if root.suffix.lower() in _MARKDOWN_EXTS:
            yield root
        return
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _IGNORE_DIRS and not d.startswith(".")]
        for filename in filenames:
            path = Path(dirpath) / filename
            if path.suffix.lower() in _MARKDOWN_EXTS:
                yield path


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _file_hash(path: Path) -> tuple[str, str]:
    text = path.read_text(encoding="utf-8", errors="replace")
    return _sha256_text(text), text


def _chunk_markdown(text: str, *, max_chars: int = 3000) -> list[tuple[int, int, str]]:
    """Split markdown into heading-aware chunks with 1-indexed line ranges."""
    lines = text.splitlines()
    if not lines:
        return []

    sections: list[tuple[int, int, list[str]]] = []
    start = 1
    current: list[str] = []
    for idx, line in enumerate(lines, start=1):
        if line.startswith("#") and current:
            sections.append((start, idx - 1, current))
            start = idx
            current = [line]
        else:
            current.append(line)
    if current:
        sections.append((start, len(lines), current))

    chunks: list[tuple[int, int, str]] = []
    for section_start, _section_end, section_lines in sections:
        buf: list[str] = []
        buf_start = section_start
        for offset, line in enumerate(section_lines):
            line_no = section_start + offset
            candidate = "\n".join(buf + [line]).strip()
            if buf and len(candidate) > max_chars:
                chunk_text = "\n".join(buf).strip()
                if chunk_text:
                    chunks.append((buf_start, line_no - 1, chunk_text))
                buf = [line]
                buf_start = line_no
            else:
                buf.append(line)
        chunk_text = "\n".join(buf).strip()
        if chunk_text:
            chunks.append((buf_start, section_start + len(section_lines) - 1, chunk_text))
    return chunks


def _delete_file_chunks(con: sqlite3.Connection, path: str) -> None:
    rowids = [r[0] for r in con.execute("SELECT rowid FROM chunks WHERE path = ?", (path,)).fetchall()]
    for rowid in rowids:
        con.execute("DELETE FROM chunks_fts WHERE rowid = ?", (rowid,))
    con.execute("DELETE FROM chunks WHERE path = ?", (path,))


def _index_file(con: sqlite3.Connection, path: Path, source: str) -> tuple[int, bool]:
    stat = path.stat()
    path_str = str(path)
    file_hash, text = _file_hash(path)
    existing = con.execute("SELECT hash FROM files WHERE path = ?", (path_str,)).fetchone()
    if existing and existing["hash"] == file_hash:
        return 0, False

    _delete_file_chunks(con, path_str)
    now = time.time()
    inserted = 0
    for start_line, end_line, chunk_text in _chunk_markdown(text):
        chunk_hash = _sha256_text(f"{path_str}:{start_line}:{end_line}:{chunk_text}")
        con.execute(
            """
            INSERT INTO chunks(id, path, source, start_line, end_line, hash, text, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (chunk_hash, path_str, source, start_line, end_line, chunk_hash, chunk_text, now),
        )
        rowid = con.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_hash,)).fetchone()[0]
        con.execute(
            "INSERT INTO chunks_fts(rowid, text, path, source) VALUES (?, ?, ?, ?)",
            (rowid, chunk_text, path_str, source),
        )
        inserted += 1

    con.execute(
        """
        INSERT INTO files(path, source, hash, mtime, size, indexed_at)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(path) DO UPDATE SET
            source=excluded.source,
            hash=excluded.hash,
            mtime=excluded.mtime,
            size=excluded.size,
            indexed_at=excluded.indexed_at
        """,
        (path_str, source, file_hash, stat.st_mtime, stat.st_size, now),
    )
    return inserted, True


def _normalize_roots(roots: Optional[Sequence[tuple[Path | str, str]]] = None) -> list[tuple[Path, str]]:
    normalized = []
    for root, source in (roots or DEFAULT_ROOTS):
        normalized.append((Path(root).expanduser(), str(source)))
    return normalized


def build_index(
    *,
    index_path: Path | str = DEFAULT_INDEX_PATH,
    roots: Optional[Sequence[tuple[Path | str, str]]] = None,
    force: bool = False,
) -> dict[str, Any]:
    """Build or update the local memory search index."""
    index_path = Path(index_path).expanduser()
    roots_norm = _normalize_roots(roots)
    with _connect(index_path) as con:
        _ensure_schema(con)
        if force:
            con.execute("DELETE FROM chunks_fts")
            con.execute("DELETE FROM chunks")
            con.execute("DELETE FROM files")
        indexed_files = 0
        indexed_chunks = 0
        scanned_files = 0
        roots_seen = []
        for root, source in roots_norm:
            roots_seen.append(str(root))
            for path in _iter_indexable_files(root) or []:
                scanned_files += 1
                try:
                    inserted, changed = _index_file(con, path, source)
                except (OSError, UnicodeError):
                    continue
                indexed_chunks += inserted
                if changed:
                    indexed_files += 1
        con.commit()
        total_chunks = con.execute("SELECT COUNT(*) FROM chunks").fetchone()[0]
        total_files = con.execute("SELECT COUNT(*) FROM files").fetchone()[0]
    return {
        "success": True,
        "index_path": str(index_path),
        "roots": roots_seen,
        "scanned_files": scanned_files,
        "indexed_files": indexed_files,
        "indexed_chunks": indexed_chunks,
        "total_files": total_files,
        "total_chunks": total_chunks,
    }


def _index_is_stale(index_path: Path, roots: Sequence[tuple[Path, str]], freshness_seconds: int) -> bool:
    if freshness_seconds <= 0 or not index_path.exists():
        return True
    try:
        age = time.time() - index_path.stat().st_mtime
    except OSError:
        return True
    if age > freshness_seconds:
        return True
    # Cheap mtime check: if any source file is newer than the DB, refresh.
    try:
        idx_mtime = index_path.stat().st_mtime
        for root, _source in roots:
            for path in _iter_indexable_files(root) or []:
                try:
                    if path.stat().st_mtime > idx_mtime:
                        return True
                except OSError:
                    continue
    except OSError:
        return True
    return False


def _fts_query(query: str, *, operator: str = "AND") -> str:
    terms = re.findall(r"[\w@./#:+-]+", query, flags=re.UNICODE)
    if not terms:
        return '""'
    # Quote each term so punctuation in project names doesn't turn into broken
    # FTS syntax. AND keeps recall focused; OR is used as a fallback when a
    # strict multi-term search misses but some terms may still identify context.
    op = " OR " if operator.upper() == "OR" else " AND "
    return op.join('"' + term.replace('"', '""') + '"' for term in terms[:8])


def _insert_chunk(
    con: sqlite3.Connection,
    *,
    path: str,
    source: str,
    start_line: int,
    end_line: int,
    text: str,
    seed: str,
    updated_at: float,
) -> None:
    chunk_hash = _sha256_text(seed)
    con.execute(
        """
        INSERT OR REPLACE INTO chunks(id, path, source, start_line, end_line, hash, text, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (chunk_hash, path, source, start_line, end_line, chunk_hash, text, updated_at),
    )
    rowid = con.execute("SELECT rowid FROM chunks WHERE id = ?", (chunk_hash,)).fetchone()[0]
    # INSERT OR REPLACE deletes the old content-row if present. The FTS table
    # is content-backed, so a plain INSERT is enough for new/replaced rows; a
    # manual DELETE by rowid can corrupt freshly-created content-backed FTS5
    # tables when the row has not existed in the index yet.
    con.execute(
        "INSERT INTO chunks_fts(rowid, text, path, source) VALUES (?, ?, ?, ?)",
        (rowid, text, path, source),
    )


def _query_chunks(
    con: sqlite3.Connection,
    *,
    fts: str,
    limit: int,
    source: str = "all",
    path_filter: str = "",
) -> list[sqlite3.Row]:
    params: list[Any] = [fts]
    filters = ["chunks_fts MATCH ?"]
    if source and source != "all":
        filters.append("c.source = ?")
        params.append(source)
    if path_filter:
        filters.append("c.path LIKE ?")
        params.append(f"%{path_filter}%")
    params.append(limit)
    sql = f"""
        SELECT c.path, c.source, c.start_line, c.end_line, c.text,
               bm25(chunks_fts) AS score
        FROM chunks_fts
        JOIN chunks c ON c.rowid = chunks_fts.rowid
        WHERE {' AND '.join(filters)}
        ORDER BY score
        LIMIT ?
    """
    return con.execute(sql, params).fetchall()


def _snippet(text: str, query: str) -> str:
    compact = re.sub(r"\s+", " ", text).strip()
    lower = compact.lower()
    positions = [lower.find(t.lower()) for t in re.findall(r"\w+", query) if lower.find(t.lower()) >= 0]
    if positions:
        start = max(0, min(positions) - 180)
    else:
        start = 0
    snippet = compact[start:start + _MAX_SNIPPET_CHARS]
    if start > 0:
        snippet = "…" + snippet
    if start + _MAX_SNIPPET_CHARS < len(compact):
        snippet += "…"
    return snippet


def _path_from_anchor(path: Path, anchor: str) -> str | None:
    try:
        idx = path.parts.index(anchor)
    except ValueError:
        return None
    return str(Path(*path.parts[idx:]))


def _compact_context_path(path: str, source: str) -> str:
    """Shorten local absolute paths for model-facing retrieved context."""

    if path.startswith(("openclaw://", "openclaw-session://")):
        return path

    parsed = Path(path)
    if source == "chatworkspace":
        anchored = _path_from_anchor(parsed, "ChatWorkspace")
        if anchored:
            return anchored
    if source == "localops":
        anchored = _path_from_anchor(parsed, "LocalOps")
        if anchored:
            return anchored
    if source == "memories":
        anchored = _path_from_anchor(parsed, "memories")
        if anchored:
            return anchored

    try:
        return str(parsed.expanduser().relative_to(Path.home()))
    except ValueError:
        return path


def search_index(
    query: str,
    *,
    index_path: Path | str = DEFAULT_INDEX_PATH,
    roots: Optional[Sequence[tuple[Path | str, str]]] = None,
    limit: int = 8,
    source: str = "all",
    path_filter: str = "",
    freshness_seconds: int = 60,
    render_format: str = "json",
) -> dict[str, Any]:
    query = (query or "").strip()
    if not query:
        return {"success": False, "error": "query is required"}
    render_format = (render_format or "json").strip().lower()
    if render_format not in {"json", "toon"}:
        return {"success": False, "error": "render_format must be one of: json, toon"}
    index_path = Path(index_path).expanduser()
    roots_norm = _normalize_roots(roots)
    indexed = None
    if _index_is_stale(index_path, roots_norm, freshness_seconds):
        indexed = build_index(index_path=index_path, roots=roots_norm)

    limit = max(1, min(int(limit or 8), 25))
    query_strategy = "strict_and"
    with _connect(index_path) as con:
        _ensure_schema(con)
        try:
            rows = _query_chunks(
                con,
                fts=_fts_query(query, operator="AND"),
                limit=limit,
                source=source,
                path_filter=path_filter,
            )
            if not rows and len(re.findall(r"\w+", query)) > 1:
                rows = _query_chunks(
                    con,
                    fts=_fts_query(query, operator="OR"),
                    limit=limit,
                    source=source,
                    path_filter=path_filter,
                )
                if rows:
                    query_strategy = "relaxed_or"
        except sqlite3.OperationalError:
            # If FTS parsing still fails for an odd query, fall back to a
            # simple LIKE over chunks so the tool stays useful.
            query_strategy = "like_fallback"
            like = f"%{query}%"
            like_params: list[Any] = [like]
            like_filters = ["text LIKE ?"]
            if source and source != "all":
                like_filters.append("source = ?")
                like_params.append(source)
            if path_filter:
                like_filters.append("path LIKE ?")
                like_params.append(f"%{path_filter}%")
            like_params.append(limit)
            rows = con.execute(
                f"""
                SELECT path, source, start_line, end_line, text, 0.0 AS score
                FROM chunks
                WHERE {' AND '.join(like_filters)}
                LIMIT ?
                """,
                like_params,
            ).fetchall()

    results = [
        {
            "source": row["source"],
            "path": row["path"],
            "start_line": int(row["start_line"]),
            "end_line": int(row["end_line"]),
            "score": float(row["score"]),
            "snippet": _snippet(row["text"], query),
        }
        for row in rows
    ]
    payload: dict[str, Any] = {
        "success": True,
        "query": query,
        "mode": "keyword",
        "query_strategy": query_strategy,
        "index_path": str(index_path),
        "index_updated": indexed,
        "count": len(results),
        "cache_miss_writeback": (
            "If this search recovers durable context that should have been in memory, "
            "write a tight summary to the relevant ChatWorkspace context file or Hermes memory pointer."
        ),
    }
    if render_format == "json":
        payload["results"] = results
    elif render_format == "toon":
        toon_rows = [
            {
                "source": hit["source"],
                "path": _compact_context_path(str(hit["path"]), str(hit["source"])),
                "lines": f"{hit['start_line']}-{hit['end_line']}",
                "score": round(float(hit["score"]), 3),
                "snippet": hit["snippet"],
            }
            for hit in results
        ]
        payload["render_format"] = "toon"
        payload["toon_context"] = render_toon_rows(
            "hits",
            toon_rows,
            ["source", "path", "lines", "score", "snippet"],
        )
    return payload


def import_openclaw_legacy_memory(
    *,
    legacy_db_path: Path | str = Path.home() / ".openclaw" / "memory" / "main.sqlite",
    index_path: Path | str = DEFAULT_INDEX_PATH,
) -> dict[str, Any]:
    """Import OpenClaw memory chunks into the Hermes FTS cache as legacy rows.

    This is intentionally one-way and rebuildable. The OpenClaw SQLite DB stays
    untouched; imported paths use an ``openclaw://`` prefix so hits are clearly
    legacy references rather than canonical Hermes files.
    """
    legacy_db_path = Path(legacy_db_path).expanduser()
    index_path = Path(index_path).expanduser()
    if not legacy_db_path.exists():
        return {
            "success": False,
            "error": f"OpenClaw memory DB not found: {legacy_db_path}",
        }

    imported = 0
    with sqlite3.connect(f"file:{legacy_db_path}?mode=ro", uri=True) as legacy, _connect(index_path) as con:
        legacy.row_factory = sqlite3.Row
        _ensure_schema(con)
        # Replace prior legacy import atomically so repeated imports track the
        # legacy source without duplicate rows.
        legacy_paths = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT path FROM chunks WHERE source = 'openclaw_legacy'"
            ).fetchall()
        ]
        for path in legacy_paths:
            _delete_file_chunks(con, path)
        con.execute("DELETE FROM files WHERE source = 'openclaw_legacy'")

        rows = legacy.execute(
            """
            SELECT path, start_line, end_line, hash, text, updated_at
            FROM chunks
            WHERE text IS NOT NULL AND TRIM(text) != ''
            ORDER BY path, start_line
            """
        )
        now = time.time()
        files: dict[str, dict[str, Any]] = {}
        for row in rows:
            legacy_path = f"openclaw://{row['path']}"
            text = str(row["text"])
            _insert_chunk(
                con,
                path=legacy_path,
                source="openclaw_legacy",
                start_line=int(row["start_line"]),
                end_line=int(row["end_line"]),
                text=text,
                seed=f"openclaw:{row['hash']}:{legacy_path}:{row['start_line']}:{row['end_line']}",
                updated_at=float(row["updated_at"] or now),
            )
            imported += 1
            rec = files.setdefault(legacy_path, {"size": 0, "hashes": []})
            rec["size"] += len(text.encode("utf-8", errors="replace"))
            rec["hashes"].append(str(row["hash"]))

        for legacy_path, rec in files.items():
            combined_hash = _sha256_text("\n".join(rec["hashes"]))
            con.execute(
                """
                INSERT INTO files(path, source, hash, mtime, size, indexed_at)
                VALUES (?, 'openclaw_legacy', ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    source=excluded.source,
                    hash=excluded.hash,
                    mtime=excluded.mtime,
                    size=excluded.size,
                    indexed_at=excluded.indexed_at
                """,
                (legacy_path, combined_hash, now, int(rec["size"]), now),
            )
        con.commit()
    return {
        "success": True,
        "legacy_db_path": str(legacy_db_path),
        "index_path": str(index_path),
        "imported_chunks": imported,
        "imported_files": len(files),
    }


def _extract_openclaw_content(content: Any) -> str:
    """Extract text from OpenClaw message content shapes."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, dict):
                text = part.get("text")
                if text:
                    parts.append(str(text))
            elif isinstance(part, str):
                parts.append(part)
        return "\n".join(parts)
    if content is None:
        return ""
    return str(content)


def _strip_openclaw_gateway_metadata(text: str) -> str:
    """Remove gateway metadata wrappers so search emphasizes the actual turn."""
    text = re.sub(
        r"\AConversation info \(untrusted metadata\):\n```json\n[\s\S]*?\n```\n\n"
        r"Sender \(untrusted metadata\):\n```json\n[\s\S]*?\n```\n\n",
        "",
        text,
    )
    return text.strip()


def _iter_openclaw_session_files(sessions_root: Path) -> Iterable[Path]:
    """Yield canonical OpenClaw session JSONL files, excluding noisy traces/checkpoints."""
    if not sessions_root.exists():
        return
    patterns = ["*/sessions/*.jsonl"] if sessions_root.name == "agents" else ["*.jsonl", "*/sessions/*.jsonl"]
    seen: set[Path] = set()
    for pattern in patterns:
        for path in sessions_root.glob(pattern):
            if path in seen:
                continue
            seen.add(path)
            name = path.name
            if name.endswith(".trajectory.jsonl") or ".checkpoint." in name:
                continue
            yield path


def import_openclaw_legacy_sessions(
    *,
    sessions_root: Path | str = Path.home() / ".openclaw" / "agents",
    index_path: Path | str = DEFAULT_INDEX_PATH,
    max_chunk_chars: int = 6000,
) -> dict[str, Any]:
    """Import OpenClaw session JSONL turns into the Hermes FTS cache.

    This indexes user/assistant messages only. Tool results and trajectory files
    are intentionally skipped because they are noisy and more likely to contain
    raw external payloads or secrets. Paths use ``openclaw-session://`` so hits
    are clearly legacy references rather than canonical Hermes memory files.
    """
    sessions_root = Path(sessions_root).expanduser()
    index_path = Path(index_path).expanduser()
    if not sessions_root.exists():
        return {"success": False, "error": f"OpenClaw sessions root not found: {sessions_root}"}

    imported_files = 0
    imported_chunks = 0
    skipped_files = 0
    now = time.time()
    with _connect(index_path) as con:
        _ensure_schema(con)
        legacy_paths = [
            r[0]
            for r in con.execute(
                "SELECT DISTINCT path FROM chunks WHERE source = 'legacy_sessions'"
            ).fetchall()
        ]
        for path in legacy_paths:
            _delete_file_chunks(con, path)
        con.execute("DELETE FROM files WHERE source = 'legacy_sessions'")

        for session_file in _iter_openclaw_session_files(sessions_root) or []:
            agent_name = "unknown"
            try:
                if session_file.parent.name == "sessions":
                    agent_name = session_file.parent.parent.name
            except Exception:
                pass
            session_id = session_file.stem
            path_uri = f"openclaw-session://{agent_name}/{session_id}"
            turns: list[str] = []
            session_timestamp = ""
            try:
                with session_file.open("r", encoding="utf-8", errors="replace") as fh:
                    for line_no, line in enumerate(fh, start=1):
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            obj = json.loads(line)
                        except json.JSONDecodeError:
                            continue
                        if obj.get("type") == "session":
                            session_timestamp = str(obj.get("timestamp") or "")
                            session_id = str(obj.get("id") or session_id)
                            path_uri = f"openclaw-session://{agent_name}/{session_id}"
                            continue
                        if obj.get("type") != "message":
                            continue
                        msg = obj.get("message") or {}
                        role = str(msg.get("role") or "")
                        if role not in {"user", "assistant"}:
                            continue
                        text = _strip_openclaw_gateway_metadata(_extract_openclaw_content(msg.get("content")))
                        if not text:
                            continue
                        timestamp = str(obj.get("timestamp") or "")
                        turns.append(f"[{timestamp} line {line_no} {role}]\n{text}")
            except OSError:
                skipped_files += 1
                continue

            if not turns:
                skipped_files += 1
                continue

            header = f"OpenClaw legacy session {session_id} ({agent_name})"
            if session_timestamp:
                header += f"\nStarted: {session_timestamp}"
            text = header + "\n\n" + "\n\n---\n\n".join(turns)
            chunks = _chunk_markdown(text, max_chars=max_chunk_chars)
            if not chunks:
                skipped_files += 1
                continue
            file_hash = _sha256_text(text)
            for start_line, end_line, chunk_text in chunks:
                _insert_chunk(
                    con,
                    path=path_uri,
                    source="legacy_sessions",
                    start_line=start_line,
                    end_line=end_line,
                    text=chunk_text,
                    seed=f"legacy_sessions:{file_hash}:{path_uri}:{start_line}:{end_line}",
                    updated_at=now,
                )
                imported_chunks += 1
            con.execute(
                """
                INSERT INTO files(path, source, hash, mtime, size, indexed_at)
                VALUES (?, 'legacy_sessions', ?, ?, ?, ?)
                ON CONFLICT(path) DO UPDATE SET
                    source=excluded.source,
                    hash=excluded.hash,
                    mtime=excluded.mtime,
                    size=excluded.size,
                    indexed_at=excluded.indexed_at
                """,
                (path_uri, file_hash, session_file.stat().st_mtime, session_file.stat().st_size, now),
            )
            imported_files += 1
        con.commit()
    return {
        "success": True,
        "sessions_root": str(sessions_root),
        "index_path": str(index_path),
        "imported_files": imported_files,
        "imported_chunks": imported_chunks,
        "skipped_files": skipped_files,
    }


def memory_search_tool(
    query: str,
    source: str = "all",
    path_filter: str = "",
    limit: int = 8,
    freshness_seconds: int = 60,
    render_format: str = "json",
    index_path: Path | str = DEFAULT_INDEX_PATH,
    roots: Optional[Sequence[tuple[Path | str, str]]] = None,
    task_id: str | None = None,
) -> str:
    del task_id
    try:
        return json.dumps(
            search_index(
                query,
                index_path=index_path,
                roots=roots,
                limit=limit,
                source=source,
                path_filter=path_filter,
                freshness_seconds=freshness_seconds,
                render_format=render_format,
            ),
            ensure_ascii=False,
        )
    except Exception as exc:  # noqa: BLE001 — tool boundary returns JSON errors
        return tool_error(f"memory_search failed: {exc}", success=False)


def check_memory_search_requirements() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.execute("CREATE VIRTUAL TABLE t USING fts5(x)")
        con.close()
        return True
    except Exception:
        return False


MEMORY_SEARCH_SCHEMA = {
    "name": "memory_search",
    "description": (
        "Search Jake's durable, auditable memory files without using the browser. "
        "Indexes ~/ChatWorkspace and ~/.hermes/memories into a rebuildable SQLite FTS cache. "
        "Use this as the cache tier when current context is missing prior/project context. "
        "If it finds durable context that should have been saved already, treat that as a cache miss "
        "and write back a tight summary to the relevant ChatWorkspace context file or Hermes memory pointer."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search terms."},
            "source": {
                "type": "string",
                "enum": ["all", "chatworkspace", "memories", "localops", "openclaw_legacy", "legacy_sessions", "discord"],
                "description": "Optional source filter. v1 indexes chatworkspace, Hermes memories, and LocalOps; other sources are reserved for migration follow-ups.",
            },
            "path_filter": {"type": "string", "description": "Optional substring filter for result paths, e.g. 'ngng' or 'microsoft/work_context'."},
            "limit": {"type": "integer", "description": "Maximum results, 1-25. Default 8."},
            "render_format": {
                "type": "string",
                "enum": ["json", "toon"],
                "description": "Optional model-facing context format. Use 'toon' for compact retrieval context; default 'json' preserves the existing output shape.",
            },
        },
        "required": ["query"],
    },
}


registry.register(
    name="memory_search",
    toolset="memory_search",
    schema=MEMORY_SEARCH_SCHEMA,
    handler=lambda args, **kw: memory_search_tool(
        query=args.get("query", ""),
        source=args.get("source", "all"),
        path_filter=args.get("path_filter", ""),
        limit=args.get("limit", 8),
        render_format=args.get("render_format", "json"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_memory_search_requirements,
    emoji="🔎",
)
