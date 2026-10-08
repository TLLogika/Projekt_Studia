"""Operacje semestru sprawdzają uprawnienia i pracują w transakcji."""

from __future__ import annotations

from datetime import datetime, time
from uuid import UUID

from academic.db import Database, one, query
from academic.errors import ConfigurationError, ConflictError, ValidationError
from academic.records import acquire_write_lock
from academic.semesters.models import Semester, SemesterInput

FIELDS = "Id, Name, AcademicYearName, StartDate, EndDate, IsActual, IsActive, Timestamp"
OUTPUT = ", ".join("INSERTED." + field.strip() for field in FIELDS.split(","))
LIST_SQL = f"SELECT {FIELDS} FROM [org].[Semesters] ORDER BY StartDate DESC, AcademicYearName DESC, Name, Id"
CREATE_SQL = (
    "INSERT INTO [org].[Semesters] "
    "(Name, AcademicYearName, StartDate, EndDate, IsActual, IsActive) "
    f"OUTPUT {OUTPUT} VALUES (?, ?, ?, ?, ?, ?)"
)
UPDATE_SQL = (
    "UPDATE [org].[Semesters] SET Name=?, AcademicYearName=?, StartDate=?, "
    "EndDate=?, IsActual=?, IsActive=? "
    f"OUTPUT {OUTPUT} WHERE Id=? AND Timestamp=?"
)


def _semester(row: dict) -> Semester:
    return Semester(
        str(row["Id"]), row["Name"], row["AcademicYearName"], row["StartDate"],
        row["EndDate"], bool(row["IsActual"]), bool(row["IsActive"]), row["Timestamp"]
    )


class SemesterService:
    def __init__(self, db: Database, auth):
        self.db = db
        self.auth = auth
        self.item_code = db.settings.semesters_item_code

    def can(self, token: str, action: str) -> bool:
        return self.auth.can(token, self.item_code, action)

    def _schema(self, connection):
        columns = self.db.columns("Semesters", connection=connection)
        if not all(field.strip() in columns for field in FIELDS.split(",")):
            raise ConfigurationError("Tabela org.Semesters nie ma wymaganych kolumn.")
        if columns["Timestamp"]["DATA_TYPE"] not in {"timestamp", "rowversion"}:
            raise ConfigurationError("Pole Timestamp w org.Semesters musi być wersją rekordu.")
        return columns

    def _validate(self, draft: SemesterInput, columns: dict) -> SemesterInput:
        draft = draft.validated()
        for column_name, value in [("Name", draft.name), ("AcademicYearName", draft.academic_year)]:
            column = columns[column_name]
            maximum = column["CHARACTER_MAXIMUM_LENGTH"]
            label = "Nazwa semestru" if column_name == "Name" else "Rok akademicki"
            if "\x00" in value:
                raise ValidationError(f"{label}: niedozwolony znak w tekście.")
            if column["DATA_TYPE"] in {"varchar", "char"}:
                code_page = column["CODE_PAGE"]
                if code_page:
                    encoding = "utf-8" if code_page == 65001 else f"cp{code_page}"
                    try:
                        length = len(value.encode(encoding))
                    except UnicodeEncodeError:
                        raise ValidationError(f"{label} zawiera znaki nieobsługiwane przez bazę.") from None
                else:
                    length = len(value)
            else:
                length = len(value.encode("utf-16-le")) // 2
            if maximum and maximum > 0 and length > maximum:
                raise ValidationError(f"{label} przekracza limit pola w bazie ({maximum}).")
        return draft

    def list(self, token: str) -> list[Semester]:
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            self._schema(connection)
            rows = query(connection, LIST_SQL)
            return [_semester(row) for row in rows]

    def create(self, token: str, draft: SemesterInput) -> Semester:
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "create", connection=connection)
            draft = self._validate(draft, self._schema(connection))
            row = one(
                connection,
                CREATE_SQL,
                self._params(draft),
            )
            return _semester(row)

    def update(
        self, token: str, id: str, draft: SemesterInput, expected_timestamp: bytes | str | None
    ) -> Semester:
        try:
            UUID(id)
        except (ValueError, TypeError, AttributeError):
            raise ValidationError("Nieprawidłowy identyfikator semestru.") from None
        if not isinstance(expected_timestamp, bytes) or len(expected_timestamp) != 8:
            raise ConflictError("Odśwież listę semestrów przed edycją.")
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "update", connection=connection)
            draft = self._validate(draft, self._schema(connection))
            acquire_write_lock(connection, "academic:organization-schedule")
            outside = query(
                connection,
                "SELECT TOP (1) cd.Id FROM org.SemesterCalendarDates cd "
                "JOIN org.SemesterCalendars cal ON cal.Id=cd.SemesterCalendarId "
                "WHERE cal.SemesterId=? AND cal.IsActive=1 AND cd.IsActive=1 "
                "AND (CONVERT(date,cd.[Date])<? OR CONVERT(date,cd.[Date])>?)",
                (id, draft.start_date, draft.end_date),
            )
            if outside:
                raise ValidationError(
                    "Daty semestru muszą obejmować wszystkie aktywne daty jego kalendarzy. "
                    "Najpierw popraw powiązane daty zjazdów."
                )
            row = one(
                connection,
                UPDATE_SQL,
                self._params(draft) + (id, expected_timestamp),
            )
            if row is None:
                raise ConflictError(
                    "Semestr został zmieniony przez inną osobę lub usunięty. "
                    "Odśwież listę i ponów edycję."
                )
            return _semester(row)

    @staticmethod
    def _params(draft: SemesterInput) -> tuple:
        return (
            draft.name, draft.academic_year,
            datetime.combine(draft.start_date, time.min),
            datetime.combine(draft.end_date, time.min), draft.is_actual, draft.is_active,
        )

    def organization_available(self, token: str, semester_id: str) -> bool:
        """Organizacja wymaga istniejącego aktywnego semestru."""
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            row = one(connection, "SELECT IsActive FROM [org].[Semesters] WHERE Id=?", (semester_id,))
            return row is not None and bool(row["IsActive"])
