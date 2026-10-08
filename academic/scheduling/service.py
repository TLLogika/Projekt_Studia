"""Zapis pojedynczych godzin z kontrolą zależności, kolizji i limitu godzin."""

from __future__ import annotations

from contextlib import nullcontext
from dataclasses import replace
from datetime import datetime
from uuid import uuid4

from academic.db import query
from academic.errors import ConflictError, ValidationError
from academic.records import (acquire_write_lock, get_record, insert_record, list_records,
                              require_active_semester, update_record, validate_values)
from academic.organization.service import active_record, active_teacher, lecture_members, subject_matches_group
from .models import ScheduleInput, calculate_balance, clock_minutes, intervals_overlap, valid_id

TABLE = "SemesterOrganizationSchedules"
FIELDS = frozenset({"SemesterOrganizationId", "SemesterCalendarDateId", "DailyScheduleId",
                    "TeacherId", "LectureRoomName", "IsComplited", "IsCanceled", "IsActive", "IsMoved"})
SLOTS_SQL = """
SELECT h.*,o.SubjectsSemesterId,o.LectureGroupId,o.TeacherId AS OrganizationTeacherId,
       COALESCE(h.TeacherId,o.TeacherId) AS EffectiveTeacherId,
       ss.SemesterId,ss.NumberOfPlanningHours,s.Name AS SubjectName,
       lg.Name AS LectureGroupName,d.Date,ds.StartHour,ds.EndHour,
       CONCAT(p.FirstName1,' ',p.LastName) AS TeacherName,
       CONCAT(ep.FirstName1,' ',ep.LastName) AS AdditionalTeacherName
FROM org.SemesterOrganizationSchedules h
JOIN org.SemesterOrganizations o ON o.Id=h.SemesterOrganizationId
JOIN org.SubjectsSemesters ss ON ss.Id=o.SubjectsSemesterId
LEFT JOIN org.Subjects s ON s.Id=ss.SubjectId
LEFT JOIN org.LectureGroups lg ON lg.Id=o.LectureGroupId
LEFT JOIN org.SemesterCalendarDates d ON d.Id=h.SemesterCalendarDateId
LEFT JOIN org.DailySchedules ds ON ds.Id=h.DailyScheduleId
LEFT JOIN org.Teachers t ON t.Id=o.TeacherId
LEFT JOIN org.Persons p ON p.Id=t.PersonId
LEFT JOIN org.Teachers et ON et.Id=h.TeacherId
LEFT JOIN org.Persons ep ON ep.Id=et.PersonId
"""


def _date(value):
    return value.date() if isinstance(value, datetime) else value


