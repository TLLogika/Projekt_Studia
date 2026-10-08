"""Wspólne operacje na istniejących rekordach org; bez DDL i seedów."""

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from uuid import UUID

from academic.db import identifier, one, query
from academic.errors import ConflictError, ValidationError

TABLES = frozenset({
    "Semesters", "Groups", "LectureGroups", "LectureGroupTypes", "LectureGroupsElements",
    "GroupMembers", "Subjects", "SubjectTypes", "SubjectsSemesters", "ProgramsOfStudies",
    "Studies", "Specializations", "FieldsOfStudies", "ModesOfStudies", "StudiesDegrees",
    "Teachers", "Students", "AcademicTitles", "Persons", "SemesterCalendars",
    "SemesterCalendarDates", "DailySchedules", "DailyScheduleTypes",
    "SemesterOrganizations", "SemesterOrganizationSchedules", "Countries", "Notes",
})
READ_ONLY = frozenset({"Id", "Timestamp", "OldId", "PeriodStart", "PeriodEnd"})


def table_name(table):
    if table not in TABLES:
        raise ValidationError("Nieobsługiwana tabela modułu.")
    return "[org]." + identifier(table)


def record_id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Nieprawidłowy identyfikator rekordu.") from None


def get_record(connection, table, id, lock=False):
    hint = " WITH (UPDLOCK,HOLDLOCK)" if lock else ""
    return one(connection, f"SELECT * FROM {table_name(table)}{hint} WHERE Id=?", (record_id(id),))


def list_records(connection, table, filters=None, active_only=False):
    clauses, params = [], []
    for key, value in (filters or {}).items():
        quoted = identifier(key)
        if value is None:
            clauses.append(quoted + " IS NULL")
        else:
            clauses.append(quoted + "=?")
            params.append(value)
    if active_only and table not in {"Students", "Teachers"}:
        clauses.append("IsActive=1")
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    return query(connection, f"SELECT * FROM {table_name(table)}{where} ORDER BY Id", tuple(params))


def validate_values(db, connection, table, values, allowed_fields, required_fields=()):
    table_name(table)
    if not isinstance(values, dict) or set(values) - set(allowed_fields) or set(values) & READ_ONLY:
        raise ValidationError("Formularz zawiera niedozwolone pola.")
    columns = db.columns(table, connection=connection)
    if set(values) - set(columns):
        raise ValidationError("Pola formularza nie odpowiadają istniejącej tabeli.")
    normalized = {}
    for name, value in values.items():
        column = columns[name]
        if value is None:
            if column["IS_NULLABLE"] == "NO" or name in required_fields:
                raise ValidationError(f"Uzupełnij pole {name}.")
            normalized[name] = None
            continue
        kind = column["DATA_TYPE"]
        if kind in {"varchar", "nvarchar", "char", "nchar", "text", "ntext"}:
            if not isinstance(value, str) or "\x00" in value:
                raise ValidationError(f"Nieprawidłowy tekst w polu {name}.")
            value = value.strip()
            if (name in required_fields or column["IS_NULLABLE"] == "NO") and not value:
                raise ValidationError(f"Uzupełnij pole {name}.")
            maximum = column.get("CHARACTER_MAXIMUM_LENGTH")
            encoding = "utf-16-le" if kind in {"nvarchar", "nchar", "ntext"} else (
                "utf-8" if column.get("CODE_PAGE") == 65001 else f"cp{column.get('CODE_PAGE') or 1250}"
            )
            try:
                size = len(value.encode(encoding))
            except UnicodeEncodeError:
                raise ValidationError(f"Pole {name} zawiera znaki nieobsługiwane przez bazę.") from None
            if kind in {"nvarchar", "nchar", "ntext"}:
                size //= 2
            if maximum and maximum > 0 and size > maximum:
                raise ValidationError(f"Pole {name} przekracza limit bazy ({maximum}).")
        elif kind == "uniqueidentifier":
            value = record_id(value)
            if value == "00000000-0000-0000-0000-000000000000":
                raise ValidationError(f"Wybierz rzeczywisty rekord w polu {name}.")
        elif kind == "bit":
            if type(value) is not bool:
                raise ValidationError(f"Pole {name} wymaga wartości tak albo nie.")
        elif kind in {"tinyint", "smallint", "int", "bigint"}:
            bounds = {"tinyint": (0, 255), "smallint": (-32768, 32767),
                      "int": (-2147483648, 2147483647), "bigint": (-9223372036854775808, 9223372036854775807)}
            low, high = bounds[kind]
            if type(value) is not int or not low <= value <= high:
                raise ValidationError(f"Pole {name} wymaga liczby całkowitej {low}–{high}.")
        elif kind in {"decimal", "numeric", "money", "smallmoney"}:
            try:
                if isinstance(value, bool):
                    raise InvalidOperation
                value = Decimal(str(value))
                scale = column.get("NUMERIC_SCALE")
                precision = column.get("NUMERIC_PRECISION")
                if not value.is_finite():
                    raise InvalidOperation
                if scale is not None:
                    quantum = Decimal(1).scaleb(-int(scale))
                    if value.quantize(quantum) != value:
                        raise InvalidOperation
                    if precision and abs(value) >= Decimal(10) ** (int(precision) - int(scale)):
                        raise InvalidOperation
            except (InvalidOperation, ValueError, TypeError):
                raise ValidationError(f"Nieprawidłowa liczba w polu {name}.") from None
        elif kind in {"date", "datetime", "datetime2", "smalldatetime"}:
            if not isinstance(value, (date, datetime)):
                raise ValidationError(f"Wybierz datę w polu {name}.")
            if kind == "date" and isinstance(value, datetime):
                value = value.date()
        else:
            raise ValidationError(f"Nieobsługiwany typ pola {name}.")
        normalized[name] = value
    for name in required_fields:
        if name not in normalized or normalized[name] is None or normalized[name] == "":
            raise ValidationError(f"Uzupełnij pole {name}.")
    return normalized


