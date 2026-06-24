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
import pickle
import re
import sqlite3
import time
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
_SCHEMA_VERSION = 2
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
        """
    )
    # Backfill/rebuild is cheap at this scale and keeps external SQLite copies
    # from drifting if a user edited tables manually.
    con.execute("INSERT INTO chunks_fts(chunks_fts) VALUES('rebuild')")
    con.execute("INSERT INTO observations_fts(observations_fts) VALUES('rebuild')")
    con.commit()
    _migrate_schema(con)


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
            con.execute("DELETE FROM files")
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


def _semantic_index_path(index_path: Path | str, granularity: str) -> Path:
    """Return the sidecar semantic cache path for a SQLite memory index."""
    path = Path(index_path).expanduser()
    safe_granularity = re.sub(r"[^A-Za-z0-9_-]+", "_", granularity or "chunk")
    if path == DEFAULT_INDEX_PATH:
        return get_hermes_home() / f"memory_search_semantic_{safe_granularity}.pkl"
    return path.with_suffix(path.suffix + f".{safe_granularity}.semantic.pkl")


def _semantic_row_id(row: sqlite3.Row, granularity: str) -> str:
    if granularity == "observation":
        return f"obs:{row['path']}:{row['line']}:{row['category']}:{row['text']}"
    return f"chunk:{row['path']}:{row['start_line']}:{row['end_line']}:{row['text']}"


def _semantic_candidate_rows(
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


def _query_semantic_model(model: dict[str, Any], query: str) -> list[float]:
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
) -> tuple[dict[str, Any], bool, str]:
    cache_path = _semantic_index_path(index_path, granularity)
    backend = "sklearn_lsa_v1"
    fingerprint = _semantic_fingerprint(rows, granularity=granularity, backend=backend)
    try:
        with cache_path.open("rb") as fh:
            cached = pickle.load(fh)
        if (
            cached.get("fingerprint") == fingerprint
            and cached.get("granularity") == granularity
            and cached.get("backend", "").startswith("sklearn_")
        ):
            return cached, False, str(cache_path)
    except Exception:
        pass

    texts = _semantic_documents(rows, granularity)
    model = _build_semantic_model(texts)
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
    cache, rebuilt, cache_path = _load_or_build_semantic_cache(
        index_path=index_path,
        rows=rows,
        granularity=granularity,
    )
    scores = _query_semantic_model(cache["model"], query)
    ranked = sorted(zip(rows, scores), key=lambda item: item[1], reverse=True)
    ranked = [(row, score) for row, score in ranked if score > 0][:limit]
    return ranked, {
        "backend": cache.get("backend"),
        "rebuilt": rebuilt,
        "cache_path": cache_path,
        "candidate_count": len(rows),
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
    mode: str = "keyword",
) -> dict[str, Any]:
    query = (query or "").strip()
    granularity = (granularity or "chunk").strip().lower()
    category = (category or "").strip().lower()
    mode = (mode or "keyword").strip().lower()
    if mode not in {"keyword", "semantic", "hybrid"}:
        return {"success": False, "error": "mode must be one of: keyword, semantic, hybrid"}
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
        semantic_rows, semantic_meta = _search_semantic_rows(
            query,
            index_path=index_path,
            limit=limit,
            source=source,
            path_filter=path_filter,
            granularity=granularity,
            category=category,
        )
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
                query_strategy="semantic_lsa",
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
                query_strategy="semantic_lsa+keyword_rrf",
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
        query_strategy = "semantic_lsa+keyword_rrf"
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


def memory_search_tool(
    query: str,
    source: str = "all",
    path_filter: str = "",
    limit: int = 8,
    freshness_seconds: int = 60,
    render_format: str = "json",
    granularity: str = "chunk",
    category: str = "",
    mode: str = "keyword",
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
        "e.g. category='decision' to find decisions across every project without reading whole files."
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
                "description": "Search mode. 'keyword' (default) uses SQLite FTS; 'semantic' uses a local rebuildable sklearn LSA/TF-IDF vector cache for fuzzy recall; 'hybrid' tries semantic first and falls back to keyword when semantic finds no scored candidates.",
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
        mode=args.get("mode", "keyword"),
        task_id=kw.get("task_id"),
    ),
    check_fn=check_memory_search_requirements,
    emoji="🔎",
)