class ScheduleService:
    item_code = "schedule"

    def __init__(self, db, auth):
        self.db, self.auth = db, auth

    def can(self, token, action: str) -> bool:
        return self.auth.can(token, self.item_code, action)

    def list(self, token, semester_id: str) -> list[dict]:
        semester_id = valid_id(semester_id, "Semestr")
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            return self._slots(connection, semester_id)

    @staticmethod
    def _slots(connection, semester_id=None) -> list[dict]:
        clause, params = (" WHERE ss.SemesterId=?", (semester_id,)) if semester_id else ("", ())
        return query(connection, SLOTS_SQL + clause + " ORDER BY d.Date,ds.StartHour,s.Name,h.Id", params)

    def lookups(self, token, semester_id=None, organization_id=None) -> dict[str, list[dict]]:
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            result = {"Semesters": list_records(connection, "Semesters")}
            if semester_id is None:
                return result
            semester_id = valid_id(semester_id, "Semestr")
            result["Organizations"] = query(connection,
                "SELECT o.*,s.Name AS SubjectName,lg.Name AS LectureGroupName,lg.SemesterCalendarId,lg.DailyScheduleTypeId "
                "FROM org.SemesterOrganizations o JOIN org.SubjectsSemesters ss ON ss.Id=o.SubjectsSemesterId "
                "JOIN org.Subjects s ON s.Id=ss.SubjectId JOIN org.LectureGroups lg ON lg.Id=o.LectureGroupId "
                "WHERE ss.SemesterId=? AND o.IsActive=1 AND ss.IsActive=1 AND s.IsActive=1 AND lg.IsActive=1 "
                "ORDER BY s.Name,lg.Name,o.Id", (semester_id,))
            result["Teachers"] = query(connection,
                "SELECT t.*,CONCAT(p.FirstName1,' ',p.LastName) AS TeacherName "
                "FROM org.Teachers t JOIN org.Persons p ON p.Id=t.PersonId "
                "WHERE p.IsActive=1 ORDER BY p.LastName,p.FirstName1,t.Id")
            if organization_id is not None:
                organization_id = valid_id(organization_id, "Organizacja semestru")
                organization = next((row for row in result["Organizations"] if str(row["Id"]) == organization_id), None)
                if organization:
                    result["Dates"] = list_records(connection, "SemesterCalendarDates",
                        {"SemesterCalendarId": organization.get("SemesterCalendarId")}, active_only=True)
                    result["Hours"] = list_records(connection, "DailySchedules",
                        {"DailyScheduleTypeId": organization.get("DailyScheduleTypeId")}, active_only=True)
            return result

    def _context(self, connection, draft: ScheduleInput) -> dict:
        org = active_record(connection, "SemesterOrganizations", draft.semester_organization_id, "Organizacja semestru")
        ss = active_record(connection, "SubjectsSemesters", org.get("SubjectsSemesterId"), "Przedmiot w semestrze")
        semester = require_active_semester(connection, ss.get("SemesterId"))
        subject = active_record(connection, "Subjects", ss.get("SubjectId"), "Przedmiot")
        lg = active_record(connection, "LectureGroups", org.get("LectureGroupId"), "Grupa wykładowa")
        if str(lg.get("SemesterId")) != str(semester["Id"]):
            raise ValidationError("Grupa wykładowa należy do innego semestru niż przedmiot.")
        active_teacher(connection, org.get("TeacherId"))
        teachers = {str(org["TeacherId"])}
        if draft.teacher_id:
            active_teacher(connection, draft.teacher_id)
            teachers.add(draft.teacher_id)
        members = lecture_members(connection, str(lg["Id"]))
        if not members:
            raise ValidationError("Grupa wykładowa nie ma przypisanych aktywnych grup.")
        for group in members:
            if str(group.get("SemesterId")) != str(semester["Id"]) or not subject_matches_group(subject, ss, group):
                raise ValidationError("Grupy wykładowe nie odpowiadają przedmiotowi w semestrze.")
        day = active_record(connection, "SemesterCalendarDates", draft.semester_calendar_date_id, "Data / zjazd")
        calendar = active_record(connection, "SemesterCalendars", day.get("SemesterCalendarId"), "Kalendarz")
        if str(calendar.get("SemesterId")) != str(semester["Id"]) or str(calendar["Id"]) != str(lg.get("SemesterCalendarId")):
            raise ValidationError("Wybrana data nie należy do kalendarza grupy wykładowej w tym semestrze.")
        if not (_date(semester["StartDate"]) <= _date(day["Date"]) <= _date(semester["EndDate"])):
            raise ValidationError("Data zajęć musi mieścić się w zakresie semestru.")
        hour = active_record(connection, "DailySchedules", draft.daily_schedule_id, "Godzina")
        active_record(connection, "DailyScheduleTypes", hour.get("DailyScheduleTypeId"), "Typ planu dziennego")
        if str(hour.get("DailyScheduleTypeId")) != str(lg.get("DailyScheduleTypeId")):
            raise ValidationError("Godzina nie należy do typu planu dziennego grupy wykładowej.")
        start, end = clock_minutes(hour.get("StartHour")), clock_minutes(hour.get("EndHour"))
        if end <= start:
            raise ValidationError("Koniec godziny zajęć musi być późniejszy niż początek.")
        if end - start != 45:
            raise ValidationError("Każdy rekord harmonogramu musi obejmować jedną 45-minutową godzinę zajęć. Uzupełnij pojedyncze godziny w planie dziennym.")
        return {"organization": org, "subject_semester": ss, "lecture_group": lg,
                "date": day, "hour": hour, "teacher_ids": teachers,
                "group_ids": {str(group["Id"]) for group in members}}

    @staticmethod
    def _values(draft: ScheduleInput, *, is_moved=False) -> dict:
        return {"SemesterOrganizationId": draft.semester_organization_id,
                "SemesterCalendarDateId": draft.semester_calendar_date_id,
                "DailyScheduleId": draft.daily_schedule_id, "TeacherId": draft.teacher_id,
                "LectureRoomName": draft.lecture_room_name or None,
                "IsComplited": draft.is_completed, "IsCanceled": draft.is_canceled,
                "IsActive": draft.is_active, "IsMoved": is_moved}

    def _validate_slot(self, connection, draft, exclude_id=None, *, is_moved=False, pending=()) -> dict:
        draft = (draft if isinstance(draft, ScheduleInput) else ScheduleInput.from_mapping(draft)).validated()
        values = validate_values(self.db, connection, TABLE, self._values(draft, is_moved=is_moved), FIELDS,
            required_fields=("SemesterOrganizationId", "SemesterCalendarDateId", "DailyScheduleId",
                             "IsComplited", "IsCanceled", "IsActive", "IsMoved"))
        context = self._context(connection, draft)
        slots = self._slots(connection) + list(pending)
        group_members = {}
        planned = 0
        for other in slots:
            if str(other["Id"]) == str(exclude_id) or not other.get("IsActive") or other.get("IsCanceled"):
                continue
            if (str(other["SubjectsSemesterId"]) == str(context["subject_semester"]["Id"])
                    and str(other["LectureGroupId"]) == str(context["lecture_group"]["Id"])):
                planned += 1
            if not draft.is_active or draft.is_canceled or _date(other.get("Date")) != _date(context["date"]["Date"]):
                continue
            if not intervals_overlap(context["hour"]["StartHour"], context["hour"]["EndHour"], other["StartHour"], other["EndHour"]):
                continue
            other_teachers = {str(other["OrganizationTeacherId"])}
            if other.get("TeacherId"):
                other_teachers.add(str(other["TeacherId"]))
            if context["teacher_ids"] & other_teachers:
                raise ConflictError("Wybrany wykładowca ma już zajęcia w tym terminie.")
            room = (draft.lecture_room_name or "").strip().casefold()
            if room and room == (other.get("LectureRoomName") or "").strip().casefold():
                raise ConflictError("Wybrana sala jest już zajęta w tym terminie.")
            other_lg = str(other["LectureGroupId"])
            if other_lg not in group_members:
                group_members[other_lg] = {str(row["Id"]) for row in lecture_members(connection, other_lg)}
            if other_lg == str(context["lecture_group"]["Id"]) or context["group_ids"] & group_members[other_lg]:
                raise ConflictError("Grupa ma już zajęcia w tym terminie, również w innej grupie wykładowej.")
        required = int(context["subject_semester"]["NumberOfPlanningHours"])
        if draft.is_active and not draft.is_canceled and planned + 1 > required:
            raise ConflictError(f"Przekroczono wymaganą liczbę godzin ({required}) dla przedmiotu i grupy wykładowej.")
        return values

    def validate_many(self, token, drafts: list[ScheduleInput | dict], connection=None) -> None:
        """Podgląd importu sprawdza kolizje także pomiędzy nowymi godzinami bez zapisu."""
        if not drafts or len(drafts) > 5000:
            raise ValidationError("Podgląd wymaga od 1 do 5000 godzin.")
        with (nullcontext(connection) if connection is not None else self.db.connection()) as conn:
            self.auth.require(token, self.item_code, "create", connection=conn)
            pending = []
            for draft in drafts:
                values = self._validate_slot(conn, draft, pending=pending)
                context = self._context(conn, ScheduleInput.from_mapping(values).validated())
                pending.append({**values, "Id": str(uuid4()),
                    "SubjectsSemesterId": context["subject_semester"]["Id"],
                    "LectureGroupId": context["lecture_group"]["Id"],
                    "OrganizationTeacherId": context["organization"]["TeacherId"],
                    "Date": context["date"]["Date"], "StartHour": context["hour"]["StartHour"],
                    "EndHour": context["hour"]["EndHour"]})

    def create_many(self, token, drafts: list[ScheduleInput | dict], connection=None) -> list[dict]:
        if not drafts:
            raise ValidationError("Brak godzin do zapisania.")
        if len(drafts) > 5000:
            raise ValidationError("Jednorazowo można zapisać najwyżej 5000 godzin.")
        with (nullcontext(connection) if connection is not None else self.db.connection()) as conn:
            self.auth.require(token, self.item_code, "create", connection=conn)
            acquire_write_lock(conn, "academic:organization-schedule")
            saved = []
            for draft in drafts:
                values = self._validate_slot(conn, draft)
                saved.append(insert_record(conn, TABLE, values))
            return saved

    def create(self, token, draft: ScheduleInput | dict) -> dict:
        return self.create_many(token, [draft])[0]

    def update(self, token, record_id, draft: ScheduleInput | dict, expected_timestamp: bytes) -> dict:
        record_id = valid_id(record_id, "Godzina harmonogramu")
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "update", connection=connection)
            acquire_write_lock(connection, "academic:organization-schedule")
            old = get_record(connection, TABLE, record_id, lock=True)
            if old is None:
                raise ConflictError("Godzina została usunięta. Odśwież harmonogram.")
            draft = (draft if isinstance(draft, ScheduleInput) else ScheduleInput.from_mapping(draft)).validated()
            old_org = active_record(connection, "SemesterOrganizations", old["SemesterOrganizationId"], "Organizacja godziny")
            old_ss = active_record(connection, "SubjectsSemesters", old_org["SubjectsSemesterId"], "Przedmiot godziny")
            require_active_semester(connection, old_ss["SemesterId"])
            target_org = active_record(connection, "SemesterOrganizations", draft.semester_organization_id, "Organizacja semestru")
            target_ss = active_record(connection, "SubjectsSemesters", target_org["SubjectsSemesterId"], "Przedmiot w semestrze")
            if str(old_ss["SemesterId"]) != str(target_ss["SemesterId"]):
                raise ValidationError("Nie można przenosić godziny do innego semestru.")
            moved = bool(old.get("IsMoved")) or any(str(old[name]) != str(value) for name, value in (
                ("SemesterCalendarDateId", draft.semester_calendar_date_id), ("DailyScheduleId", draft.daily_schedule_id)))
            values = self._validate_slot(connection, draft, record_id, is_moved=moved)
            return update_record(connection, TABLE, record_id, values, expected_timestamp)

    def _change_status(self, token, record_id, expected_timestamp, *, completed=None, canceled=None):
        record_id = valid_id(record_id, "Godzina harmonogramu")
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "update", connection=connection)
            acquire_write_lock(connection, "academic:organization-schedule")
            old = get_record(connection, TABLE, record_id, lock=True)
            if old is None:
                raise ConflictError("Godzina została usunięta. Odśwież harmonogram.")
            draft = ScheduleInput.from_mapping(old)
            if canceled is True and draft.is_completed:
                raise ValidationError("Nie można odwołać wykonanej godziny. Najpierw popraw jej stan wykonania.")
            if completed is True and (draft.is_canceled or not draft.is_active):
                raise ValidationError("Nie można oznaczyć odwołanej lub nieaktywnej godziny jako wykonanej.")
            draft = replace(draft, is_completed=draft.is_completed if completed is None else completed,
                            is_canceled=draft.is_canceled if canceled is None else canceled)
            values = self._validate_slot(connection, draft, record_id, is_moved=bool(old.get("IsMoved")))
            return update_record(connection, TABLE, record_id, values, expected_timestamp)

    def cancel(self, token, record_id, expected_timestamp):
        return self._change_status(token, record_id, expected_timestamp, canceled=True)

    def complete(self, token, record_id, expected_timestamp, *, completed=True):
        return self._change_status(token, record_id, expected_timestamp, completed=completed)

    def make_up(self, token, record_id, draft: ScheduleInput | dict, expected_timestamp):
        record_id = valid_id(record_id, "Odwołana godzina")
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "update", connection=connection)
            acquire_write_lock(connection, "academic:organization-schedule")
            old = get_record(connection, TABLE, record_id, lock=True)
            if old is None or not old.get("IsCanceled"):
                raise ConflictError("Odróbkę można wyznaczyć tylko dla odwołanej godziny. Odśwież harmonogram.")
            draft = draft if isinstance(draft, ScheduleInput) else ScheduleInput.from_mapping(draft)
            if str(draft.semester_organization_id) != str(old["SemesterOrganizationId"]):
                raise ValidationError("Odróbka musi zachować przydział przedmiotu i grupy.")
            draft = replace(draft, is_active=True, is_canceled=False, is_completed=False)
            if (str(draft.semester_calendar_date_id) == str(old["SemesterCalendarDateId"])
                    and str(draft.daily_schedule_id) == str(old["DailyScheduleId"])):
                raise ValidationError("Odróbka wymaga nowego terminu lub godziny.")
            values = self._validate_slot(connection, draft, record_id, is_moved=True)
            return update_record(connection, TABLE, record_id, values, expected_timestamp)

    def balance(self, token, semester_id: str) -> list[dict]:
        semester_id = valid_id(semester_id, "Semestr")
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            assignments = query(connection,
                "SELECT o.SubjectsSemesterId,o.LectureGroupId,ss.NumberOfPlanningHours,s.Name AS SubjectName,lg.Name AS LectureGroupName "
                "FROM org.SemesterOrganizations o JOIN org.SubjectsSemesters ss ON ss.Id=o.SubjectsSemesterId "
                "JOIN org.Subjects s ON s.Id=ss.SubjectId LEFT JOIN org.LectureGroups lg ON lg.Id=o.LectureGroupId "
                "WHERE ss.SemesterId=? AND ss.IsActive=1 AND o.IsActive=1 "
                "GROUP BY o.SubjectsSemesterId,o.LectureGroupId,ss.NumberOfPlanningHours,s.Name,lg.Name "
                "ORDER BY s.Name,lg.Name", (semester_id,))
            slots = self._slots(connection, semester_id)
            output = []
            covered = set()
            for assignment in assignments:
                subject_id, group_id = str(assignment["SubjectsSemesterId"]), str(assignment["LectureGroupId"])
                rows = [row for row in slots if str(row["SubjectsSemesterId"]) == subject_id and str(row["LectureGroupId"]) == group_id]
                output.append({**assignment, **calculate_balance(assignment["NumberOfPlanningHours"], rows)})
                covered.add(subject_id)
            for subject in query(connection,
                "SELECT ss.Id AS SubjectsSemesterId,ss.NumberOfPlanningHours,s.Name AS SubjectName "
                "FROM org.SubjectsSemesters ss JOIN org.Subjects s ON s.Id=ss.SubjectId "
                "WHERE ss.SemesterId=? AND ss.IsActive=1 ORDER BY s.Name", (semester_id,)):
                if str(subject["SubjectsSemesterId"]) not in covered:
                    output.append({**subject, "LectureGroupId": None, "LectureGroupName": "Brak przydziału grupy",
                                   **calculate_balance(subject["NumberOfPlanningHours"], [])})
            return output
