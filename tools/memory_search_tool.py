#!/usr/bin/env python3
"""Local file-backed memory search for durable project context.

This tool indexes auditable markdown files (ChatWorkspace + Hermes memories)
into a rebuildable SQLite FTS5 cache. Files remain the source of truth; the
SQLite DB is just a fast search index.
"""

from __future__ import annotations

import array
import hashlib
import json
import os
import pickle
from collections import OrderedDict
import re
import sqlite3
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Iterable, List, Optional, Sequence, Tuple

from hermes_constants import get_hermes_home
from tools.registry import registry, tool_error
from tools.toon_renderer import render_toon_rows

DEFAULT_INDEX_PATH = get_hermes_home() / "memory_search.sqlite"
DEFAULT_SEMANTIC_INDEX_PATH = get_hermes_home() / "memory_search_semantic.pkl"
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

# Schema version drives one-time, non-destructive migrations (e.g. the
# observation backfill added in v2). Bump when the on-disk index shape changes.
_SCHEMA_VERSION = 4
# Observation lines look like "- [category] content #tags". The category must be
# >=2 word chars starting with a letter so markdown checkboxes ("- [ ]", "- [x]")
# are NOT misread as observations. This mirrors basic-memory's observation syntax.
_OBSERVATION_RE = re.compile(r"^\s*[-*+]\s*\[([A-Za-z][\w-]+)\]\s+(\S.*?)\s*$")
_TAG_RE = re.compile(r"(?:^|\s)#([\w-]+)")


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
        CREATE TABLE IF NOT EXISTS meta (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS observations (
            id TEXT PRIMARY KEY,
            path TEXT NOT NULL,
            source TEXT NOT NULL,
            line INTEGER NOT NULL,
            category TEXT NOT NULL,
            tags TEXT NOT NULL,
            text TEXT NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE INDEX IF NOT EXISTS observations_path ON observations(path);
        CREATE INDEX IF NOT EXISTS observations_category ON observations(category);
        CREATE VIRTUAL TABLE IF NOT EXISTS observations_fts USING fts5(
            text,
            category UNINDEXED,
            tags,
            path UNINDEXED,
            source UNINDEXED,
            content='observations',
            content_rowid='rowid'
        );
        CREATE TABLE IF NOT EXISTS semantic_embeddings (
            row_id TEXT NOT NULL,
            granularity TEXT NOT NULL,
            backend TEXT NOT NULL,
            model TEXT NOT NULL,
            content_hash TEXT NOT NULL,
            path TEXT NOT NULL,
            source TEXT NOT NULL,
            embedding BLOB NOT NULL,
            dim INTEGER NOT NULL,
            vec_id INTEGER,
            updated_at REAL NOT NULL,
            PRIMARY KEY (row_id, granularity, backend, model)
        );
        CREATE INDEX IF NOT EXISTS semantic_embeddings_lookup
            ON semantic_embeddings(granularity, backend, model, row_id);
        CREATE INDEX IF NOT EXISTS semantic_embeddings_path
            ON semantic_embeddings(path);
        """
    )
    _migrate_schema(con)
    row = con.execute("SELECT value FROM meta WHERE key = 'fts_rebuild_schema_version'").fetchone()
    if not row or str(row[0]) != str(_SCHEMA_VERSION):
        # Rebuild once per schema version. The FTS tables are maintained
        # incrementally by build/import paths; rebuilding on every search adds
        # hundreds of milliseconds to hot memory lookups.
        con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
        con.execute("INSERT INTO observations_fts(observations_fts) VALUES('rebuild')")
        con.execute(
            "INSERT INTO meta(key, value) VALUES ('fts_rebuild_schema_version', ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (str(_SCHEMA_VERSION),),
        )
        con.commit()


def _schema_version(con: sqlite3.Connection) -> int:
    row = con.execute("SELECT value FROM meta WHERE key = 'schema_version'").fetchone()
    if not row:
        return 0
    try:
        return int(row[0])
    except (TypeError, ValueError):
        return 0


def _set_schema_version(con: sqlite3.Connection, version: int) -> None:
    con.execute(
        "INSERT INTO meta(key, value) VALUES ('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (str(version),),
    )


def _migrate_schema(con: sqlite3.Connection) -> None:
    """One-time, non-destructive migrations keyed on meta.schema_version.

    v2 introduces the observations layer. Existing chunks and legacy imports are
    preserved. Observations are backfilled by re-reading real files from disk
    (exact line numbers); virtual legacy paths with no file on disk fall back to
    their stored chunk text.
    """
    version = _schema_version(con)
    if version >= _SCHEMA_VERSION:
        return
    if version < 2:
        con.execute("DELETE FROM observations_fts")
        con.execute("DELETE FROM observations")
        now = time.time()
        # Real on-disk files: re-read source for exact 1-indexed line numbers.
        file_rows = con.execute("SELECT path, source FROM files").fetchall()
        for row in file_rows:
            path_str = str(row["path"])
            if path_str.startswith(("openclaw://", "openclaw-session://")):
                continue
            try:
                text = Path(path_str).read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            _index_observations(
                con, path=path_str, source=str(row["source"]),
                base_line=1, text=text, updated_at=now,
            )
        # Virtual legacy paths (no file on disk): backfill from stored chunks.
        legacy_chunks = con.execute(
            "SELECT path, source, start_line, text, updated_at FROM chunks "
            "WHERE path LIKE 'openclaw://%' OR path LIKE 'openclaw-session://%'"
        ).fetchall()
        for row in legacy_chunks:
            _index_observations(
                con, path=str(row["path"]), source=str(row["source"]),
                base_line=int(row["start_line"]), text=str(row["text"]),
                updated_at=float(row["updated_at"] or now),
            )
    if version < 4:
        # v4 adds a numeric vec_id used as sqlite-vec's rowid. Older databases
        # may have been created before the column existed, so keep this idempotent.
        columns = {row[1] for row in con.execute("PRAGMA table_info(semantic_embeddings)").fetchall()}
        if "vec_id" not in columns:
            con.execute("ALTER TABLE semantic_embeddings ADD COLUMN vec_id INTEGER")
        con.execute(
            """
            CREATE UNIQUE INDEX IF NOT EXISTS semantic_embeddings_vec_id
                ON semantic_embeddings(vec_id) WHERE vec_id IS NOT NULL
            """
        )
        for row in con.execute(
            "SELECT row_id, granularity, backend, model FROM semantic_embeddings "
            "WHERE vec_id IS NULL ORDER BY updated_at, row_id"
        ).fetchall():
            con.execute(
                """
                UPDATE semantic_embeddings
                SET vec_id = ?
                WHERE row_id = ? AND granularity = ? AND backend = ? AND model = ?
                """,
                (
                    _semantic_vec_id(str(row["row_id"]), str(row["granularity"]), str(row["backend"]), str(row["model"])),
                    row["row_id"], row["granularity"], row["backend"], row["model"],
                ),
            )
    _set_schema_version(con, _SCHEMA_VERSION)
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
    stale_vec_ids = [
        int(r[0]) for r in con.execute(
            "SELECT vec_id FROM semantic_embeddings WHERE path = ? AND vec_id IS NOT NULL",
            (path,),
        ).fetchall()
    ]
    if stale_vec_ids:
        for table_row in con.execute(
            """
            SELECT name FROM sqlite_master
            WHERE type = 'table'
              AND name LIKE 'semantic_vec_%'
              AND lower(sql) LIKE '%using vec0%'
            """
        ).fetchall():
            table_name = str(table_row["name"] if isinstance(table_row, sqlite3.Row) else table_row[0])
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
                continue
            for vec_id in stale_vec_ids:
                try:
                    con.execute(f"DELETE FROM {table_name} WHERE rowid = ?", (vec_id,))
                except sqlite3.DatabaseError:
                    pass
    con.execute("DELETE FROM semantic_embeddings WHERE path = ?", (path,))
    _delete_file_observations(con, path)


def _delete_file_observations(con: sqlite3.Connection, path: str) -> None:
    rowids = [r[0] for r in con.execute("SELECT rowid FROM observations WHERE path = ?", (path,)).fetchall()]
    for rowid in rowids:
        con.execute("DELETE FROM observations_fts WHERE rowid = ?", (rowid,))
    con.execute("DELETE FROM observations WHERE path = ?", (path,))


def _index_observations(
    con: sqlite3.Connection,
    *,
    path: str,
    source: str,
    base_line: int,
    text: str,
    updated_at: float,
) -> int:
    """Index `- [category] content #tags` lines as individually searchable facts.

    `base_line` is the absolute 1-indexed line of the first line of `text` in the
    source file, so observation line numbers point at the real file location.
    Markdown checkboxes are excluded by _OBSERVATION_RE's category rule.
    """
    inserted = 0
    for offset, line in enumerate(text.splitlines()):
        match = _OBSERVATION_RE.match(line)
        if not match:
            continue
        category = match.group(1).lower()
        body = match.group(2).strip()
        if not body:
            continue
        abs_line = base_line + offset
        tags = " ".join(sorted({t.lower() for t in _TAG_RE.findall(body)}))
        obs_id = _sha256_text(f"obs:{path}:{abs_line}:{category}:{body}")
        con.execute(
            """
            INSERT OR REPLACE INTO observations(id, path, source, line, category, tags, text, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (obs_id, path, source, abs_line, category, tags, body, updated_at),
        )
        rowid = con.execute("SELECT rowid FROM observations WHERE id = ?", (obs_id,)).fetchone()[0]
        con.execute(
            "INSERT INTO observations_fts(rowid, text, category, tags, path, source) VALUES (?, ?, ?, ?, ?, ?)",
            (rowid, body, category, tags, path, source),
        )
        inserted += 1
    return inserted


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

    # Index observations from the full original file text so line numbers are
    # exact (chunk text is strip()ed, which would skew per-chunk line offsets).
    _index_observations(
        con,
        path=path_str,
        source=source,
        base_line=1,
        text=text,
        updated_at=now,
    )

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
            con.execute("DELETE FROM observations_fts")
            con.execute("DELETE FROM observations")
            con.execute("DELETE FROM semantic_embeddings")
            con.execute("DELETE FROM files")
            con.execute("DELETE FROM meta WHERE key = 'fts_rebuild_schema_version'")
        indexed_files = 0
        indexed_chunks = 0
        scanned_files = 0
        roots_seen = []
        seen_paths: set[str] = set()
        for root, source in roots_norm:
            roots_seen.append(str(root))
            for path in _iter_indexable_files(root) or []:
                scanned_files += 1
                path_str = str(path)
                seen_paths.add(path_str)
                try:
                    inserted, changed = _index_file(con, path, source)
                except (OSError, UnicodeError):
                    continue
                indexed_chunks += inserted
                if changed:
                    indexed_files += 1

        # The SQLite index is a rebuildable cache over files. Remove rows for
        # source files that no longer exist under the live on-disk roots so git
        # moves/deletes do not leave stale search hits behind. Virtual imported
        # sources (OpenClaw legacy memory/sessions) are left alone because they
        # are refreshed by their importers, not by filesystem scans.
        deleted_files = 0
        real_sources = {source for _root, source in roots_norm}
        if real_sources:
            placeholders = ",".join("?" for _ in real_sources)
            file_rows = con.execute(
                f"SELECT path FROM files WHERE source IN ({placeholders})",
                tuple(real_sources),
            ).fetchall()
            for row in file_rows:
                stale_path = str(row["path"])
                if stale_path not in seen_paths:
                    _delete_file_chunks(con, stale_path)
                    con.execute("DELETE FROM files WHERE path = ?", (stale_path,))
                    deleted_files += 1
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
        "deleted_files": deleted_files,
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
    _index_observations(
        con,
        path=path,
        source=source,
        base_line=start_line,
        text=text,
        updated_at=updated_at,
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


def _query_observations(
    con: sqlite3.Connection,
    *,
    fts: str,
    limit: int,
    source: str = "all",
    path_filter: str = "",
    category: str = "",
) -> list[sqlite3.Row]:
    """Return individual observation lines ranked by relevance.

    When `fts` is empty (no query terms), fall back to a metadata-only listing
    filtered by category/source/path so "show me all [decision] facts" works
    without a search term.
    """
    params: list[Any] = []
    filters: list[str] = []
    use_fts = bool(fts and fts != '""')
    if use_fts:
        filters.append("observations_fts MATCH ?")
        params.append(fts)
    if source and source != "all":
        filters.append("o.source = ?")
        params.append(source)
    if path_filter:
        filters.append("o.path LIKE ?")
        params.append(f"%{path_filter}%")
    if category:
        filters.append("o.category = ?")
        params.append(category.lower())
    where = (" WHERE " + " AND ".join(filters)) if filters else ""
    order = "ORDER BY score" if use_fts else "ORDER BY o.updated_at DESC"
    score_expr = "bm25(observations_fts)" if use_fts else "0.0"
    join = "JOIN observations o ON o.rowid = observations_fts.rowid" if use_fts else ""
    table = "observations_fts" if use_fts else "observations o"
    params.append(limit)
    sql = f"""
        SELECT o.path, o.source, o.line, o.category, o.tags, o.text,
               {score_expr} AS score
        FROM {table}
        {join}
        {where}
        {order}
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


def _semantic_index_path(index_path: Path | str, granularity: str, backend: str) -> Path:
    """Return the sidecar semantic cache path for a SQLite memory index."""
    path = Path(index_path).expanduser()
    safe_granularity = re.sub(r"[^A-Za-z0-9_-]+", "_", granularity or "chunk")
    safe_backend = re.sub(r"[^A-Za-z0-9_-]+", "_", backend or "sklearn")
    if path == DEFAULT_INDEX_PATH:
        return get_hermes_home() / f"memory_search_semantic_{safe_backend}_{safe_granularity}.pkl"
    return path.with_suffix(path.suffix + f".{safe_backend}.{safe_granularity}.semantic.pkl")


def _semantic_row_id(row: sqlite3.Row, granularity: str) -> str:
    if granularity == "observation":
        return f"obs:{row['path']}:{row['line']}:{row['category']}:{row['text']}"
    return f"chunk:{row['path']}:{row['start_line']}:{row['end_line']}:{row['text']}"


def _semantic_candidate_rows_uncached(
    con: sqlite3.Connection,
    *,
    granularity: str,
    source: str = "all",
    path_filter: str = "",
    category: str = "",
) -> list[sqlite3.Row]:
    params: list[Any] = []
    if granularity == "observation":
        filters: list[str] = []
        if source and source != "all":
            filters.append("source = ?")
            params.append(source)
        if path_filter:
            filters.append("path LIKE ?")
            params.append(f"%{path_filter}%")
        if category:
            filters.append("category = ?")
            params.append(category.lower())
        where = (" WHERE " + " AND ".join(filters)) if filters else ""
        return con.execute(
            f"""
            SELECT path, source, line, category, tags, text, updated_at
            FROM observations
            {where}
            ORDER BY updated_at DESC
            """,
            params,
        ).fetchall()

    filters = []
    if source and source != "all":
        filters.append("source = ?")
        params.append(source)
    if path_filter:
        filters.append("path LIKE ?")
        params.append(f"%{path_filter}%")
    where = (" WHERE " + " AND ".join(filters)) if filters else ""
    return con.execute(
        f"""
        SELECT path, source, start_line, end_line, text, updated_at
        FROM chunks
        {where}
        ORDER BY updated_at DESC
        """,
        params,
    ).fetchall()


def _semantic_candidate_rows(
    con: sqlite3.Connection,
    *,
    granularity: str,
    source: str = "all",
    path_filter: str = "",
    category: str = "",
) -> list[sqlite3.Row]:
    key = (granularity, source or "all", path_filter or "", category or "")
    db_mtime = 0.0
    try:
        row = con.execute("PRAGMA database_list").fetchone()
        db_path = str(row[2]) if row and len(row) >= 3 else ""
        db_mtime = Path(db_path).stat().st_mtime if db_path else 0.0
    except Exception:
        db_mtime = 0.0
    cached = _SEMANTIC_CANDIDATE_ROWS_CACHE.get(key)
    if cached and cached[0] == db_mtime:
        _SEMANTIC_CANDIDATE_ROWS_CACHE.move_to_end(key)
        return list(cached[1])
    rows = _semantic_candidate_rows_uncached(
        con,
        granularity=granularity,
        source=source,
        path_filter=path_filter,
        category=category,
    )
    _SEMANTIC_CANDIDATE_ROWS_CACHE[key] = (db_mtime, list(rows))
    while len(_SEMANTIC_CANDIDATE_ROWS_CACHE) > _SEMANTIC_CANDIDATE_ROWS_CACHE_MAX:
        _SEMANTIC_CANDIDATE_ROWS_CACHE.popitem(last=False)
    return rows


def _semantic_fingerprint(rows: Sequence[sqlite3.Row], *, granularity: str, backend: str) -> str:
    h = hashlib.sha256()
    h.update(f"semantic-v1:{backend}:{granularity}\n".encode("utf-8"))
    for row in rows:
        h.update(_semantic_row_id(row, granularity).encode("utf-8", errors="replace"))
        h.update(b"\0")
    return h.hexdigest()


def _semantic_documents(rows: Sequence[sqlite3.Row], granularity: str) -> list[str]:
    docs: list[str] = []
    for row in rows:
        text = str(row["text"])
        if granularity == "observation":
            # Include metadata terms in the vectorized text so fuzzy searches can
            # match project/category/tag intent even when the fact wording is short.
            docs.append(f"{row['category']} {row['tags']} {row['path']} {text}")
        else:
            docs.append(f"{row['path']} {text}")
    return docs


def _build_semantic_model(texts: Sequence[str]) -> dict[str, Any]:
    """Build a local lexical-semantic vector cache.

    This deliberately uses sklearn TF-IDF + truncated SVD (LSA) instead of a
    network embedding provider. It is rebuildable, private, works offline, and
    can later be swapped for neural embeddings behind the same search mode.
    """
    try:
        from sklearn.decomposition import TruncatedSVD
        from sklearn.feature_extraction.text import TfidfVectorizer
        from sklearn.preprocessing import normalize
    except Exception as exc:  # pragma: no cover - depends on optional runtime deps
        raise RuntimeError(
            "semantic search requires scikit-learn; install sklearn or use mode='keyword'"
        ) from exc

    vectorizer = TfidfVectorizer(
        lowercase=True,
        stop_words="english",
        ngram_range=(1, 2),
        min_df=1,
        max_features=50000,
        sublinear_tf=True,
    )
    tfidf = vectorizer.fit_transform(texts)
    n_components = min(128, max(1, tfidf.shape[0] - 1), max(1, tfidf.shape[1] - 1))
    if n_components >= 2 and tfidf.shape[0] >= 3 and tfidf.shape[1] >= 3:
        svd = TruncatedSVD(n_components=n_components, random_state=0)
        matrix = normalize(svd.fit_transform(tfidf))
    else:
        svd = None
        matrix = normalize(tfidf)
    return {
        "backend": "sklearn_lsa_v1" if svd is not None else "sklearn_tfidf_v1",
        "vectorizer": vectorizer,
        "svd": svd,
        "matrix": matrix,
    }


def _gemini_api_key() -> str:
    """Resolve Gemini API key from the live environment or Hermes .env."""
    for key in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
        value = os.getenv(key)
        if value:
            return value
    env_path = get_hermes_home() / ".env"
    try:
        for line in env_path.read_text(encoding="utf-8", errors="replace").splitlines():
            stripped = line.strip()
            if not stripped or stripped.startswith("#") or "=" not in stripped:
                continue
            name, value = stripped.split("=", 1)
            name = name.replace("export ", "").strip()
            if name not in {"GEMINI_API_KEY", "GOOGLE_API_KEY"}:
                continue
            value = value.strip().strip('"').strip("'")
            if value:
                return value
    except OSError:
        pass
    return ""


def _prepare_gemini_embedding_text(text: str, *, is_query: bool, title: str = "none") -> str:
    if is_query:
        return f"task: search result | query: {text}"
    clean_title = title or "none"
    return f"title: {clean_title} | text: {text}"


_GEMINI_REQUEST_TIMEOUT_MS = 8000
_GEMINI_BATCH_SIZE = 100
# Default memory_search runs inside a live chat turn. Rebuilding a full Gemini
# vector cache is allowed because Gemini's batchEmbedContents endpoint supports
# up to 100 separate inputs per request, but keep an override for emergency
# fallback if the provider starts failing or rate-limiting.
_GEMINI_DEFAULT_MAX_COLD_ROWS = 25000
_SKLEARN_DEFAULT_MAX_COLD_ROWS = 5000
_GEMINI_QUERY_CACHE_MAX = 256
_GEMINI_QUERY_EMBEDDING_CACHE: "OrderedDict[tuple[str, str], list[float]]" = OrderedDict()
_GEMINI_ROW_REF_CACHE_MAX = 8
_GEMINI_ROW_REF_CACHE: "OrderedDict[tuple[str, str, str, str, str, str, str], tuple[str, list[dict[str, Any] | None], list[int]]]" = OrderedDict()
_SEMANTIC_CANDIDATE_ROWS_CACHE_MAX = 8
_SEMANTIC_CANDIDATE_ROWS_CACHE: "OrderedDict[tuple[str, str, str, str], tuple[float, list[sqlite3.Row]]]" = OrderedDict()
_SQLITE_VEC_ID_CACHE_MAX = 8
_SQLITE_VEC_ID_CACHE: "OrderedDict[tuple[str, str, str, str, str, str, str], tuple[str, list[int]]]" = OrderedDict()
_SQLITE_VEC_READY_CACHE: "OrderedDict[tuple[str, str, str, str, str], tuple[float, dict[str, Any]]]" = OrderedDict()
_SQLITE_VEC_READY_CACHE_MAX = 16


def _extract_gemini_values(embedding: Any) -> list[float]:
    values = getattr(embedding, "values", None) or getattr(embedding, "embedding", None) or []
    if isinstance(values, dict):
        values = values.get("values", [])
    return [float(v) for v in values]


def _embed_gemini_texts(texts: Sequence[str], *, model: str) -> list[list[float]]:
    api_key = _gemini_api_key()
    if not api_key:
        raise RuntimeError("Gemini semantic search requires GEMINI_API_KEY or GOOGLE_API_KEY in the environment or ~/.hermes/.env")
    try:
        from google import genai
        from google.genai import types
    except Exception as exc:  # pragma: no cover - optional dependency
        raise RuntimeError("Gemini semantic search requires the google-genai package") from exc

    timeout_ms = int(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_TIMEOUT_MS", str(_GEMINI_REQUEST_TIMEOUT_MS)))
    client = genai.Client(
        api_key=api_key,
        http_options=types.HttpOptions(
            timeout=timeout_ms,
            retry_options=types.HttpRetryOptions(attempts=1),
        ),
    )
    vectors: list[list[float]] = []
    for text in texts:
        response = client.models.embed_content(
            model=model,
            contents=text,
            config=types.EmbedContentConfig(
                http_options=types.HttpOptions(
                    timeout=timeout_ms,
                    retry_options=types.HttpRetryOptions(attempts=1),
                ),
            ),
        )
        embeddings = getattr(response, "embeddings", None) or []
        if not embeddings:
            vectors.append([])
            continue
        vectors.append(_extract_gemini_values(embeddings[0]))
    return vectors


def _embed_gemini_texts_batched(texts: Sequence[str], *, model: str, max_retries: int = 0) -> list[list[float]]:
    api_key = _gemini_api_key()
    if not api_key:
        raise RuntimeError("Gemini semantic search requires GEMINI_API_KEY or GOOGLE_API_KEY in the environment or ~/.hermes/.env")
    timeout_ms = int(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_TIMEOUT_MS", str(_GEMINI_REQUEST_TIMEOUT_MS)))
    batch_size = max(1, min(int(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_BATCH_SIZE", str(_GEMINI_BATCH_SIZE))), 100))
    endpoint = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:batchEmbedContents"
    vectors: list[list[float]] = []
    for start in range(0, len(texts), batch_size):
        chunk = list(texts[start:start + batch_size])
        requests = [
            {
                "model": f"models/{model}",
                "content": {"parts": [{"text": text}]},
            }
            for text in chunk
        ]
        payload = json.dumps({"requests": requests}).encode("utf-8")
        req = urllib.request.Request(
            endpoint,
            data=payload,
            headers={"Content-Type": "application/json", "x-goog-api-key": api_key},
            method="POST",
        )
        attempt = 0
        while True:
            try:
                with urllib.request.urlopen(req, timeout=max(1, timeout_ms / 1000)) as resp:
                    body = json.loads(resp.read().decode("utf-8"))
                break
            except urllib.error.HTTPError as exc:
                try:
                    detail = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    detail = str(exc)
                if exc.code == 429 and attempt < max_retries:
                    retry_after = 1.0
                    try:
                        parsed = json.loads(detail)
                        message = str(parsed.get("error", {}).get("message") or "")
                        match = re.search(r"retry in ([0-9.]+)s", message, flags=re.IGNORECASE)
                        if match:
                            retry_after = max(1.0, min(float(match.group(1)), 65.0))
                    except Exception:
                        retry_after = 5.0
                    time.sleep(retry_after)
                    attempt += 1
                    continue
                raise RuntimeError(f"Gemini batch embedding failed ({exc.code}): {detail[:500]}") from exc
        embeddings = body.get("embeddings") or []
        if len(embeddings) != len(chunk):
            raise RuntimeError(f"Gemini batch embedding returned {len(embeddings)} vectors for {len(chunk)} inputs")
        for embedding in embeddings:
            values = embedding.get("values") if isinstance(embedding, dict) else _extract_gemini_values(embedding)
            vectors.append([float(v) for v in (values or [])])
    return vectors


def _normalize_dense_matrix(vectors: Sequence[Sequence[float]]):
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - numpy is expected in Hermes env
        raise RuntimeError("Gemini semantic search requires numpy for local vector scoring") from exc
    matrix = np.asarray(vectors, dtype="float32")
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise RuntimeError("Gemini embedding backend returned empty vectors")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms




def _semantic_content_hash(row: sqlite3.Row, granularity: str, backend_id: str) -> str:
    return _sha256_text(f"semantic-content:{backend_id}:{granularity}:{_semantic_row_id(row, granularity)}")


def _row_map_key(row: sqlite3.Row, granularity: str) -> str:
    return _semantic_row_id(row, granularity)


def _semantic_vec_id(row_id: str, granularity: str, backend_id: str, model_name: str) -> int:
    """Stable positive 63-bit integer id for sqlite-vec rowids."""
    seed = f"{granularity}\0{backend_id}\0{model_name}\0{row_id}".encode("utf-8", errors="replace")
    value = int.from_bytes(hashlib.blake2b(seed, digest_size=8).digest(), "big") & ((1 << 63) - 1)
    return value or 1


def _load_sqlite_vec(con: sqlite3.Connection) -> tuple[bool, str]:
    """Load sqlite-vec into this connection when available."""
    try:
        import sqlite_vec  # type: ignore
    except Exception as exc:  # pragma: no cover - optional dependency
        return False, f"sqlite-vec unavailable: {exc}"
    try:
        con.enable_load_extension(True)
        sqlite_vec.load(con)
    except Exception as exc:  # pragma: no cover - environment dependent
        try:
            con.enable_load_extension(False)
        except Exception:
            pass
        return False, f"sqlite-vec load failed: {exc}"
    try:
        con.enable_load_extension(False)
    except Exception:
        pass
    return True, ""


def _vec_table_name(granularity: str, model_name: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]+", "_", f"{granularity}_{model_name}").strip("_")
    return f"semantic_vec_{safe[:80]}"


def _sqlite_vec_table_exists(con: sqlite3.Connection, table_name: str) -> bool:
    return con.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = ?",
        (table_name,),
    ).fetchone() is not None


def _ensure_sqlite_vec_table(
    con: sqlite3.Connection,
    *,
    table_name: str,
    dim: int,
) -> tuple[bool, str]:
    ok, reason = _load_sqlite_vec(con)
    if not ok:
        return False, reason
    existing_dim = con.execute(
        "SELECT value FROM meta WHERE key = ?",
        (f"sqlite_vec_dim:{table_name}",),
    ).fetchone()
    if existing_dim and str(existing_dim[0]) != str(dim):
        return False, f"sqlite-vec table {table_name} has dim {existing_dim[0]}, expected {dim}"
    if not _sqlite_vec_table_exists(con, table_name):
        if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", table_name):
            return False, f"unsafe sqlite-vec table name: {table_name}"
        con.execute(
            f"CREATE VIRTUAL TABLE {table_name} USING vec0(embedding float[{int(dim)}] distance_metric=cosine)"
        )
        con.execute(
            "INSERT INTO meta(key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (f"sqlite_vec_dim:{table_name}", str(dim)),
        )
    return True, ""


def _sync_sqlite_vec_rows(
    con: sqlite3.Connection,
    *,
    granularity: str,
    backend_id: str,
    model_name: str,
    table_name: str,
    limit: int = 0,
) -> tuple[bool, dict[str, Any]]:
    rows = con.execute(
        """
        SELECT vec_id, embedding, dim
        FROM semantic_embeddings
        WHERE granularity = ? AND backend = ? AND model = ? AND vec_id IS NOT NULL
        ORDER BY updated_at DESC
        """,
        (granularity, backend_id, model_name),
    ).fetchall()
    if not rows:
        return False, {"available": False, "reason": "no persisted embeddings"}
    dim = int(rows[0]["dim"] or 0)
    if dim <= 0:
        return False, {"available": False, "reason": "persisted embeddings have no dimension"}
    ok, reason = _ensure_sqlite_vec_table(con, table_name=table_name, dim=dim)
    if not ok:
        return False, {"available": False, "reason": reason}

    existing = {int(r[0]) for r in con.execute(f"SELECT rowid FROM {table_name}").fetchall()}
    wanted = {int(row["vec_id"]) for row in rows}
    stale = existing - wanted
    for vec_id in stale:
        con.execute(f"DELETE FROM {table_name} WHERE rowid = ?", (vec_id,))

    missing = list(wanted - existing)
    by_id = {int(row["vec_id"]): row for row in rows}
    inserted = 0
    max_insert = max(0, int(limit or 0))
    for vec_id in missing:
        if max_insert and inserted >= max_insert:
            break
        row = by_id[vec_id]
        try:
            con.execute(
                f"INSERT INTO {table_name}(rowid, embedding) VALUES (?, ?)",
                (vec_id, bytes(row["embedding"])),
            )
            inserted += 1
        except sqlite3.IntegrityError:
            con.execute(f"DELETE FROM {table_name} WHERE rowid = ?", (vec_id,))
            con.execute(
                f"INSERT INTO {table_name}(rowid, embedding) VALUES (?, ?)",
                (vec_id, bytes(row["embedding"])),
            )
            inserted += 1

    remaining = max(0, len(missing) - inserted)
    table_count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    return remaining == 0, {
        "available": remaining == 0,
        "table": table_name,
        "dim": dim,
        "persisted_count": len(rows),
        "table_count": int(table_count),
        "inserted": inserted,
        "deleted_stale": len(stale),
        "remaining_sync": remaining,
    }


def _pack_vec_f32(vector: Sequence[float] | bytes | bytearray | memoryview) -> bytes:
    if isinstance(vector, (bytes, bytearray, memoryview)):
        return bytes(vector)
    return _vector_to_blob(vector)


def _sqlite_vec_search(
    con: sqlite3.Connection,
    *,
    table_name: str,
    query_vector: Sequence[float],
    vec_ids: Sequence[int],
    k: int,
) -> list[tuple[int, float]]:
    if not vec_ids:
        return []
    ok, reason = _load_sqlite_vec(con)
    if not ok:
        raise RuntimeError(reason)
    query_blob = _pack_vec_f32(query_vector)
    search_k = max(int(k), min(len(vec_ids), int(k) * 8))
    # Fast path: broad searches use all rows in the sqlite-vec table, so avoid
    # creating/inserting a 19k-row temp candidate table on every query.
    table_count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    if len(vec_ids) >= int(table_count):
        rows = con.execute(
            f"""
            SELECT rowid, distance
            FROM {table_name}
            WHERE embedding MATCH ?
              AND k = ?
            ORDER BY distance
            """,
            (query_blob, search_k),
        ).fetchall()
        return [(int(row[0]), float(row[1])) for row in rows[: int(k)]]

    # Scoped path: sqlite-vec applies JOIN filters after KNN, so constrain the
    # candidate set with an IN subquery. This yields top-k within the requested
    # candidate ids.
    con.execute("DROP TABLE IF EXISTS temp.semantic_vec_candidates")
    con.execute("CREATE TEMP TABLE semantic_vec_candidates(rowid INTEGER PRIMARY KEY)")
    con.executemany(
        "INSERT INTO semantic_vec_candidates(rowid) VALUES (?)",
        [(int(v),) for v in vec_ids],
    )
    rows = con.execute(
        f"""
        SELECT rowid, distance
        FROM {table_name}
        WHERE embedding MATCH ?
          AND k = ?
          AND rowid IN (SELECT rowid FROM semantic_vec_candidates)
        ORDER BY distance
        """,
        (query_blob, search_k),
    ).fetchall()
    return [(int(row[0]), float(row[1])) for row in rows[: int(k)]]


def _sqlite_vec_coverage(
    con: sqlite3.Connection,
    *,
    granularity: str,
    backend_id: str,
    model_name: str,
) -> dict[str, Any]:
    table_name = _vec_table_name(granularity, model_name)
    persisted = con.execute(
        """
        SELECT COUNT(*) FROM semantic_embeddings
        WHERE granularity = ? AND backend = ? AND model = ? AND vec_id IS NOT NULL
        """,
        (granularity, backend_id, model_name),
    ).fetchone()[0]
    table_count = None
    reason = ""
    ok, load_reason = _load_sqlite_vec(con)
    if not ok:
        reason = load_reason
    elif _sqlite_vec_table_exists(con, table_name):
        table_count = con.execute(f"SELECT COUNT(*) FROM {table_name}").fetchone()[0]
    else:
        table_count = 0
    return {
        "table": table_name,
        "available": ok and table_count == persisted and persisted > 0,
        "persisted_count": int(persisted),
        "table_count": int(table_count) if table_count is not None else None,
        "reason": reason,
    }


def _sqlite_vec_ready_cached(
    con: sqlite3.Connection,
    *,
    index_path: Path,
    granularity: str,
    backend_id: str,
    model_name: str,
    table_name: str,
) -> tuple[bool, dict[str, Any]]:
    try:
        db_mtime = Path(index_path).stat().st_mtime
    except OSError:
        db_mtime = 0.0
    cache_key = (str(index_path), granularity, backend_id, model_name, table_name)
    cached = _SQLITE_VEC_READY_CACHE.get(cache_key)
    if cached and cached[0] == db_mtime:
        _SQLITE_VEC_READY_CACHE.move_to_end(cache_key)
        return True, dict(cached[1])
    synced, meta = _sync_sqlite_vec_rows(
        con,
        granularity=granularity,
        backend_id=backend_id,
        model_name=model_name,
        table_name=table_name,
    )
    if synced:
        _SQLITE_VEC_READY_CACHE[cache_key] = (db_mtime, dict(meta))
        while len(_SQLITE_VEC_READY_CACHE) > _SQLITE_VEC_READY_CACHE_MAX:
            _SQLITE_VEC_READY_CACHE.popitem(last=False)
    return synced, meta


def _vector_to_blob(vector: Sequence[float]) -> bytes:
    arr = array.array("f", (float(v) for v in vector))
    if arr.itemsize != 4:  # pragma: no cover - CPython float array is 32-bit
        raise RuntimeError("array('f') is not 32-bit on this platform")
    return arr.tobytes()


def _vector_from_storage(value: Any) -> list[float]:
    if isinstance(value, (bytes, bytearray, memoryview)):
        data = bytes(value)
        if len(data) % 4 == 0 and data:
            arr = array.array("f")
            arr.frombytes(data)
            return [float(v) for v in arr]
        return []
    text = str(value or "")
    if not text:
        return []
    return [float(v) for v in json.loads(text)]

def _load_persistent_embedding_refs(
    con: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    granularity: str,
    backend_id: str,
    model_name: str,
) -> tuple[list[dict[str, Any] | None], list[int]]:
    """Load lightweight embedding references without materializing vector BLOBs."""
    refs: list[dict[str, Any] | None] = [None] * len(rows)
    expected: dict[str, tuple[int, str]] = {
        _row_map_key(row, granularity): (idx, _semantic_content_hash(row, granularity, backend_id))
        for idx, row in enumerate(rows)
    }
    if not expected:
        return refs, []

    row_ids = list(expected.keys())
    stale_indices: set[int] = set()
    for start in range(0, len(row_ids), 500):
        chunk = row_ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        params: list[Any] = [granularity, backend_id, model_name, *chunk]
        hits = con.execute(
            f"""
            SELECT row_id, content_hash, vec_id, dim
            FROM semantic_embeddings
            WHERE granularity = ? AND backend = ? AND model = ?
              AND row_id IN ({placeholders})
            """,
            params,
        ).fetchall()
        for hit in hits:
            row_id = str(hit["row_id"])
            expected_entry = expected.get(row_id)
            if not expected_entry:
                continue
            idx, content_hash = expected_entry
            if hit["content_hash"] != content_hash:
                stale_indices.add(idx)
                continue
            vec_id = hit["vec_id"]
            if vec_id is None:
                vec_id = _semantic_vec_id(row_id, granularity, backend_id, model_name)
                con.execute(
                    """
                    UPDATE semantic_embeddings SET vec_id = ?
                    WHERE row_id = ? AND granularity = ? AND backend = ? AND model = ?
                    """,
                    (vec_id, row_id, granularity, backend_id, model_name),
                )
            refs[idx] = {"row_id": row_id, "vec_id": int(vec_id), "dim": int(hit["dim"] or 0)}

    missing = [idx for idx, ref in enumerate(refs) if ref is None or idx in stale_indices]
    return refs, missing


def _semantic_rows_signature(rows: Sequence[sqlite3.Row], *, granularity: str, backend_id: str) -> str:
    return _semantic_fingerprint(rows, granularity=granularity, backend=backend_id)


def _cached_vec_ids(
    refs: Sequence[dict[str, Any] | None],
    *,
    signature: str,
    index_path: Path,
    granularity: str,
    backend_id: str,
    model_name: str,
    source: str,
    path_filter: str,
    category: str,
) -> list[int]:
    cache_key = (str(index_path), granularity, backend_id, model_name, source or "all", path_filter or "", category or "")
    cached = _SQLITE_VEC_ID_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        _SQLITE_VEC_ID_CACHE.move_to_end(cache_key)
        return list(cached[1])
    vec_ids = [int(ref["vec_id"]) for ref in refs if ref is not None]
    _SQLITE_VEC_ID_CACHE[cache_key] = (signature, list(vec_ids))
    while len(_SQLITE_VEC_ID_CACHE) > _SQLITE_VEC_ID_CACHE_MAX:
        _SQLITE_VEC_ID_CACHE.popitem(last=False)
    return vec_ids


def _load_cached_persistent_embedding_refs(
    con: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    index_path: Path,
    granularity: str,
    backend_id: str,
    model_name: str,
    source: str,
    path_filter: str,
    category: str,
) -> tuple[list[dict[str, Any] | None], list[int]]:
    signature = _semantic_rows_signature(rows, granularity=granularity, backend_id=backend_id)
    cache_key = (str(index_path), granularity, backend_id, model_name, source or "all", path_filter or "", category or "")
    cached = _GEMINI_ROW_REF_CACHE.get(cache_key)
    if cached and cached[0] == signature:
        _GEMINI_ROW_REF_CACHE.move_to_end(cache_key)
        refs = [dict(ref) if ref is not None else None for ref in cached[1]]
        return refs, list(cached[2])

    refs, missing = _load_persistent_embedding_refs(
        con, rows, granularity=granularity, backend_id=backend_id, model_name=model_name
    )
    _GEMINI_ROW_REF_CACHE[cache_key] = (
        signature,
        [dict(ref) if ref is not None else None for ref in refs],
        list(missing),
    )
    while len(_GEMINI_ROW_REF_CACHE) > _GEMINI_ROW_REF_CACHE_MAX:
        _GEMINI_ROW_REF_CACHE.popitem(last=False)
    return refs, missing


def _load_persistent_embeddings(
    con: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    granularity: str,
    backend_id: str,
    model_name: str,
) -> tuple[list[Any | None], list[int]]:
    vectors: list[Any | None] = [None] * len(rows)
    expected: dict[str, tuple[int, str]] = {
        _row_map_key(row, granularity): (idx, _semantic_content_hash(row, granularity, backend_id))
        for idx, row in enumerate(rows)
    }
    if not expected:
        return vectors, []

    row_ids = list(expected.keys())
    stale_indices: set[int] = set()
    for start in range(0, len(row_ids), 500):
        chunk = row_ids[start:start + 500]
        placeholders = ",".join("?" for _ in chunk)
        params: list[Any] = [granularity, backend_id, model_name, *chunk]
        hits = con.execute(
            f"""
            SELECT row_id, content_hash, embedding, vec_id FROM semantic_embeddings
            WHERE granularity = ? AND backend = ? AND model = ?
              AND row_id IN ({placeholders})
            """,
            params,
        ).fetchall()
        for hit in hits:
            row_id = str(hit["row_id"])
            expected_entry = expected.get(row_id)
            if not expected_entry:
                continue
            idx, content_hash = expected_entry
            if hit["content_hash"] != content_hash:
                stale_indices.add(idx)
                continue
            raw = hit["embedding"]
            if hit["vec_id"] is None:
                con.execute(
                    """
                    UPDATE semantic_embeddings SET vec_id = ?
                    WHERE row_id = ? AND granularity = ? AND backend = ? AND model = ?
                    """,
                    (_semantic_vec_id(row_id, granularity, backend_id, model_name), row_id, granularity, backend_id, model_name),
                )
            if isinstance(raw, (bytes, bytearray, memoryview)) and len(bytes(raw)) % 4 == 0 and raw:
                vectors[idx] = bytes(raw)
            else:
                try:
                    vector = _vector_from_storage(raw)
                except Exception:
                    stale_indices.add(idx)
                    continue
                if vector:
                    vectors[idx] = vector
                else:
                    stale_indices.add(idx)

    missing = [idx for idx, vector in enumerate(vectors) if vector is None or idx in stale_indices]
    return vectors, missing


def _store_persistent_embeddings(
    con: sqlite3.Connection,
    rows: Sequence[sqlite3.Row],
    *,
    vectors: Sequence[Sequence[float]],
    granularity: str,
    backend_id: str,
    model_name: str,
) -> None:
    now = time.time()
    for row, vector in zip(rows, vectors):
        values = [float(v) for v in vector]
        row_id = _row_map_key(row, granularity)
        vec_id = _semantic_vec_id(row_id, granularity, backend_id, model_name)
        con.execute(
            """
            INSERT INTO semantic_embeddings(
                row_id, granularity, backend, model, content_hash, path, source, embedding, dim, vec_id, updated_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(row_id, granularity, backend, model) DO UPDATE SET
                content_hash=excluded.content_hash,
                path=excluded.path,
                source=excluded.source,
                embedding=excluded.embedding,
                dim=excluded.dim,
                vec_id=excluded.vec_id,
                updated_at=excluded.updated_at
            """,
            (
                row_id,
                granularity,
                backend_id,
                model_name,
                _semantic_content_hash(row, granularity, backend_id),
                str(row["path"]),
                str(row["source"]),
                _vector_to_blob(values),
                len(values),
                vec_id,
                now,
            ),
        )



def _normalize_persistent_vector_matrix(vectors: Sequence[Any]):
    try:
        import numpy as np
    except Exception as exc:  # pragma: no cover - numpy is expected in Hermes env
        raise RuntimeError("Gemini semantic search requires numpy for local vector scoring") from exc
    if not vectors:
        raise RuntimeError("Gemini persistent embedding cache returned no vectors")
    arrays = []
    for vector in vectors:
        if isinstance(vector, (bytes, bytearray, memoryview)):
            arrays.append(np.frombuffer(bytes(vector), dtype=np.float32))
        else:
            arrays.append(np.asarray(vector, dtype=np.float32))
    matrix = np.vstack(arrays).astype("float32", copy=False)
    if matrix.ndim != 2 or matrix.shape[0] == 0 or matrix.shape[1] == 0:
        raise RuntimeError("Gemini persistent embedding cache returned empty vectors")
    norms = np.linalg.norm(matrix, axis=1, keepdims=True)
    norms[norms == 0] = 1.0
    return matrix / norms

def _build_gemini_semantic_model(rows: Sequence[sqlite3.Row], granularity: str, *, model: str) -> dict[str, Any]:
    docs = _semantic_documents(rows, granularity)
    titles = [str(row["path"]) for row in rows]
    prepared = [
        _prepare_gemini_embedding_text(text, is_query=False, title=title)
        for text, title in zip(docs, titles)
    ]
    vectors = _embed_gemini_texts_batched(prepared, model=model)
    matrix = _normalize_dense_matrix(vectors)
    return {"backend": f"gemini:{model}", "model": model, "matrix": matrix}


def _query_gemini_embedding(query: str, *, model_name: str) -> list[float]:
    cache_key = (model_name, query)
    cached = _GEMINI_QUERY_EMBEDDING_CACHE.get(cache_key)
    if cached is not None:
        _GEMINI_QUERY_EMBEDDING_CACHE.move_to_end(cache_key)
        return list(cached)
    qtext = _prepare_gemini_embedding_text(query, is_query=True)
    qvecs = _embed_gemini_texts([qtext], model=model_name)
    if not qvecs or not qvecs[0]:
        raise RuntimeError("Gemini embedding backend returned an empty query vector")
    vector = [float(v) for v in qvecs[0]]
    _GEMINI_QUERY_EMBEDDING_CACHE[cache_key] = vector
    while len(_GEMINI_QUERY_EMBEDDING_CACHE) > _GEMINI_QUERY_CACHE_MAX:
        _GEMINI_QUERY_EMBEDDING_CACHE.popitem(last=False)
    return list(vector)


def _query_semantic_model(model: dict[str, Any], query: str) -> list[float]:
    backend = str(model.get("backend") or "")
    if backend.startswith("gemini:"):
        if model.get("sqlite_vec_table"):
            raise RuntimeError("sqlite-vec Gemini model must be queried through _sqlite_vec_search")
        qvec = _query_gemini_embedding(query, model_name=str(model.get("model") or "gemini-embedding-2"))
        qmatrix = _normalize_dense_matrix([qvec])
        scores = model["matrix"].dot(qmatrix[0])
        return [float(v) for v in scores.ravel()]

    from sklearn.preprocessing import normalize

    q = model["vectorizer"].transform([query])
    if model.get("svd") is not None:
        qv = normalize(model["svd"].transform(q))
    else:
        qv = normalize(q)
    scores = model["matrix"].dot(qv.T)
    try:
        return [float(v) for v in scores.toarray().ravel()]
    except AttributeError:
        return [float(v) for v in scores.ravel()]


def _load_or_build_semantic_cache(
    *,
    index_path: Path,
    rows: Sequence[sqlite3.Row],
    granularity: str,
    backend: str,
    model_name: str,
    allow_build: bool = True,
) -> tuple[dict[str, Any], bool, str]:
    backend_id = f"gemini:{model_name}" if backend == "gemini" else "sklearn_lsa_v1"
    if backend == "gemini":
        with _connect(index_path) as con:
            _ensure_schema(con)
            existing, missing = _load_persistent_embeddings(
                con, rows, granularity=granularity, backend_id=backend_id, model_name=model_name
            )
            if missing and not allow_build:
                available_indices = [i for i, vector in enumerate(existing) if vector is not None]
                coverage = 1.0 - (len(missing) / max(1, len(rows)))
                min_partial = float(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_MIN_PARTIAL_COVERAGE", "0.95"))
                if not available_indices or coverage < min_partial:
                    raise RuntimeError(
                        f"{backend} semantic cache is only {coverage:.1%} complete ({len(available_indices)}/{len(rows)}); refusing partial broad scoring"
                    )
                table_name = _vec_table_name(granularity, model_name)
                synced, vec_meta = _sync_sqlite_vec_rows(
                    con, granularity=granularity, backend_id=backend_id, model_name=model_name, table_name=table_name
                )
                con.commit()
                if synced:
                    return {
                        "fingerprint": _semantic_fingerprint(rows, granularity=granularity, backend=backend_id),
                        "granularity": granularity,
                        "backend": backend_id,
                        "built_at": time.time(),
                        "model": {"backend": backend_id, "model": model_name, "sqlite_vec_table": table_name},
                        "row_indices": available_indices,
                        "missing_count": len(missing),
                        "vector_index": "sqlite-vec",
                        "sqlite_vec": vec_meta,
                    }, False, f"{index_path}#{table_name}"
                if vec_meta.get("remaining_sync"):
                    raise RuntimeError(f"sqlite-vec index is incomplete: {vec_meta}")
                vectors = [existing[i] for i in available_indices if existing[i] is not None]
                matrix = _normalize_persistent_vector_matrix(vectors)
                model = {"backend": backend_id, "model": model_name, "matrix": matrix}
                return {
                    "fingerprint": _semantic_fingerprint(rows, granularity=granularity, backend=backend_id),
                    "granularity": granularity,
                    "backend": backend_id,
                    "built_at": time.time(),
                    "model": model,
                    "row_indices": available_indices,
                    "missing_count": len(missing),
                    "vector_index": "python",
                }, False, f"{index_path}#semantic_embeddings"
            rebuilt = bool(missing)
            if missing:
                batch_size = max(1, min(int(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_BATCH_SIZE", str(_GEMINI_BATCH_SIZE))), 100))
                for start in range(0, len(missing), batch_size):
                    batch_indices = missing[start:start + batch_size]
                    docs = _semantic_documents([rows[i] for i in batch_indices], granularity)
                    prepared = [
                        _prepare_gemini_embedding_text(text, is_query=False, title=str(rows[i]["path"]))
                        for text, i in zip(docs, batch_indices)
                    ]
                    new_vectors = _embed_gemini_texts_batched(prepared, model=model_name)
                    _store_persistent_embeddings(
                        con,
                        [rows[i] for i in batch_indices],
                        vectors=new_vectors,
                        granularity=granularity,
                        backend_id=backend_id,
                        model_name=model_name,
                    )
                    con.commit()
                    for i, vector in zip(batch_indices, new_vectors):
                        existing[i] = _vector_to_blob(vector)
        vectors = [vector for vector in existing if vector is not None]
        if len(vectors) != len(rows):
            raise RuntimeError("Gemini persistent embedding cache returned incomplete vectors")
        table_name = _vec_table_name(granularity, model_name)
        synced, vec_meta = _sqlite_vec_ready_cached(
            con,
            index_path=index_path,
            granularity=granularity,
            backend_id=backend_id,
            model_name=model_name,
            table_name=table_name,
        )
        con.commit()
        if synced:
            model = {"backend": backend_id, "model": model_name, "sqlite_vec_table": table_name}
            return {
                "fingerprint": _semantic_fingerprint(rows, granularity=granularity, backend=backend_id),
                "granularity": granularity,
                "backend": backend_id,
                "built_at": time.time(),
                "model": model,
                "row_indices": list(range(len(rows))),
                "missing_count": 0,
                "vector_index": "sqlite-vec",
                "sqlite_vec": vec_meta,
            }, rebuilt, f"{index_path}#{table_name}"
        matrix = _normalize_persistent_vector_matrix(vectors)
        model = {"backend": backend_id, "model": model_name, "matrix": matrix}
        return {
            "fingerprint": _semantic_fingerprint(rows, granularity=granularity, backend=backend_id),
            "granularity": granularity,
            "backend": backend_id,
            "built_at": time.time(),
            "model": model,
            "row_indices": list(range(len(rows))),
            "missing_count": 0,
            "vector_index": "python",
            "sqlite_vec": vec_meta,
        }, rebuilt, f"{index_path}#semantic_embeddings"

    cache_path = _semantic_index_path(index_path, granularity, backend_id)
    fingerprint = _semantic_fingerprint(rows, granularity=granularity, backend=backend_id)
    try:
        with cache_path.open("rb") as fh:
            cached = pickle.load(fh)
        if (
            cached.get("fingerprint") == fingerprint
            and cached.get("granularity") == granularity
            and cached.get("backend") == backend_id
        ):
            return cached, False, str(cache_path)
    except Exception:
        pass

    if not allow_build:
        raise RuntimeError(
            f"{backend} semantic cache is cold for {len(rows)} candidates; refusing live rebuild"
        )
    texts = _semantic_documents(rows, granularity)
    model = _build_semantic_model(texts)
    backend_id = str(model["backend"])
    fingerprint = _semantic_fingerprint(rows, granularity=granularity, backend=backend_id)
    cache_path = _semantic_index_path(index_path, granularity, backend_id)
    payload = {
        "fingerprint": fingerprint,
        "granularity": granularity,
        "backend": model["backend"],
        "built_at": time.time(),
        "model": model,
    }
    try:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        with cache_path.open("wb") as fh:
            pickle.dump(payload, fh, protocol=pickle.HIGHEST_PROTOCOL)
    except Exception:
        # Search can still proceed if the sidecar cache cannot be written.
        pass
    return payload, True, str(cache_path)


def _search_semantic_rows(
    query: str,
    *,
    index_path: Path,
    limit: int,
    source: str,
    path_filter: str,
    granularity: str,
    category: str,
    backend: str,
    model_name: str,
    semantic_rebuild: str = "auto",
    live_rebuild: bool = True,
) -> tuple[list[tuple[sqlite3.Row, float]], dict[str, Any]]:
    with _connect(index_path) as con:
        _ensure_schema(con)
        rows = _semantic_candidate_rows(
            con,
            granularity=granularity,
            source=source,
            path_filter=path_filter,
            category=category,
        )
    if not rows:
        return [], {"backend": None, "rebuilt": False, "candidate_count": 0}
    max_cold_rows = int(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_MAX_COLD_ROWS", str(_GEMINI_DEFAULT_MAX_COLD_ROWS)))
    if backend == "gemini":
        if semantic_rebuild == "force":
            allow_backend_build = True
        elif semantic_rebuild == "never":
            allow_backend_build = False
        else:
            allow_backend_build = live_rebuild or len(rows) <= max_cold_rows
    else:
        sklearn_max_cold_rows = int(os.getenv("HERMES_MEMORY_SEARCH_SKLEARN_MAX_COLD_ROWS", str(_SKLEARN_DEFAULT_MAX_COLD_ROWS)))
        allow_backend_build = live_rebuild or len(rows) <= sklearn_max_cold_rows
    backend_id = f"gemini:{model_name}" if backend == "gemini" else "sklearn_lsa_v1"
    if backend == "gemini" and not allow_backend_build:
        with _connect(index_path) as con:
            _ensure_schema(con)
            refs, missing = _load_cached_persistent_embedding_refs(
                con,
                rows,
                index_path=index_path,
                granularity=granularity,
                backend_id=backend_id,
                model_name=model_name,
                source=source,
                path_filter=path_filter,
                category=category,
            )
            available_indices = [i for i, ref in enumerate(refs) if ref is not None]
            coverage = 1.0 - (len(missing) / max(1, len(rows)))
            min_partial = float(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_MIN_PARTIAL_COVERAGE", "0.95"))
            if not available_indices or coverage < min_partial:
                raise RuntimeError(
                    f"{backend} semantic cache is only {coverage:.1%} complete ({len(available_indices)}/{len(rows)}); refusing partial broad scoring"
                )
            table_name = _vec_table_name(granularity, model_name)
            synced, vec_meta = _sqlite_vec_ready_cached(
                con,
                index_path=index_path,
                granularity=granularity,
                backend_id=backend_id,
                model_name=model_name,
                table_name=table_name,
            )
            if synced:
                qvec = _query_gemini_embedding(query, model_name=model_name)
                signature = _semantic_rows_signature(rows, granularity=granularity, backend_id=backend_id)
                vec_ids = _cached_vec_ids(
                    refs,
                    signature=signature,
                    index_path=index_path,
                    granularity=granularity,
                    backend_id=backend_id,
                    model_name=model_name,
                    source=source,
                    path_filter=path_filter,
                    category=category,
                )
                vec_id_to_index = {int(ref["vec_id"]): idx for idx, ref in enumerate(refs) if ref is not None}
                vec_matches = _sqlite_vec_search(
                    con,
                    table_name=table_name,
                    query_vector=qvec,
                    vec_ids=vec_ids,
                    k=limit,
                )
                ranked = []
                for vec_id, distance in vec_matches:
                    idx = vec_id_to_index.get(int(vec_id))
                    if idx is None:
                        continue
                    # Convert cosine distance back to similarity so hybrid ranking
                    # still treats larger semantic scores as better.
                    ranked.append((rows[idx], 1.0 - float(distance)))
                return ranked, {
                    "backend": backend_id,
                    "rebuilt": False,
                    "cache_path": f"{index_path}#{table_name}",
                    "candidate_count": len(rows),
                    "embedded_count": len(available_indices),
                    "missing_count": len(missing),
                    "vector_index": "sqlite-vec",
                    "sqlite_vec": vec_meta,
                }
            if vec_meta.get("remaining_sync"):
                raise RuntimeError(f"sqlite-vec index is incomplete: {vec_meta}")
            if len(rows) > int(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_PYTHON_FALLBACK_MAX_ROWS", "5000")):
                raise RuntimeError(f"sqlite-vec unavailable for {len(rows)} candidates: {vec_meta.get('reason') or vec_meta}")

    cache, rebuilt, cache_path = _load_or_build_semantic_cache(
        index_path=index_path,
        rows=rows,
        granularity=granularity,
        backend=backend,
        model_name=model_name,
        allow_build=allow_backend_build,
    )
    row_indices = cache.get("row_indices")
    if backend == "gemini" and cache.get("vector_index") == "sqlite-vec":
        with _connect(index_path) as con:
            _ensure_schema(con)
            refs, missing = _load_cached_persistent_embedding_refs(
                con,
                rows,
                index_path=index_path,
                granularity=granularity,
                backend_id=backend_id,
                model_name=model_name,
                source=source,
                path_filter=path_filter,
                category=category,
            )
            candidate_indices = [int(i) for i in (row_indices or range(len(rows))) if refs[int(i)] is not None]
            vec_id_to_index = {int(refs[i]["vec_id"]): i for i in candidate_indices if refs[i] is not None}
            if row_indices is None or len(candidate_indices) == len([ref for ref in refs if ref is not None]):
                signature = _semantic_rows_signature(rows, granularity=granularity, backend_id=backend_id)
                vec_ids = _cached_vec_ids(
                    refs,
                    signature=signature,
                    index_path=index_path,
                    granularity=granularity,
                    backend_id=backend_id,
                    model_name=model_name,
                    source=source,
                    path_filter=path_filter,
                    category=category,
                )
            else:
                vec_ids = list(vec_id_to_index.keys())
            qvec = _query_gemini_embedding(query, model_name=model_name)
            vec_matches = _sqlite_vec_search(
                con,
                table_name=str(cache["model"].get("sqlite_vec_table") or _vec_table_name(granularity, model_name)),
                query_vector=qvec,
                vec_ids=vec_ids,
                k=limit,
            )
        ranked = []
        for vec_id, distance in vec_matches:
            idx = vec_id_to_index.get(int(vec_id))
            if idx is None:
                continue
            ranked.append((rows[idx], 1.0 - float(distance)))
        return ranked, {
            "backend": cache.get("backend"),
            "rebuilt": rebuilt,
            "cache_path": cache_path,
            "candidate_count": len(rows),
            "embedded_count": len(vec_id_to_index),
            "missing_count": int(cache.get("missing_count") or len(missing) or 0),
            "vector_index": "sqlite-vec",
            "sqlite_vec": cache.get("sqlite_vec"),
        }

    scores = _query_semantic_model(cache["model"], query)
    score_rows = rows
    if row_indices is not None:
        score_rows = [rows[int(i)] for i in row_indices]
    ranked = sorted(zip(score_rows, scores), key=lambda item: item[1], reverse=True)
    ranked = [(row, score) for row, score in ranked if score > 0][:limit]
    return ranked, {
        "backend": cache.get("backend"),
        "rebuilt": rebuilt,
        "cache_path": cache_path,
        "candidate_count": len(rows),
        "embedded_count": len(score_rows),
        "missing_count": int(cache.get("missing_count") or 0),
        "vector_index": cache.get("vector_index") or "python",
        "sqlite_vec": cache.get("sqlite_vec"),
    }


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


def _chunk_result_from_row(row: sqlite3.Row, query: str, *, score: float | None = None) -> dict[str, Any]:
    return {
        "source": row["source"],
        "path": row["path"],
        "start_line": int(row["start_line"]),
        "end_line": int(row["end_line"]),
        "score": float(row["score"] if score is None and "score" in row.keys() else (score or 0.0)),
        "snippet": _snippet(row["text"], query),
    }


def _observation_result_from_row(row: sqlite3.Row, *, score: float | None = None) -> dict[str, Any]:
    return {
        "source": row["source"],
        "path": row["path"],
        "line": int(row["line"]),
        "category": row["category"],
        "tags": [t for t in str(row["tags"]).split() if t],
        "text": row["text"],
        "score": float(row["score"] if score is None and "score" in row.keys() else (score or 0.0)),
    }


def _result_key(hit: dict[str, Any], granularity: str) -> tuple[Any, ...]:
    if granularity == "observation":
        return (hit["path"], hit["line"], hit.get("category"), hit.get("text"))
    return (hit["path"], hit["start_line"], hit["end_line"])


def _merge_hybrid_results(
    *,
    semantic_results: list[dict[str, Any]],
    keyword_results: list[dict[str, Any]],
    granularity: str,
    limit: int,
) -> list[dict[str, Any]]:
    """Merge semantic + keyword rankings with reciprocal-rank fusion."""
    merged: dict[tuple[Any, ...], dict[str, Any]] = {}
    scores: dict[tuple[Any, ...], float] = {}
    rank_k = 60.0
    for rank, hit in enumerate(semantic_results, start=1):
        key = _result_key(hit, granularity)
        merged.setdefault(key, dict(hit))
        scores[key] = scores.get(key, 0.0) + 1.0 / (rank_k + rank)
    for rank, hit in enumerate(keyword_results, start=1):
        key = _result_key(hit, granularity)
        merged.setdefault(key, dict(hit))
        scores[key] = scores.get(key, 0.0) + 1.0 / (rank_k + rank)
    ranked = sorted(scores.items(), key=lambda item: item[1], reverse=True)[:limit]
    results: list[dict[str, Any]] = []
    for key, score in ranked:
        hit = merged[key]
        hit["score"] = float(score)
        results.append(hit)
    return results


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
    granularity: str = "chunk",
    category: str = "",
    mode: str = "hybrid",
    semantic_backend: str = "gemini",
    semantic_model: str = "gemini-embedding-2",
    semantic_rebuild: str = "auto",
) -> dict[str, Any]:
    query = (query or "").strip()
    granularity = (granularity or "chunk").strip().lower()
    category = (category or "").strip().lower()
    mode = (mode or "hybrid").strip().lower()
    semantic_backend = (semantic_backend or "gemini").strip().lower()
    semantic_model = (semantic_model or "gemini-embedding-2").strip()
    semantic_rebuild = (semantic_rebuild or "auto").strip().lower()
    if mode not in {"keyword", "semantic", "hybrid"}:
        return {"success": False, "error": "mode must be one of: keyword, semantic, hybrid"}
    if semantic_rebuild not in {"auto", "never", "force"}:
        return {"success": False, "error": "semantic_rebuild must be one of: auto, never, force"}
    if mode == "hybrid" and semantic_backend == "gemini" and semantic_rebuild == "auto" and not path_filter and (not source or source == "all"):
        # Normal broad memory lookups should be OpenClaw-like: embed the query
        # once and score against vectors already persisted in SQLite. A full
        # cold rebuild belongs in an explicit preindex/force path, not a
        # foreground Discord turn.
        semantic_rebuild = "never"
    if semantic_backend not in {"sklearn", "gemini"}:
        return {"success": False, "error": "semantic_backend must be one of: sklearn, gemini"}
    if granularity not in {"chunk", "observation"}:
        return {"success": False, "error": "granularity must be one of: chunk, observation"}
    # A category filter implies observation granularity; let it be a shorthand.
    if category and granularity == "chunk":
        granularity = "observation"
    # Chunk search still requires a query. Observation search may run on a bare
    # category/source/path filter (e.g. "list every [decision]"), so only demand
    # a query when none of those narrowing filters are present.
    if not query and not (granularity == "observation" and (category or path_filter or (source and source != "all"))):
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

    if mode in {"semantic", "hybrid"} and query:
        try:
            semantic_rows, semantic_meta = _search_semantic_rows(
                query,
                index_path=index_path,
                limit=limit,
                source=source,
                path_filter=path_filter,
                granularity=granularity,
                category=category,
                backend=semantic_backend,
                model_name=semantic_model,
                semantic_rebuild=semantic_rebuild,
                live_rebuild=(mode == "semantic"),
            )
        except Exception as exc:  # noqa: BLE001 - semantic backend is best-effort in hybrid mode
            if mode == "semantic":
                return {"success": False, "error": f"semantic search failed: {exc}"}
            if semantic_backend == "gemini":
                try:
                    # Large unfiltered searches should remain responsive even when
                    # the Gemini cache is cold. Local LSA over the full corpus can
                    # also be a multi-second rebuild, so only use it as a live
                    # fallback for bounded candidate sets or an existing cache.
                    semantic_rows, semantic_meta = _search_semantic_rows(
                        query,
                        index_path=index_path,
                        limit=limit,
                        source=source,
                        path_filter=path_filter,
                        granularity=granularity,
                        category=category,
                        backend="sklearn",
                        model_name=semantic_model,
                        semantic_rebuild="never",
                        live_rebuild=False,
                    )
                    semantic_meta["requested_backend"] = semantic_backend
                    semantic_meta["requested_model"] = semantic_model
                    semantic_meta["fallback"] = "sklearn"
                    semantic_meta["error"] = str(exc)
                except Exception as fallback_exc:  # noqa: BLE001 - keep hybrid search usable
                    semantic_rows = []
                    semantic_meta = {
                        "backend": None,
                        "requested_backend": semantic_backend,
                        "model": semantic_model,
                        "error": str(exc),
                        "fallback_error": str(fallback_exc),
                        "fallback": "keyword",
                    }
            else:
                semantic_rows = []
                semantic_meta = {
                    "backend": None,
                    "requested_backend": semantic_backend,
                    "model": semantic_model,
                    "error": str(exc),
                    "fallback": "keyword",
                }
        if granularity == "observation":
            semantic_results = [
                _observation_result_from_row(row, score=score)
                for row, score in semantic_rows
            ]
        else:
            semantic_results = [
                _chunk_result_from_row(row, query, score=score)
                for row, score in semantic_rows
            ]
        if mode == "semantic":
            return _format_search_payload(
                query=query,
                mode=mode,
                granularity=granularity,
                results=semantic_results,
                render_format=render_format,
                index_path=index_path,
                indexed=indexed,
                category=category,
                query_strategy="semantic",
                semantic=semantic_meta,
            )
    else:
        semantic_results = []
        semantic_meta = None

    if granularity == "observation":
        obs_payload = _search_observations(
            query,
            index_path=index_path,
            limit=limit,
            source=source,
            path_filter=path_filter,
            category=category,
            render_format="json" if mode == "hybrid" else render_format,
            indexed=indexed,
        )
        if mode == "hybrid" and obs_payload.get("success"):
            keyword_results = obs_payload.get("results", [])
            results = _merge_hybrid_results(
                semantic_results=semantic_results,
                keyword_results=keyword_results,
                granularity="observation",
                limit=limit,
            )
            return _format_search_payload(
                query=query,
                mode="hybrid",
                granularity="observation",
                results=results,
                render_format=render_format,
                index_path=index_path,
                indexed=indexed,
                category=category,
                query_strategy="semantic+keyword_rrf",
                semantic=semantic_meta,
            )
        return obs_payload

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

    results = [_chunk_result_from_row(row, query) for row in rows]
    if mode == "hybrid":
        results = _merge_hybrid_results(
            semantic_results=semantic_results,
            keyword_results=results,
            granularity="chunk",
            limit=limit,
        )
        query_strategy = "semantic+keyword_rrf"
    return _format_search_payload(
        query=query,
        mode=mode,
        granularity="chunk",
        results=results,
        render_format=render_format,
        index_path=index_path,
        indexed=indexed,
        category="",
        query_strategy=query_strategy,
        semantic=semantic_meta if mode == "hybrid" else None,
    )


def _search_observations(
    query: str,
    *,
    index_path: Path,
    limit: int,
    source: str,
    path_filter: str,
    category: str,
    render_format: str,
    indexed: Any,
) -> dict[str, Any]:
    """Observation-granularity search: returns individual facts with exact lines."""
    query_strategy = "strict_and" if query else "metadata_only"
    with _connect(index_path) as con:
        _ensure_schema(con)
        try:
            rows = _query_observations(
                con,
                fts=_fts_query(query, operator="AND") if query else "",
                limit=limit,
                source=source,
                path_filter=path_filter,
                category=category,
            )
            if query and not rows and len(re.findall(r"\w+", query)) > 1:
                rows = _query_observations(
                    con,
                    fts=_fts_query(query, operator="OR"),
                    limit=limit,
                    source=source,
                    path_filter=path_filter,
                    category=category,
                )
                if rows:
                    query_strategy = "relaxed_or"
        except sqlite3.OperationalError:
            query_strategy = "like_fallback"
            like_params: list[Any] = []
            like_filters: list[str] = []
            if query:
                like_filters.append("text LIKE ?")
                like_params.append(f"%{query}%")
            if source and source != "all":
                like_filters.append("source = ?")
                like_params.append(source)
            if path_filter:
                like_filters.append("path LIKE ?")
                like_params.append(f"%{path_filter}%")
            if category:
                like_filters.append("category = ?")
                like_params.append(category)
            where = (" WHERE " + " AND ".join(like_filters)) if like_filters else ""
            like_params.append(limit)
            rows = con.execute(
                f"""
                SELECT path, source, line, category, tags, text, 0.0 AS score
                FROM observations
                {where}
                ORDER BY updated_at DESC
                LIMIT ?
                """,
                like_params,
            ).fetchall()

    results = [_observation_result_from_row(row) for row in rows]
    return _format_search_payload(
        query=query,
        mode="keyword",
        granularity="observation",
        results=results,
        render_format=render_format,
        index_path=index_path,
        indexed=indexed,
        category=category,
        query_strategy=query_strategy,
    )


def _format_search_payload(
    *,
    query: str,
    mode: str,
    granularity: str,
    results: list[dict[str, Any]],
    render_format: str,
    index_path: Path,
    indexed: Any,
    category: str = "",
    query_strategy: str,
    semantic: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "success": True,
        "query": query,
        "mode": mode,
        "granularity": granularity,
        "query_strategy": query_strategy,
        "index_path": str(index_path),
        "index_updated": indexed,
        "count": len(results),
        "cache_miss_writeback": (
            "If this search recovers durable context that should have been in memory, "
            "write a tight summary to the relevant ChatWorkspace context file or Hermes memory pointer."
        ),
    }
    if category:
        payload["category"] = category
    elif granularity == "observation":
        payload["category"] = None
    if semantic is not None:
        payload["semantic"] = semantic

    if render_format == "json":
        payload["results"] = results
    elif render_format == "toon":
        if granularity == "observation":
            toon_rows = [
                {
                    "source": hit["source"],
                    "path": _compact_context_path(str(hit["path"]), str(hit["source"])),
                    "line": hit["line"],
                    "category": hit["category"],
                    "tags": ",".join(hit["tags"]),
                    "text": hit["text"],
                }
                for hit in results
            ]
            payload["render_format"] = "toon"
            payload["toon_context"] = render_toon_rows(
                "facts",
                toon_rows,
                ["source", "path", "line", "category", "tags", "text"],
            )
        else:
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




def preindex_semantic_embeddings(
    *,
    index_path: Path | str = DEFAULT_INDEX_PATH,
    roots: Optional[Sequence[tuple[Path | str, str]]] = None,
    source: str = "all",
    path_filter: str = "",
    granularity: str = "chunk",
    category: str = "",
    semantic_model: str = "gemini-embedding-2",
    max_batches: int = 0,
    freshness_seconds: int = 60,
    retry_429: bool = True,
) -> dict[str, Any]:
    index_path = Path(index_path).expanduser()
    roots_norm = _normalize_roots(roots)
    indexed = None
    if _index_is_stale(index_path, roots_norm, freshness_seconds):
        indexed = build_index(index_path=index_path, roots=roots_norm)
    granularity = (granularity or "chunk").strip().lower()
    category = (category or "").strip().lower()
    if category and granularity == "chunk":
        granularity = "observation"
    if granularity not in {"chunk", "observation"}:
        return {"success": False, "error": "granularity must be one of: chunk, observation"}

    backend_id = f"gemini:{semantic_model}"
    with _connect(index_path) as con:
        _ensure_schema(con)
        rows = _semantic_candidate_rows(
            con,
            granularity=granularity,
            source=source,
            path_filter=path_filter,
            category=category,
        )
        existing, missing = _load_persistent_embeddings(
            con, rows, granularity=granularity, backend_id=backend_id, model_name=semantic_model
        )
        batch_size = max(1, min(int(os.getenv("HERMES_MEMORY_SEARCH_GEMINI_BATCH_SIZE", str(_GEMINI_BATCH_SIZE))), 100))
        max_items = len(missing) if not max_batches else min(len(missing), max_batches * batch_size)
        processed = 0
        errors: list[str] = []
        for start in range(0, max_items, batch_size):
            batch_indices = missing[start:start + batch_size]
            docs = _semantic_documents([rows[i] for i in batch_indices], granularity)
            prepared = [
                _prepare_gemini_embedding_text(text, is_query=False, title=str(rows[i]["path"]))
                for text, i in zip(docs, batch_indices)
            ]
            try:
                new_vectors = _embed_gemini_texts_batched(
                    prepared, model=semantic_model, max_retries=(1 if retry_429 else 0)
                )
            except Exception as exc:  # noqa: BLE001 - return progress + blocker
                errors.append(str(exc))
                break
            _store_persistent_embeddings(
                con,
                [rows[i] for i in batch_indices],
                vectors=new_vectors,
                granularity=granularity,
                backend_id=backend_id,
                model_name=semantic_model,
            )
            con.commit()
            processed += len(batch_indices)

        embedded_after = con.execute(
            """
            SELECT COUNT(*) FROM semantic_embeddings
            WHERE granularity = ? AND backend = ? AND model = ?
            """,
            (granularity, backend_id, semantic_model),
        ).fetchone()[0]
        table_name = _vec_table_name(granularity, semantic_model)
        sqlite_vec_synced = False
        sqlite_vec_meta: dict[str, Any] = {}
        if int(embedded_after) > 0:
            sqlite_vec_synced, sqlite_vec_meta = _sync_sqlite_vec_rows(
                con,
                granularity=granularity,
                backend_id=backend_id,
                model_name=semantic_model,
                table_name=table_name,
            )
            con.commit()
    return {
        "success": not errors,
        "index_path": str(index_path),
        "index_updated": indexed,
        "granularity": granularity,
        "backend": backend_id,
        "model": semantic_model,
        "candidate_count": len(rows),
        "missing_before": len(missing),
        "processed": processed,
        "embedded_after": int(embedded_after),
        "remaining_estimate": max(0, len(missing) - processed),
        "sqlite_vec_synced": sqlite_vec_synced,
        "sqlite_vec": sqlite_vec_meta,
        "errors": errors,
    }

def memory_search_tool(
    query: str,
    source: str = "all",
    path_filter: str = "",
    limit: int = 8,
    freshness_seconds: int = 60,
    render_format: str = "json",
    granularity: str = "chunk",
    category: str = "",
    mode: str = "hybrid",
    semantic_backend: str = "gemini",
    semantic_model: str = "gemini-embedding-2",
    semantic_rebuild: str = "auto",
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
                granularity=granularity,
                category=category,
                mode=mode,
                semantic_backend=semantic_backend,
                semantic_model=semantic_model,
                semantic_rebuild=semantic_rebuild,
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
        "and write back a tight summary to the relevant ChatWorkspace context file or Hermes memory pointer. "
        "Two granularities: 'chunk' (default) returns heading-sized passages; 'observation' returns "
        "individual fact lines written as '- [category] text #tags' with exact file line numbers. Use "
        "observation granularity (or pass a category) to pull a specific fact or list all facts of a kind, "
        "e.g. category='decision' to find decisions across every project without reading whole files. "
        "Search mode defaults to 'hybrid' so normal calls use Gemini embeddings plus keyword ranking."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "query": {"type": "string", "description": "Search terms. Optional when granularity='observation' and a category/source/path filter is given (lists matching facts)."},
            "source": {
                "type": "string",
                "enum": ["all", "chatworkspace", "memories", "localops", "openclaw_legacy", "legacy_sessions", "discord"],
                "description": "Optional source filter. v1 indexes chatworkspace, Hermes memories, and LocalOps; other sources are reserved for migration follow-ups.",
            },
            "path_filter": {"type": "string", "description": "Optional substring filter for result paths, e.g. 'ngng' or 'microsoft/work_context'."},
            "limit": {"type": "integer", "description": "Maximum results, 1-25. Default 8."},
            "mode": {
                "type": "string",
                "enum": ["keyword", "semantic", "hybrid"],
                "description": "Search mode. 'hybrid' (default) uses semantic + keyword reciprocal-rank fusion; 'keyword' uses SQLite FTS only; 'semantic' uses the selected semantic backend only.",
            },
            "semantic_backend": {
                "type": "string",
                "enum": ["sklearn", "gemini"],
                "description": "Semantic backend for mode='semantic' or 'hybrid'. Default 'gemini' uses Gemini embeddings and requires GEMINI_API_KEY or GOOGLE_API_KEY. 'sklearn' is local/offline LSA over TF-IDF.",
            },
            "semantic_model": {
                "type": "string",
                "description": "Embedding model name when semantic_backend='gemini'. Default: gemini-embedding-2.",
            },
            "semantic_rebuild": {
                "type": "string",
                "enum": ["auto", "never", "force"],
                "description": "Controls semantic cache rebuilds. Default 'auto' may embed bounded/cold candidates; 'never' uses only persisted/cached vectors plus keyword fallback; 'force' explicitly allows a rebuild.",
            },
            "granularity": {
                "type": "string",
                "enum": ["chunk", "observation"],
                "description": "'chunk' (default) returns passages; 'observation' returns individual '- [category] ...' fact lines with exact line numbers.",
            },
            "category": {
                "type": "string",
                "description": "Optional observation-category filter (e.g. 'decision', 'status', 'risk', 'todo', 'preference'). Implies granularity='observation'. Combine with an empty query to list every fact of that category.",
            },
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
        granularity=args.get("granularity", "chunk"),
        category=args.get("category", ""),
        mode=args.get("mode", "hybrid"),
        semantic_backend=args.get("semantic_backend", "gemini"),
        semantic_model=args.get("semantic_model", "gemini-embedding-2"),
        semantic_rebuild=args.get("semantic_rebuild", "auto"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_memory_search_requirements,
    emoji="🔎",
)
