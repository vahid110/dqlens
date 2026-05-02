"""Database connectors — abstraction layer for multi-DB support.

Each connector implements the BaseConnector interface. The factory
function get_connector() picks the right one based on the URL scheme.

Supported:
- postgresql:// → PostgreSQLConnector
- sqlite:// or *.db → SQLiteConnector
- mysql:// → MySQLConnector (Phase 2+)
- file paths (.parquet, .csv) → FileConnector (Phase 2+)
"""

from dqlens.connectors.base import BaseConnector
from dqlens.connectors.factory import get_connector

__all__ = ["BaseConnector", "get_connector"]