def insert_record(connection, table, values):
    if not values or set(values) & READ_ONLY:
        raise ValidationError("Nieprawidłowe pola zapisu.")
    fields = ", ".join(identifier(name) for name in values)
    placeholders = ", ".join("?" for _ in values)
    row = one(connection, f"INSERT INTO {table_name(table)} ({fields}) OUTPUT INSERTED.* VALUES ({placeholders})", tuple(values.values()))
    if row is None:
        raise ConflictError("Baza nie potwierdziła utworzenia rekordu.")
    return row


def update_record(connection, table, id, values, expected_timestamp):
    if not values or set(values) & READ_ONLY:
        raise ValidationError("Nieprawidłowe pola zapisu.")
    if not isinstance(expected_timestamp, bytes) or len(expected_timestamp) != 8:
        raise ConflictError("Odśwież dane przed edycją.")
    assignments = ", ".join(identifier(name) + "=?" for name in values)
    row = one(connection, f"UPDATE {table_name(table)} SET {assignments} OUTPUT INSERTED.* WHERE Id=? AND Timestamp=?",
              tuple(values.values()) + (record_id(id), expected_timestamp))
    if row is None:
        raise ConflictError("Rekord zmieniła inna osoba lub został usunięty. Odśwież dane.")
    return row


def require_active_semester(connection, id):
    row = get_record(connection, "Semesters", id, lock=True)
    if row is None or not row["IsActive"]:
        raise ValidationError("Organizacja i harmonogram wymagają istniejącego aktywnego semestru.")
    return row


def acquire_write_lock(connection, resource):
    row = one(connection,
              "DECLARE @result int; EXEC @result=sys.sp_getapplock @Resource=?, @LockMode='Exclusive', "
              "@LockOwner='Transaction', @LockTimeout=10000; SELECT @result AS Result",
              ("academic:" + str(resource)[:220],))
    if row is None or row["Result"] < 0:
        raise ConflictError("Dane są aktualizowane przez inną osobę. Ponów zapis.")
