"""SQLite catalogue build and read helpers used by the production API."""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any, Optional

import pandas as pd

from .device_search import diversify_ranked_results, merge_consumer_identities, normalize_search_text, prepare_catalog_search, search_catalog_frame


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
    frame = prepare_catalog_search(merge_consumer_identities(pd.read_csv(csv_path, low_memory=False)))
    frame = frame[[column for column in frame.columns if not column.startswith("_search")]]
    metadata = json.loads(metadata_path.read_text())
    metadata["catalog_record_count"] = int(len(frame))
    metadata["identity_supplement_count"] = max(0, int(len(frame)) - int(metadata.get("record_count", len(frame))))
    metadata["effective_categories"] = frame["category"].value_counts().to_dict()
    metadata["search_schema_version"] = "4.0"
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
                "CREATE VIRTUAL TABLE gadget_search USING fts5(product_id UNINDEXED, manufacturer, name, model_number, category, source_name, search_aliases, tokenize='unicode61 remove_diacritics 2')"
            )
            connection.execute(
                "INSERT INTO gadget_search(product_id, manufacturer, name, model_number, category, source_name, search_aliases) SELECT product_id, manufacturer, name, model_number, category, source_name, search_aliases FROM gadgets"
            )
            connection.execute("INSERT INTO metadata(key, value) VALUES ('fts5', 'true')")
        except sqlite3.OperationalError:
            connection.execute("INSERT INTO metadata(key, value) VALUES ('fts5', 'false')")
        connection.commit()
    temporary.replace(output_path)
    return output_path


def _search_expression(query: str) -> str:
    tokens = normalize_search_text(query).split()
    tokens = [token for token in tokens if token]
    return " AND ".join(f'"{token}"*' for token in tokens)


class CatalogRepository:
    def __init__(self, path: Path = DEFAULT_DATABASE):
        self.path = path
        self._search_frame: Optional[pd.DataFrame] = None

    def _connection(self) -> sqlite3.Connection:
        if not self.path.exists():
            build_database(output_path=self.path)
        return connect(self.path, read_only=True)

    def _catalog(self) -> pd.DataFrame:
        if self._search_frame is None:
            with self._connection() as connection:
                self._search_frame = prepare_catalog_search(
                    pd.read_sql_query("SELECT * FROM gadgets", connection)
                )
        return self._search_frame

    @staticmethod
    def _public_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        public_columns = [column for column in frame.columns if not column.startswith("_search") and column != "search_aliases"]
        items: list[dict[str, Any]] = []
        for record in frame[public_columns].to_dict(orient="records"):
            clean: dict[str, Any] = {}
            for key, value in record.items():
                if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
                    clean[key] = None
                elif hasattr(value, "item"):
                    clean[key] = value.item()
                else:
                    clean[key] = value
            items.append(clean)
        return items

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
        ranked = search_catalog_frame(
            self._catalog(),
            query,
            categories=[category] if category else None,
            manufacturers=[manufacturer] if manufacturer else None,
            primary_only=primary_only,
        ).iloc[offset : offset + limit]
        return self._public_records(ranked)

    def resolve(self, query: str, limit: int = 12) -> dict[str, Any]:
        """Return diversified candidates plus facets from the full match set."""
        ranked = search_catalog_frame(self._catalog(), query, primary_only=True)
        category_counts = ranked["category"].fillna("Other").astype(str).value_counts().to_dict()
        manufacturer_counts: dict[str, int] = {}
        if not ranked.empty:
            for _, group in ranked.groupby("_search_manufacturer", sort=False):
                display = min(group["manufacturer"].fillna("Unknown").astype(str), key=lambda value: (value.isupper(), len(value), value))
                manufacturer_counts[display] = int(len(group))
        candidates = diversify_ranked_results(ranked, min(max(int(limit), 1), 50))
        return {
            "items": self._public_records(candidates),
            "total": int(len(ranked)),
            "facets": {
                "categories": {key: int(value) for key, value in category_counts.items()},
                "manufacturers": manufacturer_counts,
            },
        }

    def get(self, product_id: str) -> Optional[dict[str, Any]]:
        rows = self._catalog()[self._catalog()["product_id"].astype(str).eq(str(product_id))]
        records = self._public_records(rows.head(1))
        return records[0] if records else None

    def categories(self) -> list[dict[str, Any]]:
        frame = self._catalog()
        summary = (
            frame.groupby("category", as_index=False)
            .agg(records=("product_id", "size"), manufacturers=("_search_manufacturer", "nunique"))
            .sort_values("category")
        )
        return self._public_records(summary)

    def metadata(self) -> dict[str, Any]:
        with self._connection() as connection:
            return {row["key"]: json.loads(row["value"]) for row in connection.execute("SELECT key, value FROM metadata")}


if __name__ == "__main__":
    path = build_database()
    print(f"Built {path}")
