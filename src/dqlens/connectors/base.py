"""Base connector interface — all database connectors implement this."""

from __future__ import annotations

from abc import ABC, abstractmethod
from contextlib import contextmanager
from typing import Any, Generator


class BaseConnector(ABC):
    """Abstract base for database connectors.

    Each method returns plain dicts/lists — no database-specific types
    leak into the profiler or rule engine.
    """

    @abstractmethod
    @contextmanager
    def connect(self) -> Generator[Any, None, None]:
        """Open a database connection. Yields a connection object."""
        ...

    @abstractmethod
    def list_tables(self, conn: Any, schema: str) -> list[dict[str, Any]]:
        """List tables with row count estimates.

        Returns list of dicts with keys: table_name, estimated_rows, total_bytes.
        """
        ...

    @abstractmethod
    def get_columns(self, conn: Any, schema: str, table: str) -> list[dict[str, Any]]:
        """Get column metadata.

        Returns list of dicts with keys: column_name, data_type, is_nullable,
        column_default, ordinal_position.
        """
        ...

    @abstractmethod
    def get_foreign_keys(self, conn: Any, schema: str) -> list[dict[str, str]]:
        """Discover FK relationships.

        Returns list of dicts with keys: source_table, source_column,
        target_table, target_column, constraint_name.
        """
        ...

    @abstractmethod
    def get_primary_keys(self, conn: Any, schema: str) -> dict[str, list[str]]:
        """Get PK columns. Returns table_name -> [column_names]."""
        ...

    @abstractmethod
    def get_unique_indexes(self, conn: Any, schema: str) -> dict[str, set[str]]:
        """Get unique columns. Returns table_name -> {column_names}."""
        ...

    @abstractmethod
    def get_exact_row_count(self, conn: Any, schema: str, table: str) -> int:
        """Get exact row count."""
        ...

    @abstractmethod
    def get_column_details(
        self, conn: Any, schema: str, table: str, column: str, data_type: str,
    ) -> dict[str, Any]:
        """Get detailed column stats (nulls, distinct, min/max, etc.)."""
        ...

    @abstractmethod
    def get_timestamp_columns(self, conn: Any, schema: str, table: str) -> list[str]:
        """Find timestamp/date columns."""
        ...

    @abstractmethod
    def get_latest_timestamp(
        self, conn: Any, schema: str, table: str, column: str,
    ) -> str | None:
        """Get the most recent timestamp value."""
        ...

    @abstractmethod
    def check_fk_integrity(
        self, conn: Any, schema: str,
        source_table: str, source_column: str,
        target_table: str, target_column: str,
    ) -> dict[str, int]:
        """Check FK integrity. Returns dict with total, non_null, orphaned."""
        ...

    @abstractmethod
    def sample_text_values(
        self, conn: Any, schema: str, table: str, column: str, limit: int,
    ) -> list[str]:
        """Sample non-null text values for pattern detection."""
        ...

    def is_numeric_type(self, data_type: str) -> bool:
        """Check if a data type is numeric. Override for DB-specific types."""
        numeric_types = {
            "integer", "bigint", "smallint", "numeric", "decimal",
            "real", "double precision", "float", "int", "int4", "int8",
            "float4", "float8", "serial", "bigserial", "tinyint",
            "mediumint", "int unsigned",
        }
        return data_type.lower() in numeric_types

    def is_temporal_type(self, data_type: str) -> bool:
        """Check if a data type is temporal. Override for DB-specific types."""
        temporal_types = {
            "timestamp without time zone", "timestamp with time zone",
            "date", "time without time zone", "time with time zone",
            "timestamp", "timestamptz", "datetime",
        }
        return data_type.lower() in temporal_types

    def is_text_type(self, data_type: str) -> bool:
        """Check if a data type is text. Override for DB-specific types."""
        text_types = {
            "character varying", "varchar", "character", "char", "text",
            "name", "citext", "tinytext", "mediumtext", "longtext",
        }
        return data_type.lower() in text_types
