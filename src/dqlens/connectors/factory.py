"""Connector factory — picks the right connector based on URL scheme."""

from __future__ import annotations

from pathlib import Path

from dqlens.connectors.base import BaseConnector


def get_connector(connection_url: str) -> BaseConnector:
    """Create the appropriate connector for a connection URL.

    Supported schemes:
    - postgresql://, postgres:// → PostgreSQLConnector
    - sqlite:/// or *.db, *.sqlite → SQLiteConnector
    - mysql:// → MySQLConnector (not yet implemented)

    Args:
        connection_url: Database connection URL or file path.

    Returns:
        A BaseConnector instance.

    Raises:
        ValueError: If the scheme is not supported.
    """
    url = connection_url.strip()

    # PostgreSQL
    if url.startswith(("postgresql://", "postgres://")):
        from dqlens.connectors.postgresql import PostgreSQLConnector
        return PostgreSQLConnector(url)

    # SQLite — URL or file path
    if url.startswith("sqlite:///"):
        db_path = url.replace("sqlite:///", "", 1)
        from dqlens.connectors.sqlite import SQLiteConnector
        return SQLiteConnector(db_path)

    if url.startswith("sqlite://"):
        db_path = url.replace("sqlite://", "", 1)
        from dqlens.connectors.sqlite import SQLiteConnector
        return SQLiteConnector(db_path)

    # File path ending in .db or .sqlite
    path = Path(url)
    if path.suffix in (".db", ".sqlite", ".sqlite3"):
        from dqlens.connectors.sqlite import SQLiteConnector
        return SQLiteConnector(str(path))

    # MySQL
    if url.startswith(("mysql://", "mysql+pymysql://")):
        from dqlens.connectors.mysql import MySQLConnector
        return MySQLConnector(url)

    raise ValueError(
        f"Unsupported connection URL: {url}\n"
        f"Supported schemes: postgresql://, sqlite:///, *.db, *.sqlite"
    )
