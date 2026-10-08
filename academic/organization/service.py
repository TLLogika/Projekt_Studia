"""Organizacja istniejącego aktywnego semestru; żadnych zmian schematu."""

from __future__ import annotations

from datetime import datetime

from academic.db import one, query
from academic.errors import ConflictError, ValidationError
from academic.records import (acquire_write_lock, get_record, insert_record, list_records,
                              require_active_semester, update_record, validate_values)
from academic.scheduling.models import valid_id
from .models import ReadinessReport

FIELDS = frozenset({"SubjectsSemesterId", "LectureGroupId", "TeacherId", "IsActive"})


def active_record(connection, table: str, record_id, label: str) -> dict:
    row = get_record(connection, table, valid_id(record_id, label))
    if row is None or ("IsActive" in row and not row["IsActive"]):
        raise ValidationError(f"{label}: rekord nie istnieje lub jest nieaktywny.")
    return row


def active_teacher(connection, teacher_id) -> dict:
    teacher = active_record(connection, "Teachers", teacher_id, "Wykładowca")
    active_record(connection, "Persons", teacher["PersonId"], "Osoba wykładowcy")
    return teacher


def lecture_members(connection, lecture_group_id: str) -> list[dict]:
    return query(connection,
        "SELECT g.* FROM org.LectureGroupsElements e JOIN org.Groups g ON g.Id=e.GroupId "
        "WHERE e.LectureGroupId=? AND e.IsActive=1 AND g.IsActive=1 ORDER BY g.Name,g.Id",
        (lecture_group_id,))


def subject_matches_group(subject: dict, subject_semester: dict, group: dict) -> bool:
    number = int(subject_semester.get("SemesterNumber") or subject.get("Semester") or 0)
    return (str(subject.get("StudiesId")) == str(group.get("StudiesId"))
            and int(group.get("SemesterNumber") or 0) == number
            and (subject.get("SpecializationId") is None
                 or str(subject["SpecializationId"]) == str(group.get("SpecializationId"))))


