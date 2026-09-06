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
BOOLEAN_FIELDS = frozenset(
    {
        "replaceable_battery",
        "observed_energy",
        "observed_repairability",
        "observed_carbon",
        "observed_battery",
        "observed_durability",
        "observed_software_support",
        "is_primary_record",
    }
)


def _boolean(value: Any) -> bool:
    if isinstance(value, str):
        return value.strip().casefold() in {"true", "1", "yes", "y"}
    return bool(value)


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
        self._nullable_integer_fields: Optional[set[str]] = None

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

    def _read_frame(self, sql: str, parameters: tuple[Any, ...] = ()) -> pd.DataFrame:
        """Read database rows without populating the process-wide catalogue cache."""
        with self._connection() as connection:
            cursor = connection.execute(sql, parameters)
            columns = [description[0] for description in cursor.description or ()]
            frame = pd.DataFrame.from_records(cursor.fetchall(), columns=columns)
        # pandas promotes a nullable SQLite INTEGER to float when it reads the
        # full table.  Preserve that public JSON shape even when a candidate
        # subset happens not to contain any nulls.
        for column in self._nullable_integers():
            if column in frame:
                frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
        return frame

    def _nullable_integers(self) -> set[str]:
        if self._nullable_integer_fields is not None:
            return self._nullable_integer_fields
        with self._connection() as connection:
            integer_columns = [
                str(row["name"])
                for row in connection.execute("PRAGMA table_info(gadgets)")
                if str(row["type"]).upper() == "INTEGER"
            ]
            if not integer_columns:
                self._nullable_integer_fields = set()
                return self._nullable_integer_fields
            expressions = []
            for column in integer_columns:
                quoted = column.replace('"', '""')
                expressions.append(f'MAX("{quoted}" IS NULL) AS "{quoted}"')
            nulls = connection.execute(f"SELECT {', '.join(expressions)} FROM gadgets").fetchone()
        self._nullable_integer_fields = {column for column in integer_columns if nulls[column]}
        return self._nullable_integer_fields

    def _fts_candidates(self, query: str) -> pd.DataFrame:
        expression = _search_expression(query)
        if not expression:
            return pd.DataFrame()
        return self._read_frame(
            """
            SELECT gadgets.*
            FROM gadget_search
            JOIN gadgets ON gadgets.product_id = gadget_search.product_id
            WHERE gadget_search MATCH ?
            """,
            (expression,),
        )

    def _sql_candidates(self, query: str) -> pd.DataFrame:
        """Return a conservative literal candidate set when FTS5 is unavailable.

        ``search_aliases`` is normalized when the snapshot is built.  The SQL
        predicates intentionally over-select (for example, a term may occur in
        the middle of a token); the existing Python ranker remains the source of
        truth and removes those false positives.
        """
        terms = normalize_search_text(query).split()
        if not terms:
            return pd.DataFrame()
        identity = (
            "LOWER(COALESCE(manufacturer, '') || ' ' || COALESCE(name, '') || ' ' || "
            "COALESCE(model_number, '') || ' ' || COALESCE(search_aliases, ''))"
        )
        predicates = " AND ".join(f"INSTR({identity}, ?) > 0" for _ in terms)
        return self._read_frame(f"SELECT * FROM gadgets WHERE {predicates}", tuple(terms))

    def _fuzzy_ranked(self, query: str, rank_options: dict[str, Any]) -> pd.DataFrame:
        """Find typo candidates from identity fields, then fetch only full matches."""
        identities = self._read_frame(
            """
            SELECT product_id, manufacturer, name, model_number, category,
                   source_name, search_aliases, is_primary_record,
                   observed_field_count, raw_product_type, manufacturing_kg,
                   lifespan_years, weight_kg, observed_energy, active_power_w,
                   observed_battery, battery_wh
            FROM gadgets
            """
        )
        ranked_identities = search_catalog_frame(
            prepare_catalog_search(identities), query, **rank_options
        )
        if ranked_identities.empty:
            return ranked_identities

        product_ids = ranked_identities["product_id"].astype(str).tolist()
        frames: list[pd.DataFrame] = []
        # Stay below the conservative SQLite parameter limit used by older
        # distributions.  Typical typo recovery fetches fewer than ten rows.
        for start in range(0, len(product_ids), 500):
            chunk = product_ids[start : start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            frames.append(
                self._read_frame(
                    f"SELECT * FROM gadgets WHERE product_id IN ({placeholders})",
                    tuple(chunk),
                )
            )
        candidates = prepare_catalog_search(pd.concat(frames, ignore_index=True))
        candidate_order = {product_id: index for index, product_id in enumerate(product_ids)}
        candidates["_candidate_order"] = candidates["product_id"].astype(str).map(candidate_order)
        return candidates.sort_values("_candidate_order", kind="stable").drop(columns="_candidate_order")

    def _ranked(
        self,
        query: str,
        *,
        category: Optional[str] = None,
        manufacturer: Optional[str] = None,
        primary_only: bool = True,
    ) -> pd.DataFrame:
        """Retrieve SQL candidates, then apply the compatibility ranker.

        FTS5 handles the normal path.  A literal SQL scan supports SQLite builds
        without FTS5 and older snapshots, while a lightweight identity pass
        preserves typo recovery without retaining every catalogue field.
        """
        normalized_query = normalize_search_text(query)
        rank_options = {
            "categories": [category] if category else None,
            "manufacturers": [manufacturer] if manufacturer else None,
            "primary_only": primary_only,
        }
        if not normalized_query:
            return search_catalog_frame(self._catalog(), query, **rank_options)

        candidate_sources = (self._fts_candidates, self._sql_candidates)
        for candidate_source in candidate_sources:
            try:
                candidates = candidate_source(normalized_query)
            except sqlite3.Error:
                continue
            if candidates.empty:
                continue
            ranked = search_catalog_frame(candidates, query, **rank_options)
            if not ranked.empty:
                return ranked

        try:
            return self._fuzzy_ranked(query, rank_options)
        except sqlite3.Error:
            # Last-resort compatibility for snapshots that predate the identity
            # columns used by the lightweight typo-candidate pass.
            return search_catalog_frame(self._catalog(), query, **rank_options)

    @staticmethod
    def _public_records(frame: pd.DataFrame) -> list[dict[str, Any]]:
        public_columns = [column for column in frame.columns if not column.startswith("_search") and column != "search_aliases"]
        items: list[dict[str, Any]] = []
        for record in frame[public_columns].to_dict(orient="records"):
            clean: dict[str, Any] = {}
            for key, value in record.items():
                if value is None or (not isinstance(value, (list, dict)) and pd.isna(value)):
                    clean[key] = None
                elif key in BOOLEAN_FIELDS:
                    clean[key] = _boolean(value)
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
        ranked = self._ranked(
            query,
            category=category,
            manufacturer=manufacturer,
            primary_only=primary_only,
        ).iloc[offset : offset + limit]
        return self._public_records(ranked)

    def resolve(self, query: str, limit: int = 12) -> dict[str, Any]:
        """Return diversified candidates plus facets from the full match set."""
        ranked = self._ranked(query, primary_only=True)
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
        rows = self._read_frame(
            "SELECT * FROM gadgets WHERE product_id = ? LIMIT 1",
            (str(product_id),),
        )
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

    def readiness(self) -> tuple[bool, dict[str, Any]]:
        """Check the existing snapshot without creating or replacing it."""
        if not self.path.is_file():
            return False, {}
        try:
            with connect(self.path, read_only=True) as connection:
                record_count = int(connection.execute("SELECT COUNT(*) FROM gadgets").fetchone()[0])
                metadata = {
                    row["key"]: json.loads(row["value"])
                    for row in connection.execute("SELECT key, value FROM metadata")
                }
        except (OSError, sqlite3.Error, TypeError, ValueError):
            return False, {}
        metadata.setdefault("catalog_record_count", record_count)
        return record_count > 0, metadata


if __name__ == "__main__":
    path = build_database()
    print(f"Built {path}")
