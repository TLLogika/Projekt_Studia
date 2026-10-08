"""Backend permission, schema and transactional semester tests with no online DB."""

from dataclasses import replace
from datetime import date, datetime

import pytest

from academic.auth.models import AuthorizationError
from academic.config import Settings
from academic.db import Database
from academic.errors import ConfigurationError, ConflictError, ValidationError
from academic.semesters.models import SemesterInput
from academic.semesters.service import SemesterService
import academic.semesters.service as semester_module


SEMESTER_ID = "71a0cf92-4c76-42bf-8e09-65a2c18b5c28"
ORIGINAL_VERSION = b"\x00\x00\x00\x00\x00\x00\x00\x01"
NEXT_VERSION = b"\x00\x00\x00\x00\x00\x00\x00\x02"


@pytest.fixture
def draft():
    return SemesterInput("Semestr testowy", "Rok testowy", date(2026, 1, 1), date(2026, 6, 30), False, True)


def columns(*, encoding=1250, text_type="varchar", name_length=100):
    metadata = {
        name: {"DATA_TYPE": "uniqueidentifier" if name == "Id" else "bit"}
        for name in "Id StartDate EndDate IsActual IsActive Timestamp".split()
    }
    metadata["Timestamp"]["DATA_TYPE"] = "timestamp"
    for name, length in [("Name", name_length), ("AcademicYearName", 50)]:
        metadata[name] = {
            "DATA_TYPE": text_type,
            "CHARACTER_MAXIMUM_LENGTH": length,
            "CODE_PAGE": encoding,
        }
    return metadata


class TransactionConnection:
    def __init__(self):
        self.commits = 0
        self.rollbacks = 0
        self.closes = 0

    def commit(self):
        self.commits += 1

    def rollback(self):
        self.rollbacks += 1

    def close(self):
        self.closes += 1


class AuthGrants:
    def __init__(self, actions):
        self.actions = set(actions)
        self.checks = []

    def require(self, token, item_code, action, connection=None):
        self.checks.append((token, item_code, action, connection))
        if action not in self.actions:
            raise AuthorizationError("Brak uprawnień.")

    def can(self, token, item_code, action):
        return action in self.actions


@pytest.fixture
def backend(monkeypatch):
    connection = TransactionConnection()
    db = Database(Settings("example.invalid", 1433, "test_database", "test_account", "test_password"))
    metadata = columns()
    schema_calls = []
    writes = []
    auth = AuthGrants({"view", "create", "update"})
    monkeypatch.setattr(db, "_connect", lambda: connection)

    def inspect_schema(table, **kwargs):
        schema_calls.append(table)
        return metadata

    def execute_write(conn, sql, params=()):
        writes.append((sql, params))
        return {
            "Id": SEMESTER_ID, "Name": params[0], "AcademicYearName": params[1],
            "StartDate": params[2], "EndDate": params[3], "IsActual": params[4],
            "IsActive": params[5], "Timestamp": NEXT_VERSION,
        }

    monkeypatch.setattr(db, "columns", inspect_schema)
    monkeypatch.setattr(semester_module, "one", execute_write)
    monkeypatch.setattr(semester_module, "acquire_write_lock", lambda *args: None)
    monkeypatch.setattr(semester_module, "query", lambda *args: [])
    return SemesterService(db, auth), connection, metadata, schema_calls, writes


@pytest.mark.parametrize("operation", ["create", "update"])
def test_backend_refuses_writes_without_permission(backend, draft, operation):
    service, connection, _, schema_calls, writes = backend
    service.auth.actions.discard(operation)

    with pytest.raises(AuthorizationError):
        if operation == "create":
            service.create("session", draft)
        else:
            service.update("session", SEMESTER_ID, draft, ORIGINAL_VERSION)

    assert writes == []
    assert schema_calls == []
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closes == 1


@pytest.mark.parametrize(
    "changes",
    [
        {"name": " \t "},
        {"academic_year": ""},
        {"end_date": date(2025, 12, 31)},
        {"start_date": None},
        {"is_active": 1},
        {"name": "Nazwa\x00testowa"},
    ],
)
def test_invalid_semester_never_reaches_database_write(backend, draft, changes):
    service, connection, _, _, writes = backend
    with pytest.raises(ValidationError):
        service.create("session", replace(draft, **changes))
    assert writes == []
    assert connection.commits == 0
    assert connection.rollbacks == 1


def test_equal_dates_and_free_text_year_are_preserved(backend, draft):
    service, connection, _, _, writes = backend
    data = replace(draft, name="  Semestr  ", academic_year="  Rok testowy  ", end_date=draft.start_date)

    saved = service.create("session", data)

    assert saved.name == "Semestr"
    assert saved.academic_year == "Rok testowy"
    assert saved.duration_days == 0
    assert writes[0][1][2] == datetime(2026, 1, 1)
    assert connection.commits == 1
    assert connection.rollbacks == 0
    assert connection.closes == 1


