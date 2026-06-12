"""Small TOON-style renderer for uniform row data.

This intentionally implements only the constrained shape Hermes needs for
model-facing retrieved context: a named array of uniform dictionaries rendered
as a compact row table.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from typing import Any

_NAME_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_name(value: str, *, label: str) -> None:
    if not isinstance(value, str) or not _NAME_RE.fullmatch(value):
        raise ValueError(f"invalid {label}: {value!r}")


def _format_cell(value: Any, delimiter: str) -> str:
    if value is None:
        text = "null"
    elif isinstance(value, bool):
        text = "true" if value else "false"
    else:
        text = str(value)

    needs_quotes = (
        delimiter in text
        or "\n" in text
        or "\r" in text
        or '"' in text
        or text != text.strip()
    )
    if "\r" in text or "\n" in text:
        text = text.replace("\r", "\\r").replace("\n", "\\n")
        needs_quotes = True
    if '"' in text:
        text = text.replace('"', '""')
        needs_quotes = True
    if needs_quotes:
        return f'"{text}"'
    return text


def render_toon_rows(
    name: str,
    rows: list[dict[str, Any]],
    columns: Sequence[str],
    *,
    delimiter: str = ",",
) -> str:
    """Render uniform dictionaries as a TOON-style row table.

    Example:
        hits[1]{source,path,lines}:
          memories,memory/travel.md,5-10
    """

    _validate_name(name, label="name")
    if delimiter not in {",", "\t"}:
        raise ValueError("delimiter must be ',' or '\\t'")
    if not isinstance(rows, list):
        raise ValueError("rows must be a list of dictionaries")
    if not columns:
        raise ValueError("columns are required")
    for column in columns:
        _validate_name(column, label="column")

    header_columns = ",".join(columns)
    lines = [f"{name}[{len(rows)}]{{{header_columns}}}:"]
    for index, row in enumerate(rows):
        if not isinstance(row, dict):
            raise ValueError(f"row {index} is not a dictionary")
        missing = [column for column in columns if column not in row]
        if missing:
            raise ValueError(f"row {index} missing column(s): {', '.join(missing)}")
        cells = [_format_cell(row[column], delimiter) for column in columns]
        lines.append("  " + delimiter.join(cells))
    return "\n".join(lines)
