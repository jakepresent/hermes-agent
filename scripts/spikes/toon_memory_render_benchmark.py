#!/usr/bin/env python3
"""Compare durable-memory search hit render formats.

This is a lightweight spike/benchmark for TOON-style row rendering. It uses a
small fixture by default and can also render live memory_search rows for a query.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Callable

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from tools.toon_renderer import render_toon_rows  # noqa: E402

ROWS: list[dict[str, Any]] = [
    {
        "source": "chatworkspace",
        "path": "ChatWorkspace/ngng/context.md",
        "lines": "40-82",
        "score": -12.3,
        "snippet": "Controlled scan reads outrank AI screenshot impressions.",
    },
    {
        "source": "memories",
        "path": "memories/hermes-ops.md",
        "lines": "12-20",
        "score": -10.1,
        "snippet": "Hermes WSL Startup task starts gateway before login.",
    },
    {
        "source": "localops",
        "path": "LocalOps/hermes/runbooks/gateway.md",
        "lines": "3-18",
        "score": -8.54,
        "snippet": "Check gateway.log and systemd user status before blaming Discord.",
    },
]

COLUMNS = ["source", "path", "lines", "score", "snippet"]


def _rough_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _render_pretty_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, indent=2, ensure_ascii=False)


def _render_compact_json(rows: list[dict[str, Any]]) -> str:
    return json.dumps(rows, ensure_ascii=False, separators=(",", ":"))


def _render_markdown(rows: list[dict[str, Any]]) -> str:
    lines = []
    for row in rows:
        lines.append(
            f"- {row['source']} {row['path']}:{row['lines']} "
            f"score={row['score']}: {row['snippet']}"
        )
    return "\n".join(lines)


def _render_toon_comma(rows: list[dict[str, Any]]) -> str:
    return render_toon_rows("hits", rows, COLUMNS)


def _render_toon_tab(rows: list[dict[str, Any]]) -> str:
    return render_toon_rows("hits", rows, COLUMNS, delimiter="\t")


def _live_rows(query: str, limit: int) -> list[dict[str, Any]]:
    from tools.memory_search_tool import _compact_context_path, search_index

    payload = search_index(query, limit=limit)
    rows = []
    for hit in payload.get("results", []):
        rows.append(
            {
                "source": hit["source"],
                "path": _compact_context_path(str(hit["path"]), str(hit["source"])),
                "lines": f"{hit['start_line']}-{hit['end_line']}",
                "score": round(float(hit["score"]), 3),
                "snippet": hit["snippet"],
            }
        )
    return rows


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--query", help="Use live memory_search rows for this query instead of fixture rows")
    parser.add_argument("--limit", type=int, default=8)
    args = parser.parse_args()

    rows = _live_rows(args.query, args.limit) if args.query else ROWS
    renderers: list[tuple[str, Callable[[list[dict[str, Any]]], str]]] = [
        ("pretty_json", _render_pretty_json),
        ("compact_json", _render_compact_json),
        ("markdown", _render_markdown),
        ("toon_comma", _render_toon_comma),
        ("toon_tab", _render_toon_tab),
    ]
    outputs = [(name, renderer(rows)) for name, renderer in renderers]
    baseline_chars = len(dict(outputs)["compact_json"])
    baseline_tokens = _rough_tokens(dict(outputs)["compact_json"])

    print(f"rows={len(rows)}")
    print("format          chars   est_tokens   savings_vs_compact_json")
    for name, text in outputs:
        chars = len(text)
        tokens = _rough_tokens(text)
        if name == "compact_json":
            savings = "baseline"
        else:
            char_savings = 1 - (chars / baseline_chars) if baseline_chars else 0
            token_savings = 1 - (tokens / baseline_tokens) if baseline_tokens else 0
            savings = f"{char_savings:>6.1%} chars / {token_savings:>6.1%} tokens"
        print(f"{name:<15} {chars:>6} {tokens:>12}   {savings}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
