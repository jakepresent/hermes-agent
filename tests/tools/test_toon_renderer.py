"""Tests for the constrained TOON row renderer."""

from __future__ import annotations

import pytest

from tools.toon_renderer import render_toon_rows


def test_render_simple_rows():
    out = render_toon_rows(
        "hits",
        [{"source": "memories", "path": "memory/travel.md", "lines": "5-10"}],
        ["source", "path", "lines"],
    )

    assert out == "hits[1]{source,path,lines}:\n  memories,memory/travel.md,5-10"


def test_quotes_comma_cells():
    out = render_toon_rows(
        "hits",
        [{"source": "memories", "snippet": "Bellevue, WA hotel"}],
        ["source", "snippet"],
    )

    assert out == 'hits[1]{source,snippet}:\n  memories,"Bellevue, WA hotel"'


def test_escapes_quote_cells():
    out = render_toon_rows(
        "hits",
        [{"source": "memories", "snippet": 'Jake said "stable release"'}],
        ["source", "snippet"],
    )

    assert out == 'hits[1]{source,snippet}:\n  memories,"Jake said ""stable release"""'


def test_escapes_newline_cells():
    out = render_toon_rows(
        "hits",
        [{"source": "memories", "snippet": "line one\nline two"}],
        ["source", "snippet"],
    )

    assert out == 'hits[1]{source,snippet}:\n  memories,"line one\\nline two"'


def test_renders_boolean_numeric_null_values():
    out = render_toon_rows(
        "hits",
        [{"enabled": True, "score": -12.34567, "note": None}],
        ["enabled", "score", "note"],
    )

    assert out == "hits[1]{enabled,score,note}:\n  true,-12.34567,null"


def test_renders_empty_rows_header():
    out = render_toon_rows("hits", [], ["source", "path"])

    assert out == "hits[0]{source,path}:"


def test_raises_for_missing_column():
    with pytest.raises(ValueError, match="missing column"):
        render_toon_rows("hits", [{"source": "memories"}], ["source", "path"])


def test_rejects_invalid_name():
    with pytest.raises(ValueError, match="name"):
        render_toon_rows("bad-name", [], ["source"])
