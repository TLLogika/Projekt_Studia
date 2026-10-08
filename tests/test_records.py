from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest

from academic.errors import ConflictError, ValidationError
from academic import records


def column(kind, **extra):
    return {"DATA_TYPE": kind, "IS_NULLABLE": "NO", "CHARACTER_MAXIMUM_LENGTH": None,
            "CODE_PAGE": 1250, **extra}


@pytest.fixture
def db():
    metadata = {
        "Name": column("varchar", CHARACTER_MAXIMUM_LENGTH=4),
        "Number": column("tinyint"), "IsActive": column("bit"),
        "EctsPoints": column("decimal", NUMERIC_PRECISION=4, NUMERIC_SCALE=1),
        "StartDate": column("date"), "StudiesId": column("uniqueidentifier"),
    }
    return SimpleNamespace(columns=lambda table, connection: metadata)


@pytest.mark.parametrize("values", [{"Number": -1}, {"Number": 256}, {"Number": True},
                                    {"IsActive": 1}, {"EctsPoints": Decimal("NaN")},
                                    {"EctsPoints": Decimal("1.11")}, {"EctsPoints": 1000},
                                    {"StudiesId": "00000000-0000-0000-0000-000000000000"},
                                    {"Name": "😀"}, {"Name": "abcde"}, {"StartDate": "2026-01-01"}])
def test_invalid_sql_values_are_rejected_before_writing(db, values):
    with pytest.raises(ValidationError):
        records.validate_values(db, object(), "Subjects", values, tuple(values))


def test_real_schema_constraints_are_respected(db):
    result = records.validate_values(db, object(), "Subjects",
                                    {"Name": " żółć ", "Number": 255, "IsActive": False,
                                     "EctsPoints": 3.5, "StartDate": date(2026, 10, 7)},
                                    ("Name", "Number", "IsActive", "EctsPoints", "StartDate"))
    assert result["Name"] == "żółć"
    assert result["EctsPoints"] == Decimal("3.5")


def test_unknown_tables_and_readonly_columns_cannot_be_written(db):
    with pytest.raises(ValidationError):
        records.validate_values(db, object(), "Roles", {"Name": "test"}, ("Name",))
    with pytest.raises(ValidationError):
        records.validate_values(db, object(), "Subjects", {"Timestamp": b"12345678"}, ("Timestamp",))


def test_optimistic_conflict_never_reports_success(monkeypatch):
    monkeypatch.setattr(records, "one", lambda *args: None)
    with pytest.raises(ConflictError):
        records.update_record(object(), "Groups", "71a0cf92-4c76-42bf-8e09-65a2c18b5c28", {"Name": "test"}, b"12345678")


@pytest.mark.parametrize("result", [None, {"Result": -1}, {"Result": -3}])
def test_failed_transaction_locks_refuse_write(monkeypatch, result):
    monkeypatch.setattr(records, "one", lambda *args: result)
    with pytest.raises(ConflictError):
        records.acquire_write_lock(object(), "semester:test")
