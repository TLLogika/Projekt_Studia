"""Transakcyjne katalogi: rzeczywiste FK, uprawnienia i wersjonowanie rekordów."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time
from decimal import Decimal
from uuid import UUID

from academic.catalogs.specs import CATALOGS, TABLE_SPECS, CatalogSpec
from academic.errors import ConflictError, ValidationError
from academic.records import (
    acquire_write_lock, get_record, insert_record, list_records,
    require_active_semester, update_record, validate_values,
)


def _day(value):
    return value.date() if isinstance(value, datetime) else value


def _id(value):
    try:
        return str(UUID(str(value)))
    except (ValueError, TypeError, AttributeError):
        raise ValidationError("Nieprawidłowy identyfikator rekordu.") from None


def _title(table: str, row: dict, related: dict | None = None) -> str:
    related = related or {}
    columns = TABLE_SPECS[table].label_fields if table in TABLE_SPECS else ("Name", "AcademicYearName")
    parts = []
    for field in columns:
        value = related.get(field, row.get(field))
        if value is not None and str(value).strip():
            value = _day(value).strftime("%d.%m.%Y") if isinstance(value, (date, datetime)) else str(value)
            if value not in parts:
                parts.append(value)
    if table == "Subjects":
        for key in ("ProgramsOfStudyId", "StudiesId", "SubjectTypeId"):
            if related.get(key):
                parts.append(related[key])
        if row.get("Semester") is not None:
            parts.append(f"semestr {row['Semester']}")
    return " · ".join(parts) or str(row["Id"])


@dataclass(frozen=True)
class GenerationResult:
    created: tuple[dict, ...]
    already_existing: int


class CatalogService:
    def __init__(self, db, auth):
        self.db = db
        self.auth = auth

    @staticmethod
    def _spec(key: str) -> CatalogSpec:
        if key not in CATALOGS:
            raise ValidationError("Nieznany katalog danych.")
        return CATALOGS[key]

    def can(self, token: str, entity_key: str, action: str) -> bool:
        return self.auth.can(token, self._spec(entity_key).item_code, action)

    def _decorate(self, connection, table: str, rows: list[dict], depth=0, cache=None) -> list[dict]:
        if not rows:
            return []
        cache = cache if cache is not None else {}
        spec = TABLE_SPECS.get(table)
        reference_maps = {}
        reference_active = {}
        reference_rows = {}
        if spec and depth < 3:
            for field in spec.fields:
                if field.reference:
                    cache_key = (field.reference, depth + 1)
                    if cache_key not in cache:
                        raw_key = ('rows', field.reference)
                        if raw_key not in cache:
                            cache[raw_key] = list_records(connection, field.reference)
                        references = cache[raw_key]
                        cache[cache_key] = self._decorate(connection, field.reference, references, depth + 1, cache)
                    references = cache[cache_key]
                    reference_maps[field.name] = {str(row["Id"]): row["_label"] for row in references}
                    reference_active[field.name] = {str(row["Id"]): row.get("_active", True) for row in references}
                    reference_rows[field.name] = {str(row["Id"]): row for row in references}
        decorated = []
        for source in rows:
            row = dict(source)
            related = {
                field: lookup.get(str(row.get(field)), "")
                for field, lookup in reference_maps.items()
            }
            row["_related"] = related
            row["_label"] = _title(table, row, related)
            row["_active"] = bool(row.get("IsActive", True))
            if table in {"Teachers", "Students"}:
                row["_active"] = reference_active.get("PersonId", {}).get(str(row.get("PersonId")), False)
            if table in {"LectureGroups", "SubjectsSemesters"}:
                semester = reference_rows.get("SemesterId", {}).get(str(row.get("SemesterId")))
                if semester:
                    row["_duration_days"] = (_day(semester["EndDate"]) - _day(semester["StartDate"])).days
            decorated.append(row)
        return decorated

    def list(self, token: str, entity_key: str) -> list[dict]:
        spec = self._spec(entity_key)
        with self.db.connection() as connection:
            self.auth.require(token, spec.item_code, "view", connection=connection)
            rows = self._decorate(connection, spec.table, list_records(connection, spec.table))
            return sorted(rows, key=lambda row: (row["_label"].casefold(), str(row["Id"])))

    def choices(self, token: str, entity_key: str, field_name: str) -> list[dict]:
        spec = self._spec(entity_key)
        field = next((item for item in spec.fields if item.name == field_name), None)
        if not field or not field.reference:
            raise ValidationError("Pole nie jest powiązaniem z katalogiem.")
        with self.db.connection() as connection:
            self.auth.require(token, spec.item_code, "view", connection=connection)
            rows = self._decorate(connection, field.reference, list_records(connection, field.reference))
            return sorted(rows, key=lambda row: (row["_label"].casefold(), str(row["Id"])))

    def _normalize(self, connection, spec, values):
        data = dict(values)
        allowed = spec.allowed_fields
        data = validate_values(self.db, connection, spec.table, data, allowed, spec.required_fields)
        for field in spec.fields:
            value = data.get(field.name)
            if value is None:
                continue
            if field.minimum is not None and Decimal(str(value)) < Decimal(str(field.minimum)):
                raise ValidationError(f"{field.label}: minimalna wartość to {field.minimum}.")
            if field.maximum is not None and Decimal(str(value)) > Decimal(str(field.maximum)):
                raise ValidationError(f"{field.label}: maksymalna wartość to {field.maximum}.")
            if field.kind == "time":
                try:
                    parsed = time.fromisoformat(value)
                except (TypeError, ValueError):
                    raise ValidationError(f"{field.label}: podaj godzinę w formacie GG:MM.") from None
                if len(value) != 5 or parsed.second or parsed.microsecond:
                    raise ValidationError(f"{field.label}: podaj godzinę w formacie GG:MM.")
        return data

    def editor_choices(self, token: str, entity_key: str) -> dict[str, list[dict]]:
        """Wszystkie listy formularza w jednym połączeniu, z wspólnymi relacjami."""
        spec = self._spec(entity_key)
        with self.db.connection() as connection:
            self.auth.require(token, spec.item_code, 'view', connection=connection)
            cache, result = {}, {}
            for field in spec.fields:
                if field.reference:
                    raw_key = ('rows', field.reference)
                    if raw_key not in cache:
                        cache[raw_key] = list_records(connection, field.reference)
                    rows = self._decorate(connection, field.reference, cache[raw_key], cache=cache)
                    result[field.name] = sorted(rows, key=lambda row: (row['_label'].casefold(), str(row['Id'])))
            return result

    def _relations(self, connection, spec, data, current=None):
        references = {}
        for field in spec.fields:
            if not field.reference or data.get(field.name) is None:
                continue
            row = get_record(connection, field.reference, _id(data[field.name]), lock=True)
            if row is None:
                raise ValidationError(f"{field.label}: wskazany rekord już nie istnieje.")
            preparing_semester_data = field.reference == "Semesters" and spec.table != "SubjectsSemesters"
            if not preparing_semester_data and data.get("IsActive", True) and "IsActive" in row and not row["IsActive"]:
                old_value = current.get(field.name) if current else None
                old_active = current.get("IsActive", True) if current else False
                if not old_active or str(old_value) != str(data[field.name]):
                    raise ValidationError(f"{field.label}: wybierz aktywny rekord.")
            references[field.name] = row

        studies = references.get("StudiesId")
        specialization = references.get("SpecializationId")
        if specialization and studies and str(specialization.get("StudiesId")) != str(studies["Id"]):
            raise ValidationError("Specjalizacja musi należeć do wybranych studiów.")
        semester_number = data.get("SemesterNumber", data.get("Semester"))
        if studies and semester_number is not None:
            limit = studies.get("NumberOfSemesters")
            if limit and semester_number > limit:
                raise ValidationError("Numer semestru przekracza liczbę semestrów wybranych studiów.")

        if spec.table == "Subjects" and current and str(current.get("ProgramsOfStudyId")) != str(data.get("ProgramsOfStudyId")):
            raise ValidationError("Nowy program wymaga nowego rekordu przedmiotu. Dodaj przedmiot do nowego programu.")
        if spec.table == "LectureGroups":
            calendar = references["SemesterCalendarId"]
            if str(calendar.get("SemesterId")) != str(data["SemesterId"]):
                raise ValidationError("Kalendarz musi należeć do wybranego semestru.")
        if spec.table == "SubjectsSemesters":
            require_active_semester(connection, data["SemesterId"])
            if data["SemesterNumber"] != references["SubjectId"]["Semester"]:
                raise ValidationError("Numer semestru realizacji musi odpowiadać numerowi w programie przedmiotu.")
        if spec.table == "LectureGroupsElements":
            group, lecture = references["GroupId"], references["LectureGroupId"]
            if not group.get("SemesterId") or str(group["SemesterId"]) != str(lecture.get("SemesterId")):
                raise ValidationError("Grupa i grupa wykładowa muszą należeć do tego samego semestru.")
            self._validate_lecture_membership(connection, data, group, current)
        if spec.table == "GroupMembers":
            start, end = data.get("StartDate"), data.get("EndDate")
            if start and end and _day(end) < _day(start):
                raise ValidationError("Koniec członkostwa nie może poprzedzać początku.")
            if data.get("IsActive"):
                memberships = list_records(connection, "GroupMembers", {"StudentId": data["StudentId"], "GroupId": data["GroupId"]}, active_only=True)
                for membership in memberships:
                    if current and str(membership["Id"]) == str(current["Id"]):
                        continue
                    new_start, new_end = _day(start) if start else date.min, _day(end) if end else date.max
                    old_start = _day(membership["StartDate"]) if membership.get("StartDate") else date.min
                    old_end = _day(membership["EndDate"]) if membership.get("EndDate") else date.max
                    if new_start <= old_end and old_start <= new_end:
                        raise ConflictError("Student ma już aktywne członkostwo w tej grupie w podanym okresie.")
        if spec.table == "SemesterCalendarDates":
            calendar = references["SemesterCalendarId"]
            semester = get_record(connection, "Semesters", calendar["SemesterId"], lock=True)
            if not semester or not (_day(semester["StartDate"]) <= _day(data["Date"]) <= _day(semester["EndDate"])):
                raise ValidationError("Data zjazdu musi mieścić się w terminie semestru kalendarza.")
        if spec.table == "DailySchedules":
            if time.fromisoformat(data["EndHour"]) <= time.fromisoformat(data["StartHour"]):
                raise ValidationError("Koniec godziny musi być późniejszy niż początek.")
            if data.get("IsActive"):
                rows = list_records(connection, "DailySchedules", {"DailyScheduleTypeId": data["DailyScheduleTypeId"]}, active_only=True)
                for row in rows:
                    if current and str(row["Id"]) == str(current["Id"]):
                        continue
                    if row.get("StartHour") and row.get("EndHour") and data["StartHour"] < row["EndHour"] and row["StartHour"] < data["EndHour"]:
                        raise ConflictError("Godzina nakłada się na istniejącą godzinę tego planu dziennego.")
        self._duplicates(connection, spec, data, current)
        if current:
            self._protect_dependencies(connection, spec, data, current)

    @staticmethod
    def _has_active_hours(connection, table, record_id, visited=None):
        """Sprawdź zależności bez filtrowania historii rodziców aktywnych godzin."""
        visited = visited if visited is not None else set()
        marker = (table, str(record_id))
        if marker in visited:
            return False
        visited.add(marker)
        direct = {"SemesterOrganizations": "SemesterOrganizationId", "SemesterCalendarDates": "SemesterCalendarDateId",
                  "DailySchedules": "DailyScheduleId", "Teachers": "TeacherId"}
        if table in direct and list_records(connection, "SemesterOrganizationSchedules", {direct[table]: record_id}, active_only=True):
            return True
        children = {
            "SubjectsSemesters": (("SemesterOrganizations", "SubjectsSemesterId"),),
            "LectureGroups": (("SemesterOrganizations", "LectureGroupId"),),
            "Groups": (("LectureGroupsElements", "GroupId"),),
            "Subjects": (("SubjectsSemesters", "SubjectId"),),
            "SemesterCalendars": (("SemesterCalendarDates", "SemesterCalendarId"), ("LectureGroups", "SemesterCalendarId")),
            "DailyScheduleTypes": (("DailySchedules", "DailyScheduleTypeId"), ("LectureGroups", "DailyScheduleTypeId")),
            "Teachers": (("SemesterOrganizations", "TeacherId"),),
            "Persons": (("Teachers", "PersonId"),),
            "ProgramsOfStudies": (("Subjects", "ProgramsOfStudyId"),),
            "Studies": (("Subjects", "StudiesId"), ("Groups", "StudiesId")),
            "Specializations": (("Subjects", "SpecializationId"), ("Groups", "SpecializationId")),
            "SubjectTypes": (("Subjects", "SubjectTypeId"),),
            "LectureGroupTypes": (("LectureGroups", "LectureGroupTypeId"),),
            "AcademicTitles": (("Teachers", "AcademicTitleId"),),
            "FieldsOfStudies": (("Studies", "FieldsOfStudiesId"),),
            "ModesOfStudies": (("Studies", "ModesOfStudiesId"),),
            "StudiesDegrees": (("Studies", "StudiesDegreeId"),),
        }
        if table == "LectureGroupsElements":
            membership = get_record(connection, table, record_id)
            return bool(membership and membership.get("IsActive") and CatalogService._has_active_hours(
                connection, "LectureGroups", membership["LectureGroupId"], visited))
        for child_table, foreign_key in children.get(table, ()):
            records = list_records(connection, child_table, {foreign_key: record_id})
            if child_table == "LectureGroupsElements":
                records = [row for row in records if row.get("IsActive")]
            if any(CatalogService._has_active_hours(connection, child_table, row["Id"], visited) for row in records):
                return True
        return False

    @staticmethod
    def _validate_lecture_membership(connection, data, group, current):
        old_active = bool(current and current.get("IsActive"))
        new_active = bool(data.get("IsActive"))
        changed = not current or any(data.get(field) != current.get(field) for field in ("GroupId", "LectureGroupId", "IsActive"))
        affected = set()
        if changed and old_active:
            affected.add(current["LectureGroupId"])
        if changed and new_active:
            affected.add(data["LectureGroupId"])
        if any(CatalogService._has_active_hours(connection, "LectureGroups", lecture_id) for lecture_id in affected):
            raise ConflictError("Grupa wykładowa ma aktywne godziny w harmonogramie. Najpierw dezaktywuj godziny przed zmianą jej składu.")
        if not new_active:
            return
        assignments = list_records(connection, "SemesterOrganizations", {"LectureGroupId": data["LectureGroupId"]}, active_only=True)
        for assignment in assignments:
            realization = get_record(connection, "SubjectsSemesters", assignment["SubjectsSemesterId"])
            subject = get_record(connection, "Subjects", realization["SubjectId"]) if realization else None
            if not realization or not subject:
                raise ValidationError("Organizacja grupy wykładowej ma nieprawidłowy przedmiot. Popraw przypisanie przedmiotu.")
            number = int(realization.get("SemesterNumber") or subject.get("Semester") or 0)
            if (str(realization.get("SemesterId")) != str(group.get("SemesterId"))
                or str(subject.get("StudiesId")) != str(group.get("StudiesId"))
                or int(group.get("SemesterNumber") or 0) != number
                or (subject.get("SpecializationId") is not None and str(subject["SpecializationId"]) != str(group.get("SpecializationId")))):
                raise ValidationError("Grupa nie odpowiada studiom, numerowi semestru lub specjalizacji przedmiotów już przypisanych do grupy wykładowej.")

    @staticmethod
    def _protect_dependencies(connection, spec, data, current):
        def changed(*fields):
            return any(field in data and _day(data[field]) != _day(current.get(field)) for field in fields)

        def rows(table, field):
            return list_records(connection, table, {field: current["Id"]}, active_only=True)

        if current.get("IsActive") and data.get("IsActive") is False:
            if CatalogService._has_active_hours(connection, spec.table, current["Id"]):
                raise ConflictError("Rekord jest używany przez aktywne godziny harmonogramu. Najpierw dezaktywuj godziny przed wyłączeniem tego rekordu.")

        if spec.table == "SubjectsSemesters":
            assignments = rows("SemesterOrganizations", "SubjectsSemesterId")
            if assignments and changed("SubjectId", "SemesterId", "SemesterNumber"):
                raise ConflictError("Przedmiot ma aktywną organizację. Najpierw zmień jej przypisania.")
            if changed("NumberOfPlanningHours"):
                scheduled_by_group = {}
                for assignment in assignments:
                    hours = list_records(connection, "SemesterOrganizationSchedules", {"SemesterOrganizationId": assignment["Id"]}, active_only=True)
                    group = str(assignment["LectureGroupId"])
                    scheduled_by_group[group] = scheduled_by_group.get(group, 0) + sum(not row.get("IsCanceled", False) for row in hours)
                if any(count > data["NumberOfPlanningHours"] for count in scheduled_by_group.values()):
                    raise ConflictError("Liczba godzin planowanych nie może być mniejsza od już przydzielonych godzin grupy.")
        if spec.table == "Subjects" and changed("Semester", "StudiesId", "SpecializationId"):
            if rows("SubjectsSemesters", "SubjectId"):
                raise ConflictError("Przedmiot ma aktywne realizacje. Zmiana toku wymaga nowego rekordu przedmiotu.")
        if spec.table == "Groups" and changed("SemesterId", "StudiesId", "SemesterNumber", "SpecializationId"):
            if rows("LectureGroupsElements", "GroupId"):
                raise ConflictError("Grupa jest przypisana do grupy wykładowej. Najpierw zmień przypisanie.")
        if spec.table == "LectureGroups":
            assignments = rows("SemesterOrganizations", "LectureGroupId")
            if assignments and changed("SemesterId"):
                raise ConflictError("Grupa wykładowa ma organizację semestru. Nie można zmienić jej semestru.")
            if changed("SemesterCalendarId", "DailyScheduleTypeId") and any(
                list_records(connection, "SemesterOrganizationSchedules", {"SemesterOrganizationId": row["Id"]}, active_only=True)
                for row in assignments
            ):
                raise ConflictError("Grupa wykładowa ma godziny w harmonogramie. Najpierw zmień ich terminy.")
        if spec.table == "SemesterCalendars" and changed("SemesterId"):
            if rows("SemesterCalendarDates", "SemesterCalendarId") or rows("LectureGroups", "SemesterCalendarId"):
                raise ConflictError("Kalendarz jest już używany. Utwórz nowy kalendarz dla innego semestru.")
        if spec.table == "SemesterCalendarDates" and changed("Date", "SemesterCalendarId"):
            if rows("SemesterOrganizationSchedules", "SemesterCalendarDateId"):
                raise ConflictError("Data jest używana w harmonogramie. Zmień terminy godzin w harmonogramie.")
        if spec.table == "DailySchedules" and changed("StartHour", "EndHour", "DailyScheduleTypeId"):
            if rows("SemesterOrganizationSchedules", "DailyScheduleId"):
                raise ConflictError("Godzina jest używana w harmonogramie. Utwórz nową godzinę i zmień terminy zajęć.")

    @staticmethod
    def _lock(connection, table):
        acquire_write_lock(connection, "academic:organization-schedule")
        acquire_write_lock(connection, "catalog:" + table)

    @staticmethod
    def _duplicates(connection, spec, data, current):
        keys = {
            "SubjectsSemesters": ("SubjectId", "SemesterId"),
            "LectureGroupsElements": ("GroupId", "LectureGroupId"),
            "SemesterCalendarDates": ("SemesterCalendarId", "Date"),
            "DailySchedules": ("DailyScheduleTypeId", "Number"),
            "Teachers": ("PersonId",), "Students": ("PersonId",),
        }.get(spec.table)
        if not keys:
            return
        rows = list_records(connection, spec.table, {key: data[key] for key in keys})
        if any(not current or str(row["Id"]) != str(current["Id"]) for row in rows):
            raise ConflictError("Takie przypisanie już istnieje. Otwórz istniejący rekord do edycji.")

    def create(self, token: str, entity_key: str, values: dict) -> dict:
        spec = self._spec(entity_key)
        with self.db.connection() as connection:
            self.auth.require(token, spec.item_code, "create", connection=connection)
            self._lock(connection, spec.table)
            data = self._normalize(connection, spec, values)
            self._relations(connection, spec, data)
            return insert_record(connection, spec.table, data)

    def update(self, token: str, entity_key: str, record_id: str, values: dict, expected_timestamp: bytes) -> dict:
        spec = self._spec(entity_key)
        record_id = _id(record_id)
        with self.db.connection() as connection:
            self.auth.require(token, spec.item_code, "update", connection=connection)
            self._lock(connection, spec.table)
            current = get_record(connection, spec.table, record_id, lock=True)
            if not current:
                raise ConflictError("Rekord został usunięty. Odśwież listę.")
            if current.get("Timestamp") != expected_timestamp:
                raise ConflictError("Rekord zmieniła inna osoba. Odśwież listę przed zapisem.")
            data = self._normalize(connection, spec, values)
            self._relations(connection, spec, data, current)
            return update_record(connection, spec.table, record_id, data, expected_timestamp)

    def _candidates(self, connection, semester_id, program_id, studies_id, semester_number, specialization_id):
        semester_id, program_id, studies_id = _id(semester_id), _id(program_id), _id(studies_id)
        semester = require_active_semester(connection, semester_id)
        program = get_record(connection, "ProgramsOfStudies", program_id, lock=True)
        studies = get_record(connection, "Studies", studies_id, lock=True)
        if not program or not program["IsActive"] or not studies or not studies["IsActive"]:
            raise ValidationError("Wybierz aktywny program nauczania i aktywne studia.")
        if _day(program["StartDate"]) > _day(semester["StartDate"]):
            raise ValidationError("Program nauczania musi obowiązywać na początku wybranego semestru.")
        if type(semester_number) is not int or semester_number < 1:
            raise ValidationError("Podaj dodatni numer semestru studiów.")
        if studies.get("NumberOfSemesters") and semester_number > studies["NumberOfSemesters"]:
            raise ValidationError("Numer semestru przekracza liczbę semestrów wybranych studiów.")
        if specialization_id:
            specialization_id = _id(specialization_id)
            specialization = get_record(connection, "Specializations", specialization_id, lock=True)
            if not specialization or not specialization["IsActive"] or str(specialization.get("StudiesId")) != str(studies_id):
                raise ValidationError("Wybierz aktywną specjalizację należącą do wybranych studiów.")
        subjects = list_records(connection, "Subjects", {
            "ProgramsOfStudyId": program_id, "StudiesId": studies_id, "Semester": semester_number,
        }, active_only=True)
        return [row for row in subjects if row.get("SpecializationId") is None or (specialization_id and str(row["SpecializationId"]) == specialization_id)]

    def preview_subjects(self, token: str, semester_id: str, program_id: str, studies_id: str, semester_number: int, specialization_id=None):
        with self.db.connection() as connection:
            self.auth.require(token, "subjects_semesters", "view", connection=connection)
            subjects = self._candidates(connection, semester_id, program_id, studies_id, semester_number, specialization_id)
            return self._decorate(connection, "Subjects", subjects)

    def generation_choices(self, token: str, field_name: str):
        tables = {"semester_id": "Semesters", "program_id": "ProgramsOfStudies", "studies_id": "Studies", "specialization_id": "Specializations"}
        if field_name not in tables:
            raise ValidationError("Nieznane kryterium wyboru przedmiotów.")
        with self.db.connection() as connection:
            self.auth.require(token, "subjects_semesters", "view", connection=connection)
            table = tables[field_name]
            return self._decorate(connection, table, list_records(connection, table, active_only=True))

    def generate_subjects(self, token: str, semester_id: str, program_id: str, studies_id: str, semester_number: int, specialization_id=None, hybrid_hours: int = 0) -> GenerationResult:
        with self.db.connection() as connection:
            self.auth.require(token, "subjects_semesters", "create", connection=connection)
            self._lock(connection, "SubjectsSemesters")
            subjects = self._candidates(connection, semester_id, program_id, studies_id, semester_number, specialization_id)
            if not subjects:
                raise ValidationError("Program nie zawiera aktywnych przedmiotów dla podanych studiów, semestru i specjalizacji.")
            existing = list_records(connection, "SubjectsSemesters", {"SemesterId": semester_id})
            existing_ids = {str(row["SubjectId"]) for row in existing}
            spec = self._spec("subjects_semesters")
            drafts = []
            skipped = 0
            for subject in subjects:
                if str(subject["Id"]) in existing_ids:
                    skipped += 1
                    continue
                if subject.get("NumberOfHours") is None:
                    raise ValidationError(f"Przedmiot {subject['Name']} nie ma liczby godzin w programie. Uzupełnij ją przed generowaniem.")
                data = self._normalize(connection, spec, {
                    "SubjectId": str(subject["Id"]), "SemesterId": semester_id,
                    "NumberOfHybridHours": hybrid_hours, "NumberOfPlanningHours": subject["NumberOfHours"],
                    "SemesterNumber": semester_number, "IsActive": True,
                })
                self._relations(connection, spec, data)
                drafts.append(data)
            created = tuple(insert_record(connection, spec.table, data) for data in drafts)
            return GenerationResult(created, skipped)
