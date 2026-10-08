"""Configuration and connection regression tests; all credentials are test literals."""

from pathlib import Path

import pyodbc
import pytest

from academic.config import Settings, read_env
from academic.db import Database
from academic.errors import ConfigurationError, DatabaseError


@pytest.fixture(autouse=True)
def isolated_configuration(monkeypatch):
    for name in (
        "DB_SERVER", "DB_PORT", "DB_NAME", "DB_USER", "DB_PASSWORD", "DB_DRIVER",
        "DB_ENCRYPT", "DB_TRUST_SERVER_CERTIFICATE", "SERVER_NAME", "PORT",
        "DATABASE", "USERNAME", "PASSWORD", "SEMESTERS_ITEM_CODE",
        "AUTH_DATABASE", "AUTH_SCHEMA", "AUTH_TABLE", "SESSION_TTL_MINUTES",
        "AUTH_PROVIDER", "AUTH_LOCAL_CREDENTIALS_FILE",
        "SESSION_IDLE_MINUTES",
    ):
        monkeypatch.delenv(name, raising=False)


def write_configuration(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "test.env"
    path.write_text(content, encoding="utf-8")
    return path


def test_supplied_colon_format_keeps_password_and_removes_server_comma(tmp_path):
    password = r"p:a=s;word}# ${VARIABLE} C:\folder\file 'quote'"
    path = write_configuration(
        tmp_path,
        "server_name: tcp:example.invalid,\nport: 1433\ndatabase: test_database\n"
        "username: test_account\npassword: " + password + "\n",
    )

    settings = Settings.load(path)

    assert settings.server == "example.invalid"
    assert settings.port == 1433
    assert settings.password == password
    assert settings.database == "test_database"
    assert settings.encrypt is True
    assert settings.trust_server_certificate is False


@pytest.mark.parametrize("quote", ["'", '"'])
def test_colon_quoted_value_preserves_internal_delimiters(tmp_path, quote):
    password = "alpha:beta=gamma;${NO_INTERPOLATION} # tail"
    path = write_configuration(tmp_path, f"password: {quote}{password}{quote}\n")
    assert read_env(path)["PASSWORD"] == password


def test_standard_env_is_literal_and_environment_overrides_file(tmp_path, monkeypatch):
    path = write_configuration(
        tmp_path,
        "DB_SERVER=example.invalid,1433\nDB_NAME=test_database\nDB_USER=test_account\n"
        "DB_PASSWORD='${NO_INTERPOLATION}:=;#literal'\n",
    )
    assert Settings.load(path).password == "${NO_INTERPOLATION}:=;#literal"

    monkeypatch.setenv("DB_PASSWORD", "environment:password=literal")
    assert Settings.load(path).password == "environment:password=literal"


def test_conflicting_ports_rejected_without_values(tmp_path):
    path = write_configuration(tmp_path, "DB_SERVER=private-host.invalid,1444\nDB_PORT=1433\n")
    with pytest.raises(ConfigurationError) as caught:
        Settings.load(path)
    assert "private-host.invalid" not in str(caught.value)


@pytest.mark.parametrize(
    "content",
    [
        "sensitive-marker invalid configuration line\n",
        "DB_SERVER=example.invalid\nDB_PORT=sensitive-marker\nDB_NAME=test_database\n"
        "DB_USER=test_account\nDB_PASSWORD=test_password\n",
    ],
)
def test_configuration_errors_do_not_echo_values(tmp_path, content, capsys, caplog):
    path = write_configuration(tmp_path, content)
    with pytest.raises(ConfigurationError) as caught:
        Settings.load(path)
    output = capsys.readouterr()
    assert "sensitive-marker" not in str(caught.value) + output.out + output.err + caplog.text


def test_settings_repr_does_not_include_connection_identity():
    settings = Settings("private-host.invalid", 1433, "private-database", "private-login", "private-password")
    assert all(value not in repr(settings) for value in (
        settings.server, settings.database, settings.username, settings.password,
    ))


def test_password_is_escaped_as_one_odbc_value(monkeypatch):
    password = "test;password}=:#${literal}"
    settings = Settings("example.invalid", 1433, "test_database", "test_account", password)
    observed = []

    class Connection:
        timeout = None

    def connect(connection_string, **kwargs):
        observed.append((connection_string, kwargs))
        return Connection()

    monkeypatch.setattr(pyodbc, "drivers", lambda: [settings.driver])
    monkeypatch.setattr(pyodbc, "connect", connect)

    connection = Database(settings)._connect()

    assert "PWD={test;password}}=:#${literal}}}" in observed[0][0]
    assert observed[0][1] == {"timeout": 8, "autocommit": False}
    assert connection.timeout == 15


@pytest.mark.parametrize("diagnostic_only", [False, True])
def test_driver_errors_never_disclose_diagnostic_credentials(monkeypatch, caplog, diagnostic_only):
    sensitive = "private-login private-host.invalid private-password"
    error = pyodbc.Error(sensitive) if diagnostic_only else pyodbc.Error("HY000", sensitive)
    db = Database(Settings("example.invalid", 1433, "test_database", "test_account", "test_password"))

    def fail_connection():
        raise error

    monkeypatch.setattr(db, "_connect", fail_connection)
    with pytest.raises(DatabaseError) as caught:
        with db.connection():
            pytest.fail("An unsuccessful connection must not be yielded.")
    assert sensitive not in str(caught.value) + caplog.text
    assert "private-password" not in str(caught.value) + caplog.text


def test_broken_connection_cleanup_preserves_safe_original_error(monkeypatch, caplog):
    sensitive = "private-login private-host.invalid private-password"

    class BrokenConnection:
        def rollback(self):
            raise pyodbc.Error("08003", sensitive)

        def close(self):
            raise pyodbc.Error("08003", sensitive)

    db = Database(Settings("example.invalid", 1433, "test_database", "test_account", "test_password"))
    monkeypatch.setattr(db, "_connect", BrokenConnection)
    with pytest.raises(DatabaseError) as caught:
        with db.connection():
            raise pyodbc.Error("08S01", sensitive)
    assert sensitive not in str(caught.value) + caplog.text
    assert "Nie można połączyć" in str(caught.value)