@pytest.mark.parametrize(
    "start,end",
    [
        (datetime(2026, 1, 1, 10), date(2026, 1, 1)),
        (date(2026, 1, 1), datetime(2026, 1, 1, 10)),
        (datetime(2026, 1, 1, 20), datetime(2026, 1, 1, 10)),
    ],
)
def test_calendar_dates_are_normalized_before_validation(draft, start, end):
    normalized = replace(draft, start_date=start, end_date=end).validated()
    assert normalized.start_date == normalized.end_date == date(2026, 1, 1)
    assert type(normalized.start_date) is date


def test_varchar_uses_database_code_page_and_rejects_unsupported_characters(backend, draft):
    service, connection, metadata, _, writes = backend
    metadata["Name"]["CODE_PAGE"] = 1250
    saved = service.create("session", replace(draft, name="Zażółć gęślą jaźń"))
    assert saved.name == "Zażółć gęślą jaźń"

    with pytest.raises(ValidationError, match="nieobsługiwane"):
        service.create("session", replace(draft, name="Semestr 😀"))
    assert len(writes) == 1
    assert connection.commits == 1
    assert connection.rollbacks == 1


def test_utf8_varchar_limit_counts_bytes(backend, draft):
    service, connection, metadata, _, writes = backend
    metadata["Name"].update(CODE_PAGE=65001, CHARACTER_MAXIMUM_LENGTH=4)

    assert service.create("session", replace(draft, name="ąą")).name == "ąą"
    with pytest.raises(ValidationError, match="limit"):
        service.create("session", replace(draft, name="ąąą"))
    assert len(writes) == 1
    assert connection.rollbacks == 1


def test_nvarchar_limit_counts_utf16_units(backend, draft):
    service, _, metadata, _, writes = backend
    metadata["Name"].update(DATA_TYPE="nvarchar", CHARACTER_MAXIMUM_LENGTH=2)

    assert service.create("session", replace(draft, name="😀")).name == "😀"
    with pytest.raises(ValidationError, match="limit"):
        service.create("session", replace(draft, name="😀a"))
    assert len(writes) == 1


def test_schema_mismatch_is_reported_before_write(backend, draft):
    service, connection, metadata, _, writes = backend
    metadata["Timestamp"]["DATA_TYPE"] = "datetime2"
    with pytest.raises(ConfigurationError):
        service.create("session", draft)
    assert writes == []
    assert connection.rollbacks == 1


def test_update_uses_snapshot_version_and_returns_database_generated_version(backend, draft):
    service, connection, _, _, writes = backend

    saved = service.update("session", SEMESTER_ID, draft, ORIGINAL_VERSION)

    assert saved.timestamp == NEXT_VERSION
    assert writes[0][1][-2:] == (SEMESTER_ID, ORIGINAL_VERSION)
    assert "WHERE Id=? AND Timestamp=?" in writes[0][0]
    assert connection.commits == 1
    assert connection.rollbacks == 0


def test_stale_version_rolls_back_instead_of_claiming_save(backend, draft, monkeypatch):
    service, connection, _, _, writes = backend

    def reject_stale_update(conn, sql, params):
        writes.append((sql, params))
        assert params[-1] == ORIGINAL_VERSION
        return None

    monkeypatch.setattr(semester_module, "one", reject_stale_update)
    with pytest.raises(ConflictError, match="zmieniony"):
        service.update("session", SEMESTER_ID, draft, ORIGINAL_VERSION)
    assert connection.commits == 0
    assert connection.rollbacks == 1
    assert connection.closes == 1


@pytest.mark.parametrize("version", [None, "0x0000000000000001", b"short"])
def test_missing_snapshot_cannot_bypass_concurrency_check(backend, draft, version):
    service, connection, _, _, writes = backend
    with pytest.raises(ConflictError):
        service.update("session", SEMESTER_ID, draft, version)
    assert writes == []
    assert connection.commits == 0


def test_semester_dates_cannot_exclude_existing_active_calendar_date(backend, draft, monkeypatch):
    service, connection, _, _, writes = backend
    locked = []
    monkeypatch.setattr(semester_module, "acquire_write_lock", lambda *args: locked.append(args))
    def existing_date(conn, sql, params):
        assert locked == [(connection, "academic:organization-schedule")]
        assert params == (SEMESTER_ID, draft.start_date, draft.end_date)
        return [{"Id": "existing-date"}]
    monkeypatch.setattr(semester_module, "query", existing_date)
    with pytest.raises(ValidationError, match="aktywne daty"):
        service.update("session", SEMESTER_ID, draft, ORIGINAL_VERSION)
    assert not writes
    assert connection.rollbacks == 1
    assert connection.commits == 0
