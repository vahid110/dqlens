"""Unit tests for the MySQL connector.

Tests URL parsing, type detection, and factory integration.
No live MySQL database needed.
"""

from __future__ import annotations

import pytest

from dqlens.connectors.factory import get_connector
from dqlens.connectors.mysql import MySQLConnector, _parse_mysql_url


class TestMySQLURLParsing:
    def test_full_url(self):
        params = _parse_mysql_url("mysql://admin:secret@db.example.com:3307/mydb")
        assert params["host"] == "db.example.com"
        assert params["port"] == 3307
        assert params["database"] == "mydb"
        assert params["user"] == "admin"
        assert params["password"] == "secret"

    def test_default_port(self):
        params = _parse_mysql_url("mysql://user:pass@localhost/testdb")
        assert params["port"] == 3306

    def test_default_host(self):
        params = _parse_mysql_url("mysql:///testdb")
        assert params["host"] is None or params["host"] == "localhost"

    def test_no_password(self):
        params = _parse_mysql_url("mysql://root@localhost/db")
        assert params["user"] == "root"
        assert params["password"] == "" or params["password"] is None

    def test_pymysql_scheme(self):
        """mysql+pymysql:// should also work via factory."""
        c = get_connector("mysql+pymysql://user:pass@localhost/db")
        assert isinstance(c, MySQLConnector)


class TestMySQLFactory:
    def test_mysql_url_returns_mysql_connector(self):
        c = get_connector("mysql://user:pass@localhost:3306/db")
        assert isinstance(c, MySQLConnector)

    def test_mysql_pymysql_url(self):
        c = get_connector("mysql+pymysql://user:pass@localhost/db")
        assert isinstance(c, MySQLConnector)


class TestMySQLTypeDetection:
    def test_numeric_types(self):
        c = MySQLConnector("mysql://localhost/db")
        assert c.is_numeric_type("int") is True
        assert c.is_numeric_type("bigint") is True
        assert c.is_numeric_type("decimal") is True
        assert c.is_numeric_type("float") is True
        assert c.is_numeric_type("tinyint") is True
        assert c.is_numeric_type("mediumint") is True
        assert c.is_numeric_type("varchar") is False

    def test_text_types(self):
        c = MySQLConnector("mysql://localhost/db")
        assert c.is_text_type("varchar") is True
        assert c.is_text_type("text") is True
        assert c.is_text_type("longtext") is True
        assert c.is_text_type("enum") is True
        assert c.is_text_type("int") is False

    def test_temporal_types(self):
        c = MySQLConnector("mysql://localhost/db")
        assert c.is_temporal_type("datetime") is True
        assert c.is_temporal_type("timestamp") is True
        assert c.is_temporal_type("date") is True
        assert c.is_temporal_type("year") is True
        assert c.is_temporal_type("varchar") is False


class TestMySQLConnectorInit:
    def test_stores_url(self):
        c = MySQLConnector("mysql://user:pass@host:3306/db")
        assert c.connection_url == "mysql://user:pass@host:3306/db"

    def test_parses_params(self):
        c = MySQLConnector("mysql://admin:pw@myhost:3307/mydb")
        assert c._params["host"] == "myhost"
        assert c._params["port"] == 3307
        assert c._params["database"] == "mydb"
        assert c._params["user"] == "admin"
