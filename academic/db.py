"""Dostęp do istniejącej bazy SQL Server. Brak DDL, migracji i inicjalizacji."""

from __future__ import annotations

import logging
import re
from copy import deepcopy
from threading import RLock
from contextlib import contextmanager
from typing import Any, Iterator
from uuid import UUID

import pyodbc

from academic.config import Settings
from academic.errors import DatabaseError

logger = logging.getLogger(__name__)


def identifier(value: str) -> str:
    """Bezpieczne cytowanie identyfikatora; wartości SQL są parametrami."""
    if not value or "\x00" in value or len(value) > 128:
        raise DatabaseError("Nieprawidłowy identyfikator w konfiguracji bazy.")
    return "[" + value.replace("]", "]]") + "]"


def query(connection: Any, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    cursor = connection.cursor()
    try:
        cursor.execute(sql, params)
        if cursor.description is None:
            return []
        names = [column[0] for column in cursor.description]
        rows = []
        for values in cursor.fetchall():
            row = dict(zip(names, values))
            for name, value in row.items():
                if name.lower().endswith("id") and isinstance(value, (str, UUID)):
                    try:
                        row[name] = str(UUID(str(value)))
                    except ValueError:
                        pass
            rows.append(row)
        return rows
    finally:
        cursor.close()


def one(connection: Any, sql: str, params: tuple = ()) -> dict[str, Any] | None:
    rows = query(connection, sql, params)
    return rows[0] if rows else None


def _odbc_value(value: str) -> str:
    return "{" + value.replace("}", "}}") + "}"


class Database:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._columns_cache = {}
        self._columns_lock = RLock()

    def _connect(self):
        settings = self.settings
        if settings.driver not in pyodbc.drivers():
            raise DatabaseError(f"Zainstaluj sterownik {settings.driver}.")
        connection_string = ";".join(
            [
                "DRIVER=" + _odbc_value(settings.driver),
                "SERVER=" + _odbc_value(f"{settings.server},{settings.port}"),
                "DATABASE=" + _odbc_value(settings.database),
                "UID=" + _odbc_value(settings.username),
                "PWD=" + _odbc_value(settings.password),
                "Encrypt=" + ("yes" if settings.encrypt else "no"),
                "TrustServerCertificate=" + ("yes" if settings.trust_server_certificate else "no"),
                "APP={Studia - organizacja semestru}",
            ]
        )
        connection = pyodbc.connect(connection_string, timeout=8, autocommit=False)
        connection.timeout = 15
        return connection

    @contextmanager
    def connection(self) -> Iterator[Any]:
        connection = None
        try:
            connection = self._connect()
            yield connection
            connection.commit()
        except pyodbc.Error as error:
            if connection is not None:
                self._rollback(connection)
            # Szczegółowy komunikat ODBC może zawierać host/login. Nie logujemy go.
            candidate = str(error.args[0]) if error.args else ""
            state = candidate if re.fullmatch(r"[A-Z0-9]{5}", candidate) else "unknown"
            logger.warning("SQL Server operation failed (SQLSTATE %s)", state)
            if state == "28000":
                message = "Serwer odrzucił dane połączenia z bazą. Sprawdź secret/.env."
            elif state.startswith("08") or state in {"HYT00", "HYT01"}:
                message = "Nie można połączyć się z bazą. Sprawdź połączenie sieciowe i secret/.env."
            elif state == "23000":
                message = "Baza odrzuciła zapis naruszający jej istniejące reguły."
            else:
                message = "Nie udało się wykonać operacji w bazie danych."
            raise DatabaseError(message) from None
        except Exception:
            if connection is not None:
                self._rollback(connection)
            raise
        finally:
            if connection is not None:
                try:
                    connection.close()
                except pyodbc.Error:
                    logger.warning("SQL Server connection cleanup failed")

    @staticmethod
    def _rollback(connection):
        try:
            connection.rollback()
        except pyodbc.Error:
            logger.warning("SQL Server rollback failed after an operation error")

    def table_exists(
        self, table: str, schema: str = "org", database: str | None = None, connection=None
    ) -> bool:
        def check(conn):
            catalog = identifier(database or self.settings.database)
            row = one(
                conn,
                f"SELECT 1 AS present FROM {catalog}.sys.tables AS t "
                f"JOIN {catalog}.sys.schemas AS s ON s.schema_id=t.schema_id "
                "WHERE s.name=? AND t.name=?",
                (schema, table),
            )
            return row is not None

        if connection is not None:
            return check(connection)
        with self.connection() as conn:
            return check(conn)

    def columns(self, table: str, schema: str = "org", connection=None) -> dict[str, dict]:
        cache_key = (schema, table)
        with self._columns_lock:
            if cache_key in self._columns_cache:
                return deepcopy(self._columns_cache[cache_key])

        def inspect(conn):
            rows = query(
                conn,
                "SELECT COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_DEFAULT, "
                "CHARACTER_MAXIMUM_LENGTH, COLLATION_NAME, NUMERIC_PRECISION, NUMERIC_SCALE, "
                "CONVERT(int,COLLATIONPROPERTY(COLLATION_NAME,'CodePage')) AS CODE_PAGE "
                "FROM INFORMATION_SCHEMA.COLUMNS WHERE TABLE_SCHEMA=? AND TABLE_NAME=?",
                (schema, table),
            )
            result = {row["COLUMN_NAME"]: row for row in rows}
            if result:
                # Schemat jest niezmienny; cache znika przy restarcie procesu.
                with self._columns_lock:
                    self._columns_cache[cache_key] = deepcopy(result)
            return result

        if connection is not None:
            return inspect(connection)
        with self.connection() as conn:
            return inspect(conn)
