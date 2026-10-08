"""Read-only import preview followed by one fully revalidated SQL transaction."""

from dataclasses import replace
from uuid import UUID

from academic.db import query
from academic.errors import ConflictError, ValidationError
from academic.records import acquire_write_lock, get_record, list_records, require_active_semester
from .models import ImportContext, ImportIssue, ImportMapping, ImportPreview
from .parser import parse_rows
from .reader import read_table
from .resolver import resolve_rows

INPUT_FIELDS = ("SemesterOrganizationId", "SemesterCalendarDateId", "DailyScheduleId", "TeacherId", "LectureRoomName", "IsActive")

ORGANIZATION_SQL = """
SELECT o.Id, o.TeacherId, o.LectureGroupId, o.SubjectsSemesterId,
       ss.SemesterId, ss.SemesterNumber, s.Id AS SubjectId, s.Name AS SubjectName,
       s.ProgramsOfStudyId AS ProgramId, s.StudiesId, s.SpecializationId,
       lg.Name AS LectureGroupName, lg.SemesterCalendarId, lg.DailyScheduleTypeId,
       CONCAT(p.FirstName1, ' ', p.LastName) AS TeacherName
FROM org.SemesterOrganizations o
JOIN org.SubjectsSemesters ss ON ss.Id=o.SubjectsSemesterId AND ss.IsActive=1
JOIN org.Subjects s ON s.Id=ss.SubjectId AND s.IsActive=1
JOIN org.LectureGroups lg ON lg.Id=o.LectureGroupId AND lg.IsActive=1 AND lg.SemesterId=ss.SemesterId
JOIN org.Teachers t ON t.Id=o.TeacherId
JOIN org.Persons p ON p.Id=t.PersonId AND p.IsActive=1
WHERE o.IsActive=1 AND ss.SemesterId=?
"""


class ImportService:
    def __init__(self, db, auth, schedule_service):
        self.db, self.auth, self.schedule_service = db, auth, schedule_service

    def can(self, token, action):
        return self.auth.can(token, "schedule_import", action)

    def options(self, token):
        with self.db.connection() as connection:
            self.auth.require(token, "schedule_import", "view", connection=connection)
            return {table: list_records(connection, table, active_only=True) for table in
                    ("Semesters", "ProgramsOfStudies", "Studies", "Specializations")}

    def _context(self, connection, context):
        if not isinstance(context, ImportContext):
            raise ValidationError("Wybierz kontekst importowanego semestru.")
        for value in (context.semester_id, context.program_id, context.studies_id, context.specialization_id):
            if value is not None:
                try:
                    UUID(value)
                except (ValueError, TypeError, AttributeError):
                    raise ValidationError("Kontekst importu zawiera nieprawidłowe ID.") from None
        require_active_semester(connection, context.semester_id)
        for table, value in (("ProgramsOfStudies", context.program_id), ("Studies", context.studies_id),
                             ("Specializations", context.specialization_id)):
            if value:
                row = get_record(connection, table, value)
                if not row or not row.get("IsActive"):
                    raise ValidationError("Kontekst importu wskazuje nieistniejący lub nieaktywny rekord.")
                if table == "Specializations" and context.studies_id and str(row.get("StudiesId")).lower() != context.studies_id.lower():
                    raise ValidationError("Wybrana specjalizacja nie należy do wybranych studiów.")
        if context.semester_number is not None and (type(context.semester_number) is not int or context.semester_number < 1):
            raise ValidationError("Numer semestru studiów musi być dodatnią liczbą całkowitą.")

    def _snapshot(self, connection, context):
        self._context(connection, context)
        return {
            "organizations": query(connection, ORGANIZATION_SQL, (context.semester_id,)),
            "dates": query(connection, "SELECT d.* FROM org.SemesterCalendarDates d "
                           "JOIN org.SemesterCalendars c ON c.Id=d.SemesterCalendarId "
                           "WHERE d.IsActive=1 AND c.IsActive=1 AND c.SemesterId=?", (context.semester_id,)),
            "slots": query(connection, "SELECT s.* FROM org.DailySchedules s "
                           "JOIN org.DailyScheduleTypes t ON t.Id=s.DailyScheduleTypeId "
                           "WHERE s.IsActive=1 AND t.IsActive=1"),
            "groups": list_records(connection, "Groups", {"SemesterId": context.semester_id}, active_only=True),
            "group_links": query(connection, "SELECT e.* FROM org.LectureGroupsElements e "
                                 "JOIN org.LectureGroups lg ON lg.Id=e.LectureGroupId "
                                 "JOIN org.Groups g ON g.Id=e.GroupId "
                                 "WHERE e.IsActive=1 AND lg.IsActive=1 AND g.IsActive=1 "
                                 "AND lg.SemesterId=? AND g.SemesterId=lg.SemesterId", (context.semester_id,)),
            "teachers": query(connection, "SELECT t.Id,t.ShortName,CONCAT(p.FirstName1,' ',p.LastName) AS Name "
                              "FROM org.Teachers t JOIN org.Persons p ON p.Id=t.PersonId WHERE p.IsActive=1"),
        }

    @staticmethod
    def _inputs(preview):
        return [{field: row[field] for field in INPUT_FIELDS} for row in preview.entries]

    def preview(self, token, content: bytes, filename: str, *, context: ImportContext,
                mapping: ImportMapping, reader_options: dict | None = None) -> ImportPreview:
        with self.db.connection() as connection:
            self.auth.require(token, "schedule_import", "view", connection=connection)
            table = read_table(content, filename, **(reader_options or {}))
            rows, issues = parse_rows(table, mapping)
            result = resolve_rows(rows, context, self._snapshot(connection, context), issues, len(table.rows))
            if result.ready:
                try:
                    self.schedule_service.validate_many(token, self._inputs(result), connection=connection)
                except (ValidationError, ConflictError) as error:
                    result = replace(result, issues=(ImportIssue(0, str(error)),))
            return replace(result, source_table=table, mapping=mapping, context=context)

    def commit(self, token, preview: ImportPreview) -> list[dict]:
        if not isinstance(preview, ImportPreview) or not preview.ready or preview.source_table is None or preview.mapping is None or preview.context is None:
            raise ValidationError("Najpierw przygotuj poprawny podgląd importu bez błędów.")
        with self.db.connection() as connection:
            self.auth.require(token, "schedule_import", "create", connection=connection)
            acquire_write_lock(connection, "academic:organization-schedule")
            rows, issues = parse_rows(preview.source_table, preview.mapping)
            current = resolve_rows(rows, preview.context, self._snapshot(connection, preview.context), issues, preview.source_rows)
            if not current.ready:
                raise ConflictError("Dane importu przestały być poprawne. Przygotuj podgląd ponownie.")
            if current.entries != preview.entries:
                raise ConflictError("Dane w bazie zmieniły się od podglądu. Przygotuj go ponownie przed importem.")
            return self.schedule_service.create_many(token, self._inputs(current), connection=connection)