class OrganizationService:
    item_code = "organization"

    def __init__(self, db, auth):
        self.db, self.auth = db, auth

    def can(self, token, action: str) -> bool:
        return self.auth.can(token, self.item_code, action)

    def list(self, token, semester_id: str) -> list[dict]:
        semester_id = valid_id(semester_id, "Semestr")
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            return self._list(connection, semester_id)

    @staticmethod
    def _list(connection, semester_id) -> list[dict]:
        return query(connection,
            "SELECT o.*,s.Name AS SubjectName,lg.Name AS LectureGroupName, "
            "CONCAT(p.FirstName1,' ',p.LastName) AS TeacherName "
            "FROM org.SemesterOrganizations o "
            "JOIN org.SubjectsSemesters ss ON ss.Id=o.SubjectsSemesterId "
            "LEFT JOIN org.Subjects s ON s.Id=ss.SubjectId "
            "LEFT JOIN org.LectureGroups lg ON lg.Id=o.LectureGroupId "
            "LEFT JOIN org.Teachers t ON t.Id=o.TeacherId "
            "LEFT JOIN org.Persons p ON p.Id=t.PersonId "
            "WHERE ss.SemesterId=? ORDER BY s.Name,lg.Name,o.Id", (semester_id,))

    def lookups(self, token, semester_id=None) -> dict[str, list[dict]]:
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            result = {"Semesters": list_records(connection, "Semesters")}
            if semester_id is None:
                return result
            semester_id = valid_id(semester_id, "Semestr")
            result["SubjectsSemesters"] = query(connection,
                "SELECT ss.*,s.Name AS SubjectName FROM org.SubjectsSemesters ss "
                "JOIN org.Subjects s ON s.Id=ss.SubjectId "
                "WHERE ss.SemesterId=? AND ss.IsActive=1 AND s.IsActive=1 ORDER BY s.Name,ss.Id",
                (semester_id,))
            result["LectureGroups"] = list_records(connection, "LectureGroups",
                filters={"SemesterId": semester_id}, active_only=True)
            result["Teachers"] = query(connection,
                "SELECT t.*,CONCAT(p.FirstName1,' ',p.LastName) AS TeacherName "
                "FROM org.Teachers t JOIN org.Persons p ON p.Id=t.PersonId "
                "WHERE p.IsActive=1 ORDER BY p.LastName,p.FirstName1,t.Id")
            return result

    def _validate(self, connection, values: dict, existing_id=None) -> dict:
        values = validate_values(self.db, connection, "SemesterOrganizations", values, FIELDS,
                                 required_fields=("SubjectsSemesterId", "LectureGroupId", "TeacherId", "IsActive"))
        for name in ("SubjectsSemesterId", "LectureGroupId", "TeacherId"):
            values[name] = valid_id(values.get(name), name)
        ss = active_record(connection, "SubjectsSemesters", values["SubjectsSemesterId"], "Przedmiot w semestrze")
        require_active_semester(connection, ss["SemesterId"])
        subject = active_record(connection, "Subjects", ss["SubjectId"], "Przedmiot")
        lg = active_record(connection, "LectureGroups", values["LectureGroupId"], "Grupa wykładowa")
        active_teacher(connection, values["TeacherId"])
        if str(lg.get("SemesterId")) != str(ss["SemesterId"]):
            raise ValidationError("Przedmiot i grupa wykładowa muszą należeć do tego samego semestru.")
        members = lecture_members(connection, str(lg["Id"]))
        if not members:
            raise ValidationError("Grupa wykładowa nie ma przypisanych aktywnych grup. Uzupełnij skład grupy.")
        for group in members:
            if str(group.get("SemesterId")) != str(ss["SemesterId"]) or not subject_matches_group(subject, ss, group):
                raise ValidationError(f"Grupa {group['Name']} nie odpowiada studiom, numerowi semestru lub specjalizacji przedmiotu.")
        duplicate = one(connection,
            "SELECT Id FROM org.SemesterOrganizations WHERE SubjectsSemesterId=? "
            "AND LectureGroupId=? AND TeacherId=? AND IsActive=1 AND (? IS NULL OR Id<>?)",
            (values["SubjectsSemesterId"], values["LectureGroupId"], values["TeacherId"], existing_id, existing_id))
        if values["IsActive"] and duplicate:
            raise ConflictError("Taki aktywny przydział przedmiotu, grupy i wykładowcy już istnieje.")
        return values

    def create(self, token, values: dict) -> dict:
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "create", connection=connection)
            acquire_write_lock(connection, "academic:organization-schedule")
            values = self._validate(connection, values)
            return insert_record(connection, "SemesterOrganizations", values)

    def update(self, token, record_id, values: dict, expected_timestamp: bytes) -> dict:
        record_id = valid_id(record_id, "Organizacja semestru")
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "update", connection=connection)
            acquire_write_lock(connection, "academic:organization-schedule")
            old = get_record(connection, "SemesterOrganizations", record_id, lock=True)
            if old is None:
                raise ConflictError("Przydział został usunięty. Odśwież listę.")
            old_ss = get_record(connection, "SubjectsSemesters", old.get("SubjectsSemesterId"))
            if old_ss:
                require_active_semester(connection, old_ss["SemesterId"])
            values = self._validate(connection, values, record_id)
            changed = any(str(old.get(name)) != str(values.get(name)) for name in FIELDS)
            if changed and one(connection,
                "SELECT TOP (1) Id FROM org.SemesterOrganizationSchedules "
                "WHERE SemesterOrganizationId=? AND IsActive=1", (record_id,)):
                raise ConflictError("Przydział ma aktywne godziny w harmonogramie. Najpierw zmień lub dezaktywuj ich przypisanie.")
            return update_record(connection, "SemesterOrganizations", record_id, values, expected_timestamp)

    def readiness(self, token, semester_id: str) -> ReadinessReport:
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "view", connection=connection)
            return self._readiness(connection, valid_id(semester_id, "Semestr"))

    def finalize(self, token, semester_id: str) -> ReadinessReport:
        """Zakończenie oznacza kontrolę kompletności, bez wymyślonego stanu w bazie."""
        with self.db.connection() as connection:
            self.auth.require(token, self.item_code, "update", connection=connection)
            acquire_write_lock(connection, "academic:organization-schedule")
            report = self._readiness(connection, valid_id(semester_id, "Semestr"))
            if not report.ready:
                raise ValidationError("Nie można zakończyć organizacji: " + " ".join(report.issues))
            return report

    def _readiness(self, connection, semester_id: str) -> ReadinessReport:
        issues = []
        semester = get_record(connection, "Semesters", semester_id)
        if semester is None or not semester.get("IsActive"):
            issues.append("Semestr nie istnieje lub nie jest aktywny.")
        subjects = list_records(connection, "SubjectsSemesters", {"SemesterId": semester_id}, active_only=True)
        groups = list_records(connection, "Groups", {"SemesterId": semester_id}, active_only=True)
        organizations = [row for row in self._list(connection, semester_id) if row["IsActive"]]
        if not subjects:
            issues.append("Brak aktywnych przedmiotów w semestrze.")
        if not groups:
            issues.append("Brak aktywnych grup w semestrze.")
        coverage = set()
        for organization in organizations:
            try:
                self._validate(connection, {field: organization.get(field) for field in FIELDS}, str(organization["Id"]))
                lg = active_record(connection, "LectureGroups", organization["LectureGroupId"], "Grupa wykładowa")
                active_record(connection, "LectureGroupTypes", lg.get("LectureGroupTypeId"), "Typ grupy wykładowej")
                calendar = active_record(connection, "SemesterCalendars", lg.get("SemesterCalendarId"), "Kalendarz grupy")
                if str(calendar.get("SemesterId")) != semester_id:
                    raise ValidationError("Kalendarz grupy jest przypisany do innego semestru.")
                active_record(connection, "DailyScheduleTypes", lg.get("DailyScheduleTypeId"), "Typ planu dziennego")
                dates = list_records(connection, "SemesterCalendarDates", {"SemesterCalendarId": calendar["Id"]}, active_only=True)
                if not dates:
                    raise ValidationError("Kalendarz grupy nie ma dat / zjazdów.")
                if semester is not None:
                    def day(value):
                        return value.date() if isinstance(value, datetime) else value
                    if any(not (day(semester["StartDate"]) <= day(row["Date"]) <= day(semester["EndDate"])) for row in dates):
                        raise ValidationError("Kalendarz grupy zawiera daty poza zakresem semestru.")
                if not list_records(connection, "DailySchedules", {"DailyScheduleTypeId": lg["DailyScheduleTypeId"]}, active_only=True):
                    raise ValidationError("Typ planu dziennego nie ma godzin.")
                for group in lecture_members(connection, organization["LectureGroupId"]):
                    coverage.add((str(organization["SubjectsSemesterId"]), str(group["Id"])))
            except (ValidationError, ConflictError) as error:
                issues.append(f"Przydział {organization.get('SubjectName') or organization['Id']}: {error}")
        used_groups = set()
        for ss in subjects:
            subject = get_record(connection, "Subjects", ss["SubjectId"]) if ss.get("SubjectId") else None
            if subject is None or not subject.get("IsActive"):
                issues.append(f"Realizacja {ss['Id']} nie ma aktywnego przedmiotu.")
                continue
            if int(ss.get("SemesterNumber") or 0) != int(subject.get("Semester") or 0) or int(ss.get("SemesterNumber") or 0) < 1:
                issues.append(f"Przedmiot {subject['Name']} ma numer semestru realizacji niezgodny z programem.")
            for table, field, label in (("ProgramsOfStudies", "ProgramsOfStudyId", "programu nauczania"),
                                        ("Studies", "StudiesId", "studiów"),
                                        ("SubjectTypes", "SubjectTypeId", "typu przedmiotu")):
                related = get_record(connection, table, subject.get(field)) if subject.get(field) else None
                if related is None or not related.get("IsActive", True):
                    issues.append(f"Przedmiot {subject['Name']} nie ma aktywnego {label}.")
                elif table == "ProgramsOfStudies" and semester is not None:
                    program_start = related.get("StartDate")
                    program_day = program_start.date() if isinstance(program_start, datetime) else program_start
                    semester_start = semester["StartDate"]
                    semester_day = semester_start.date() if isinstance(semester_start, datetime) else semester_start
                    if program_day is None or program_day > semester_day:
                        issues.append(f"Program przedmiotu {subject['Name']} nie obowiązuje na początku semestru.")
                elif table == "Studies" and related.get("NumberOfSemesters"):
                    if int(ss.get("SemesterNumber") or 0) > int(related["NumberOfSemesters"]):
                        issues.append(f"Przedmiot {subject['Name']} przekracza liczbę semestrów swoich studiów.")
            if subject.get("SpecializationId"):
                specialization = get_record(connection, "Specializations", subject["SpecializationId"])
                if (specialization is None or not specialization.get("IsActive")
                        or str(specialization.get("StudiesId")) != str(subject.get("StudiesId"))):
                    issues.append(f"Przedmiot {subject['Name']} nie ma aktywnej specjalizacji należącej do swoich studiów.")
            matching = [group for group in groups if subject_matches_group(subject, ss, group)]
            if not matching:
                issues.append(f"Przedmiot {subject['Name']} nie ma pasującej grupy.")
            for group in matching:
                used_groups.add(str(group["Id"]))
                if (str(ss["Id"]), str(group["Id"])) not in coverage:
                    issues.append(f"Brak przydziału przedmiotu {subject['Name']} dla grupy {group['Name']} z wykładowcą i kalendarzem.")
        for group in groups:
            studies = get_record(connection, "Studies", group["StudiesId"]) if group.get("StudiesId") else None
            if studies is None or not studies.get("IsActive"):
                issues.append(f"Grupa {group['Name']} nie ma aktywnych studiów.")
            elif int(group.get("SemesterNumber") or 0) < 1 or (studies.get("NumberOfSemesters")
                    and int(group.get("SemesterNumber") or 0) > int(studies["NumberOfSemesters"])):
                issues.append(f"Grupa {group['Name']} ma numer semestru poza zakresem swoich studiów.")
            if group.get("SpecializationId"):
                specialization = get_record(connection, "Specializations", group["SpecializationId"])
                if (specialization is None or not specialization.get("IsActive")
                        or str(specialization.get("StudiesId")) != str(group.get("StudiesId"))):
                    issues.append(f"Grupa {group['Name']} nie ma aktywnej specjalizacji należącej do swoich studiów.")
            if str(group["Id"]) not in used_groups:
                issues.append(f"Grupa {group['Name']} nie ma przedmiotów odpowiadających studiom i numerowi semestru.")
        return ReadinessReport(semester_id, tuple(dict.fromkeys(issues)), len(subjects), len(groups), len(organizations))
