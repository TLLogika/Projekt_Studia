"""Konfiguracja z pliku secret/.env, bez ujawniania wartości poufnych."""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, field
from io import StringIO
from pathlib import Path

from dotenv import dotenv_values

from academic.errors import ConfigurationError

ROOT = Path(__file__).resolve().parent.parent


def read_env(path: Path) -> dict[str, str]:
    """Obsługuje standardowe KEY=value i dostarczony zapis key: value.

    Separator rozpoznajemy wyłącznie po nazwie klucza. Znaki '=' i ':'
    wewnątrz hasła nie zmieniają sposobu parsowania wartości.
    """
    if not path.is_file():
        raise ConfigurationError(
            "Brak pliku secret/.env. Uzupełnij konfigurację połączenia z bazą."
        )
    assignments = []
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        match = re.match(r"^\s*([A-Za-z_][A-Za-z0-9_]*)\s*([=:])\s*(.*)$", line)
        if not match:
            raise ConfigurationError("Nieprawidłowy zapis w pliku konfiguracji .env.")
        key, separator, value = match.groups()
        if separator == ":":
            # Pierwotny format nie interpretuje znaków wewnątrz hasła.
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            value = "'" + value.replace("\\", "\\\\").replace("'", "\\'") + "'"
        assignments.append(f"{key}={value}")
    return {
        key.upper(): value
        for key, value in dotenv_values(
            stream=StringIO("\n".join(assignments)), interpolate=False
        ).items()
        if value is not None
    }


def _boolean(value: str, key: str) -> bool:
    if value.lower() in {"true", "yes", "1"}:
        return True
    if value.lower() in {"false", "no", "0"}:
        return False
    raise ConfigurationError(f"{key} musi mieć wartość true albo false.")


def _integer(value: str, key: str, low: int, high: int) -> int:
    try:
        number = int(value)
    except ValueError:
        raise ConfigurationError(f"{key} musi być liczbą całkowitą.") from None
    if not low <= number <= high:
        raise ConfigurationError(f"{key} musi być w zakresie {low}–{high}.")
    return number


@dataclass(frozen=True, repr=False)
class Settings:
    server: str
    port: int
    database: str
    username: str
    password: str = field(repr=False)
    driver: str = "ODBC Driver 18 for SQL Server"
    encrypt: bool = True
    trust_server_certificate: bool = False
    semesters_item_code: str = "semesters"
    auth_database: str | None = None
    auth_schema: str = "org"
    auth_table: str = "AccountCredentials"
    auth_provider: str = "database"
    auth_local_credentials_file: str = "secret/test_accounts.json"
    session_ttl_minutes: int = 480
    session_idle_minutes: int = 30

    @classmethod
    def load(cls, path: Path | None = None) -> Settings:
        values = read_env(path or ROOT / "secret" / ".env")
        # Zmienne środowiska mają pierwszeństwo nad plikiem .env.
        values.update({key.upper(): value for key, value in os.environ.items()})

        def get(key: str, alias: str | None = None, default: str = "") -> str:
            return values.get(key, values.get(alias, default) if alias else default)

        server = get("DB_SERVER", "SERVER_NAME").strip().removeprefix("tcp:")
        port_text = get("DB_PORT", "PORT").strip()
        # Dostarczony server_name kończy się przecinkiem; port jest osobno.
        server = server.rstrip(",")
        if "," in server:
            server, embedded_port = server.rsplit(",", 1)
            if port_text and embedded_port != port_text:
                raise ConfigurationError("Port w DB_SERVER i DB_PORT jest różny.")
            port_text = embedded_port
        required = {
            "DB_SERVER": server,
            "DB_PORT": port_text,
            "DB_NAME": get("DB_NAME", "DATABASE"),
            "DB_USER": get("DB_USER", "USERNAME"),
            "DB_PASSWORD": get("DB_PASSWORD", "PASSWORD"),
        }
        missing = [key for key, value in required.items() if not value]
        if missing:
            raise ConfigurationError("Uzupełnij w secret/.env: " + ", ".join(missing) + ".")
        return cls(
            server=server,
            port=_integer(port_text, "DB_PORT", 1, 65535),
            database=required["DB_NAME"],
            username=required["DB_USER"],
            password=required["DB_PASSWORD"],
            driver=get("DB_DRIVER", default="ODBC Driver 18 for SQL Server"),
            encrypt=_boolean(get("DB_ENCRYPT", default="true"), "DB_ENCRYPT"),
            trust_server_certificate=_boolean(
                get("DB_TRUST_SERVER_CERTIFICATE", default="false"),
                "DB_TRUST_SERVER_CERTIFICATE",
            ),
            semesters_item_code=get("SEMESTERS_ITEM_CODE", default="semesters"),
            auth_database=get("AUTH_DATABASE") or None,
            auth_schema=get("AUTH_SCHEMA", default="org"),
            auth_table=get("AUTH_TABLE", default="AccountCredentials"),
            auth_provider=get("AUTH_PROVIDER", default="database"),
            auth_local_credentials_file=get("AUTH_LOCAL_CREDENTIALS_FILE", default="secret/test_accounts.json"),
            session_ttl_minutes=_integer(
                get("SESSION_TTL_MINUTES", default="480"), "SESSION_TTL_MINUTES", 1, 1440
            ),
            session_idle_minutes=_integer(
                get("SESSION_IDLE_MINUTES", default="30"), "SESSION_IDLE_MINUTES", 1, 1440
            ),
        )
