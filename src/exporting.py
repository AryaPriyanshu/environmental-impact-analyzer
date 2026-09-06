"""Safe, deterministic exports for user-visible tabular data."""
from __future__ import annotations

from io import StringIO
from typing import Any

import pandas as pd


FORMULA_PREFIXES = ("=", "+", "-", "@")
FORMULA_HIDING_CHARACTERS = frozenset(
    {"\u200b", "\u200e", "\u200f", "\u202a", "\u202b", "\u202c", "\u202d", "\u202e", "\u2066", "\u2067", "\u2068", "\u2069", "\u2060", "\ufeff"}
)
MAX_CSV_ROWS = 250_000
MAX_CSV_COLUMNS = 256
MAX_CSV_CELL_CHARS = 32_767
MAX_CSV_BYTES = 50 * 1024 * 1024


class ExportError(ValueError):
    """The requested tabular export exceeds a safe deterministic boundary."""


def spreadsheet_safe_value(value: Any) -> Any:
    """Prevent spreadsheet applications from executing exported cell formulas.

    Source-backed catalogues and custom device names are untrusted text. Prefixing
    formula-looking values with an apostrophe preserves their visible content in
    Excel-compatible applications without evaluating it.
    """
    if not isinstance(value, str):
        return value
    stripped = value.lstrip()
    while stripped and stripped[0] in FORMULA_HIDING_CHARACTERS:
        stripped = stripped[1:].lstrip()
    if (
        stripped.startswith(FORMULA_PREFIXES)
        or value.startswith(("\t", "\r", "\n"))
        or (value and value[0] in FORMULA_HIDING_CHARACTERS)
    ):
        return "'" + value
    return value


def spreadsheet_safe_frame(frame: pd.DataFrame) -> pd.DataFrame:
    """Return a copy whose text cells are safe to open in a spreadsheet."""
    if not isinstance(frame, pd.DataFrame):
        raise TypeError("frame must be a pandas DataFrame")
    rows, columns = frame.shape
    if rows > MAX_CSV_ROWS or columns > MAX_CSV_COLUMNS:
        raise ExportError(
            f"CSV export exceeds {MAX_CSV_ROWS} rows or {MAX_CSV_COLUMNS} columns"
        )
    for column in frame.select_dtypes(include=["object", "string"]).columns:
        if frame[column].map(lambda value: isinstance(value, str) and len(value) > MAX_CSV_CELL_CHARS).any():
            raise ExportError(f"CSV cell exceeds {MAX_CSV_CELL_CHARS} characters")
    safe = frame.copy()
    for column in safe.select_dtypes(include=["object", "string"]).columns:
        safe[column] = safe[column].map(spreadsheet_safe_value)
    return safe


def csv_bytes(frame: pd.DataFrame) -> bytes:
    """Encode a spreadsheet-safe CSV with a UTF-8 BOM for broad compatibility."""
    buffer = StringIO()
    spreadsheet_safe_frame(frame).to_csv(buffer, index=False, lineterminator="\n")
    encoded = buffer.getvalue().encode("utf-8-sig")
    if len(encoded) > MAX_CSV_BYTES:
        raise ExportError(f"CSV export exceeds {MAX_CSV_BYTES} bytes")
    return encoded


__all__ = [
    "ExportError",
    "MAX_CSV_BYTES",
    "MAX_CSV_CELL_CHARS",
    "MAX_CSV_COLUMNS",
    "MAX_CSV_ROWS",
    "csv_bytes",
    "spreadsheet_safe_frame",
    "spreadsheet_safe_value",
]
