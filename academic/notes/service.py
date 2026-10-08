"""Polimorficzne Notes z jawnym wykazem obiektów i kontrolą ich dostępu."""

from academic.auth.models import AuthenticationError
from academic.catalogs.service import CatalogService
from academic.catalogs.specs import CATALOGS
from academic.db import one
from academic.errors import ConflictError, ValidationError
from academic.records import acquire_write_lock, get_record, insert_record, list_records, record_id, update_record, validate_values


NOTE_TARGETS = {
    "org." + spec.table: {"table": spec.table, "title": spec.title, "item_code": spec.item_code}
    for spec in CATALOGS.values()
}
NOTE_TARGETS.update({
    "org.Semesters": {"table": "Semesters", "title": "Semestry", "item_code": "semesters"},
    "org.SemesterOrganizations": {"table": "SemesterOrganizations", "title": "Organizacja semestru", "item_code": "organization"},
    "org.SemesterOrganizationSchedules": {"table": "SemesterOrganizationSchedules", "title": "Harmonogram semestru", "item_code": "schedule"},
})
EDITABLE = ("Table", "TableId", "Comment", "Pin", "IsActive")
REQUIRED = EDITABLE


class NotesService:
    item_code = "dictionaries"

    def __init__(self, db, auth):
        self.db, self.auth = db, auth
        self.catalogs = CatalogService(db, auth)

    def can(self, token, action):
        return self.auth.can(token, self.item_code, action)

    @staticmethod
    def _target(table):
        if table not in NOTE_TARGETS:
            raise ValidationError("Wybierz obiekt notatki z dostępnej listy.")
        return NOTE_TARGETS[table]

    def _target_record(self, token, connection, table, target_id):
        target = self._target(table)
        item_code = self.db.settings.semesters_item_code if target["table"] == "Semesters" else target["item_code"]
        self.auth.require(token, item_code, "view", connection=connection)
        row = get_record(connection, target["table"], record_id(target_id), lock=True)
        if row is None:
            raise ValidationError("Obiekt notatki już nie istnieje. Wybierz rekord z listy.")
        return row

    def targets(self, token):
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            result = []
            grants = {}
            for name, target in NOTE_TARGETS.items():
                item_code = self.db.settings.semesters_item_code if target["table"] == "Semesters" else target["item_code"]
                if item_code not in grants:
                    grants[item_code] = self.auth.can(token, item_code, "view", connection=connection)
                if grants[item_code]:
                    result.append({"Table": name, "title": target["title"]})
            return sorted(result, key=lambda row: row["title"].casefold())

    def _records(self, connection, table):
        target = self._target(table)
        rows = list_records(connection, target["table"])
        if target["table"] in {spec.table for spec in CATALOGS.values()} or target["table"] == "Semesters":
            return self.catalogs._decorate(connection, target["table"], rows)
        return [{**row, "_label": self._label(connection, target["table"], row)} for row in rows]

    def _label(self, connection, table, row):
        if table == "SemesterOrganizations":
            realization = get_record(connection, "SubjectsSemesters", row.get("SubjectsSemesterId")) if row.get("SubjectsSemesterId") else None
            subject = get_record(connection, "Subjects", realization["SubjectId"]) if realization and realization.get("SubjectId") else None
            lecture = get_record(connection, "LectureGroups", row.get("LectureGroupId")) if row.get("LectureGroupId") else None
            return " · ".join(str(value) for value in (subject.get("Name") if subject else None, lecture.get("Name") if lecture else None, str(row["Id"])[:8]) if value)
        if table == "SemesterOrganizationSchedules":
            assignment = get_record(connection, "SemesterOrganizations", row["SemesterOrganizationId"]) if row.get("SemesterOrganizationId") else None
            day = get_record(connection, "SemesterCalendarDates", row["SemesterCalendarDateId"]) if row.get("SemesterCalendarDateId") else None
            hour = get_record(connection, "DailySchedules", row["DailyScheduleId"]) if row.get("DailyScheduleId") else None
            return " · ".join(str(value) for value in (
                self._label(connection, "SemesterOrganizations", assignment) if assignment else None,
                day.get("Date") if day else None,
                (str(hour.get("StartHour")) + "–" + str(hour.get("EndHour"))) if hour else None,
                str(row["Id"])[:8],
            ) if value)
        return str(row["Id"])

    def records(self, token, table):
        target = self._target(table)
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            item_code = self.db.settings.semesters_item_code if target["table"] == "Semesters" else target["item_code"]
            self.auth.require(token, item_code, "view", connection=connection)
            return self._records(connection, table)

    def list(self, token):
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            notes = []
            labels = {}
            grants = {}
            for row in list_records(connection, "Notes"):
                table = row.get("Table")
                if table not in NOTE_TARGETS:
                    continue
                target = NOTE_TARGETS[table]
                item_code = self.db.settings.semesters_item_code if target["table"] == "Semesters" else target["item_code"]
                if item_code not in grants:
                    grants[item_code] = self.auth.can(token, item_code, "view", connection=connection)
                if not grants[item_code]:
                    continue
                if table not in labels:
                    labels[table] = {str(record["Id"]): record["_label"] for record in self._records(connection, table)}
                label = labels[table].get(str(row.get("TableId")))
                if label is not None:
                    notes.append({**row, "_target_title": NOTE_TARGETS[table]["title"], "_record_label": label})
            return sorted(notes, key=lambda row: (not row.get("Pin", False), str(row.get("CreatedAt", "")), str(row["Id"])))

    def _author(self, token, connection):
        user = self.auth.get_user(token)
        account = one(connection, "SELECT Login FROM [org].[Accounts] WHERE Id=? AND PersonId=? AND IsActive=1", (user.account_id, user.person_id))
        if not account or not account.get("Login"):
            raise AuthenticationError("Nie można ustalić autora notatki. Zaloguj się ponownie.")
        return account["Login"]

    def _values(self, token, connection, values):
        normalized = validate_values(self.db, connection, "Notes", values, EDITABLE, REQUIRED)
        self._target_record(token, connection, normalized["Table"], normalized["TableId"])
        return normalized

    def create(self, token, values):
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "create", connection=connection)
            acquire_write_lock(connection, "catalog:Notes")
            values = self._values(token, connection, values)
            author = self._author(token, connection)
            values = validate_values(self.db, connection, "Notes", {**values, "CreatedBy": author, "ModifiedBy": author}, EDITABLE + ("CreatedBy", "ModifiedBy"), REQUIRED)
            return insert_record(connection, "Notes", values)

    def update(self, token, note_id, values, expected_timestamp):
        note_id = record_id(note_id)
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "update", connection=connection)
            acquire_write_lock(connection, "catalog:Notes")
            current = get_record(connection, "Notes", note_id, lock=True)
            if current is None or current.get("Timestamp") != expected_timestamp:
                raise ConflictError("Notatka zmieniła się lub została usunięta. Odśwież listę.")
            self._target_record(token, connection, current["Table"], current["TableId"])
            values = self._values(token, connection, values)
            author = self._author(token, connection)
            clock = one(connection, "SELECT SYSDATETIME() AS ModifiedAt")
            values = validate_values(self.db, connection, "Notes", {**values, "ModifiedBy": author, "ModifiedAt": clock["ModifiedAt"]},
                EDITABLE + ("ModifiedBy", "ModifiedAt"), REQUIRED)
            return update_record(connection, "Notes", note_id, values, expected_timestamp)
