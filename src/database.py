"""SQLite catalogue build and read helpers used by the production API."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

import pandas as pd


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DATABASE = ROOT / "data" / "catalog.db"


def connect(path: Path = DEFAULT_DATABASE, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path}?mode=ro", uri=True, check_same_thread=False)
    else:
        connection = sqlite3.connect(path, check_same_thread=False)
    connection.row_factory = sqlite3.Row
    return connection


def build_database(
    csv_path: Path = ROOT / "data" / "official_gadgets.csv",
    metadata_path: Path = ROOT / "data" / "source_metadata.json",
    output_path: Path = DEFAULT_DATABASE,
) -> Path:
    frame = pd.read_csv(csv_path, low_memory=False)
    metadata = json.loads(metadata_path.read_text())
    output_path.parent.mkdir(exist_ok=True)
    temporary = output_path.with_suffix(".db.next")
    if temporary.exists():
        temporary.unlink()
    with connect(temporary) as connection:
        frame.to_sql("gadgets", connection, index=False, if_exists="replace")
        connection.execute("CREATE UNIQUE INDEX idx_gadgets_product_id ON gadgets(product_id)")
        connection.execute("CREATE INDEX idx_gadgets_category ON gadgets(category)")
        connection.execute("CREATE INDEX idx_gadgets_manufacturer ON gadgets(manufacturer)")
        connection.execute("CREATE INDEX idx_gadgets_entity ON gadgets(entity_key)")
        connection.execute("CREATE INDEX idx_gadgets_primary ON gadgets(is_primary_record)")
        connection.execute("CREATE TABLE metadata (key TEXT PRIMARY KEY, value TEXT NOT NULL)")
        connection.executemany(
            "INSERT INTO metadata(key, value) VALUES (?, ?)",
            [(str(key), json.dumps(value, ensure_ascii=False)) for key, value in metadata.items()],
        )
        try:
            connection.execute(
                "CREATE VIRTUAL TABLE gadget_search USING fts5(product_id UNINDEXED, manufacturer, name, model_number, category, source_name, tokenize='unicode61 remove_diacritics 2')"
            )
            connection.execute(
                "INSERT INTO gadget_search(product_id, manufacturer, name, model_number, category, source_name) SELECT product_id, manufacturer, name, model_number, category, source_name FROM gadgets"
            )
            connection.execute("INSERT INTO metadata(key, value) VALUES ('fts5', 'true')")
        except sqlite3.OperationalError:
            connection.execute("INSERT INTO metadata(key, value) VALUES ('fts5', 'false')")
        connection.commit()
    temporary.replace(output_path)
    return output_path


def _search_expression(query: str) -> str:
    tokens = ["".join(char for char in token if char.isalnum()) for token in query.split()]
    tokens = [token for token in tokens if token]
    return " AND ".join(f'"{token}"*' for token in tokens)


class CatalogRepository:
    def __init__(self, path: Path = DEFAULT_DATABASE):
        self.path = path

    def _connection(self) -> sqlite3.Connection:
        if not self.path.exists():
            build_database(output_path=self.path)
        return connect(self.path, read_only=True)

    def search(
        self,
        query: str = "",
        category: Optional[str] = None,
        manufacturer: Optional[str] = None,
        limit: int = 50,
        offset: int = 0,
        primary_only: bool = True,
    ) -> list[dict[str, Any]]:
        limit = min(max(int(limit), 1), 250)
        offset = max(int(offset), 0)
        filters, parameters = [], []
        if category:
            filters.append("g.category = ?")
            parameters.append(category)
        if manufacturer:
            filters.append("g.manufacturer = ?")
            parameters.append(manufacturer)
        if primary_only:
            filters.append("g.is_primary_record = 1")
        where = f"WHERE {' AND '.join(filters)}" if filters else ""
        with self._connection() as connection:
            fts_enabled = connection.execute("SELECT value FROM metadata WHERE key='fts5'").fetchone()
            expression = _search_expression(query)
            if query.strip() and not expression:
                return []
            if expression and fts_enabled and json.loads(fts_enabled[0]):
                sql = f"""
                    SELECT g.* FROM gadget_search s
                    JOIN gadgets g ON g.product_id = s.product_id
                    {where + (' AND' if where else 'WHERE')} gadget_search MATCH ?
                    ORDER BY bm25(gadget_search), g.manufacturer, g.name LIMIT ? OFFSET ?
                """
                parameters.extend([expression, limit, offset])
            else:
                if query.strip():
                    filters.append("lower(g.manufacturer || ' ' || g.name || ' ' || g.model_number) LIKE ?")
                    parameters.append(f"%{query.casefold()}%")
                    where = f"WHERE {' AND '.join(filters)}"
                sql = f"SELECT g.* FROM gadgets g {where} ORDER BY g.manufacturer, g.name LIMIT ? OFFSET ?"
                parameters.extend([limit, offset])
            return [dict(row) for row in connection.execute(sql, parameters).fetchall()]

    def get(self, product_id: str) -> Optional[dict[str, Any]]:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM gadgets WHERE product_id = ?", (product_id,)).fetchone()
            return dict(row) if row else None

    def categories(self) -> list[dict[str, Any]]:
        with self._connection() as connection:
            rows = connection.execute(
                "SELECT category, COUNT(*) AS records, COUNT(DISTINCT manufacturer) AS manufacturers FROM gadgets GROUP BY category ORDER BY category"
            ).fetchall()
            return [dict(row) for row in rows]

    def metadata(self) -> dict[str, Any]:
        with self._connection() as connection:
            return {row["key"]: json.loads(row["value"]) for row in connection.execute("SELECT key, value FROM metadata")}


if __name__ == "__main__":
    path = build_database()
    print(f"Built {path}")
