"""Relacje, kontrola zapisu i wybór rzeczywistych przedmiotów bez zapisów online."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import date, datetime
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

import academic.catalogs.service as module
from academic.auth.models import AuthorizationError
from academic.catalogs.service import CatalogService
from academic.catalogs.specs import CATALOGS, TABLE_SPECS
from academic.errors import ConflictError, ValidationError


VERSION = b"\x00\x00\x00\x00\x00\x00\x00\x01"
NEXT_VERSION = b"\x00\x00\x00\x00\x00\x00\x00\x02"


class Auth:
    def __init__(self):
        self.actions = {"view", "create", "update"}
        self.checks = []

    def can(self, token, item, action):
        return action in self.actions

    def require(self, token, item, action, connection=None):
        self.checks.append((item, action))
        if not self.can(token, item, action):
            raise AuthorizationError("Brak uprawnień.")


class MemoryDatabase:
    def __init__(self):
        self.rows = {}
        self.writes = []
        self.commits = 0
        self.rollbacks = 0
        self.locks = []

    @contextmanager
    def connection(self):
        snapshot = deepcopy(self.rows)
        try:
            yield self
        except Exception:
            self.rows = snapshot
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def add(self, table, **values):
        row = {"Id": str(uuid4()), "Timestamp": VERSION, **values}
        self.rows.setdefault(table, []).append(row)
        return row

    def columns(self, table, **kwargs):
        columns = {}
        for field in TABLE_SPECS[table].fields:
            kind = {"boolean": "bit", "integer": "int", "decimal": "decimal", "date": "date", "reference": "uniqueidentifier"}.get(field.kind, "nvarchar")
            columns[field.name] = {"DATA_TYPE": kind, "IS_NULLABLE": "NO" if field.required else "YES",
                "CHARACTER_MAXIMUM_LENGTH": 200, "CODE_PAGE": 1250, "NUMERIC_PRECISION": 5, "NUMERIC_SCALE": 2}
        return columns


@pytest.fixture
def backend(monkeypatch):
    db = MemoryDatabase()
    auth = Auth()

    def get_record(connection, table, id, lock=False):
        return next((row for row in db.rows.get(table, []) if str(row["Id"]) == str(id)), None)

    def list_records(connection, table, filters=None, active_only=False):
        return [row for row in db.rows.get(table, [])
            if all(row.get(key) == value for key, value in (filters or {}).items())
            and (not active_only or row.get("IsActive", True))]

    def insert_record(connection, table, values):
        db.writes.append(("insert", table, dict(values)))
        return db.add(table, **values)

    def update_record(connection, table, id, values, expected_timestamp):
        row = get_record(connection, table, id)
        if not row or row["Timestamp"] != expected_timestamp:
            raise ConflictError("Zmieniony rekord.")
        row.update(values, Timestamp=NEXT_VERSION)
        db.writes.append(("update", table, dict(values)))
        return row

    def active(connection, id):
        semester = get_record(connection, "Semesters", id)
        if not semester or not semester["IsActive"]:
            raise ValidationError("Wymagany aktywny semestr.")
        return semester

    monkeypatch.setattr(module, "get_record", get_record)
    monkeypatch.setattr(module, "list_records", list_records)
    monkeypatch.setattr(module, "insert_record", insert_record)
    monkeypatch.setattr(module, "update_record", update_record)
    monkeypatch.setattr(module, "require_active_semester", active)
    monkeypatch.setattr(module, "acquire_write_lock", lambda connection, name: db.locks.append(name))
    return CatalogService(db, auth), db, auth


@pytest.fixture
def curriculum(backend):
    service, db, auth = backend
    semester = db.add("Semesters", Name="Okres realizacji", AcademicYearName="Rok", IsActive=True,
        StartDate=date(2026, 10, 1), EndDate=date(2027, 2, 15))
    program = db.add("ProgramsOfStudies", ProgramName="Program A", KRKNumber="A", KRKName="A", IsActive=True, StartDate=date(2024, 10, 1))
    studies = db.add("Studies", Name="Studia A", ShortName="A", IsActive=True, NumberOfSemesters=6)
    specialization = db.add("Specializations", Name="Specjalizacja A", ShortName="A", IsActive=True, StudiesId=studies["Id"])
    subject_type = db.add("SubjectTypes", Name="Wykład", ShortName="W", IsActive=True)

    def subject(**changes):
        values = {"Name": "Przedmiot programu", "ShortName": None, "Semester": 1, "Exam": False,
            "Symbol": None, "Subsymbol": None, "EctsPoints": None, "NumberOfHours": 30, "IsActive": True,
            "SubjectTypeId": subject_type["Id"], "ProgramsOfStudyId": program["Id"],
            "StudiesId": studies["Id"], "SpecializationId": None, **changes}
        return db.add("Subjects", **values)
    return service, db, auth, semester, program, studies, specialization, subject


@pytest.fixture
def planned_state(curriculum):
    service, db, _, semester, _, studies, _, subject = curriculum
    course = subject()
    realization = db.add("SubjectsSemesters", SubjectId=course["Id"], SemesterId=semester["Id"],
        NumberOfHybridHours=0, NumberOfPlanningHours=30, SemesterNumber=1, IsActive=True)
    group = db.add("Groups", Name="Grupa A", SemesterNumber=1, StudiesId=studies["Id"], SemesterId=semester["Id"], SpecializationId=None, IsActive=True)
    lecture_type = db.add("LectureGroupTypes", Name="Wykładowa", ShortName="W", IsActive=True)
    daily_type = db.add("DailyScheduleTypes", Name="Plan", ShortName="P", IsActive=True)
    calendar = db.add("SemesterCalendars", Name="Kalendarz", ShortName="K", SemesterId=semester["Id"], IsActive=True)
    day = db.add("SemesterCalendarDates", SemesterCalendarId=calendar["Id"], Date=date(2026, 10, 10), Number=1, IsActive=True)
    hour = db.add("DailySchedules", DailyScheduleTypeId=daily_type["Id"], Number=1, StartHour="08:00", EndHour="08:45", IsActive=True)
    lecture = db.add("LectureGroups", Name="Wykład", ShortName="W", SemesterId=semester["Id"], LectureGroupTypeId=lecture_type["Id"],
        SemesterCalendarId=calendar["Id"], DailyScheduleTypeId=daily_type["Id"], IsActive=True, TeamsGroupID=None, TeamsGroupName=None)
    membership = db.add("LectureGroupsElements", GroupId=group["Id"], LectureGroupId=lecture["Id"], IsActive=True)
    person = db.add("Persons", FirstName1="Imię", FirstName2=None, LastNamePrefix=None, LastName="Nazwisko", AdditionalInfo=None, IsActive=True)
    teacher = db.add("Teachers", PersonId=person["Id"], ShortName="IN", AcademicTitleId=None)
    assignment = db.add("SemesterOrganizations", SubjectsSemesterId=realization["Id"], LectureGroupId=lecture["Id"], TeacherId=teacher["Id"], IsActive=True)
    scheduled = db.add("SemesterOrganizationSchedules", SemesterOrganizationId=assignment["Id"], SemesterCalendarDateId=day["Id"],
        DailyScheduleId=hour["Id"], TeacherId=None, IsActive=True, IsCanceled=False)
    records = {"subjects_semesters": realization, "groups": group, "lecture_groups": lecture,
        "lecture_group_elements": membership, "semester_calendars": calendar, "semester_calendar_dates": day,
        "daily_schedules": hour, "daily_schedule_types": daily_type, "lecture_group_types": lecture_type,
        "persons": person, "subjects": course, "teacher": teacher, "assignment": assignment, "scheduled": scheduled}
    return service, db, records


@pytest.mark.parametrize("operation", ["create", "update"])
def test_permission_denial_precedes_schema_or_write(backend, operation):
    service, db, auth = backend
    auth.actions.remove(operation)
    with pytest.raises(AuthorizationError):
        if operation == "create":
            service.create("session", "subject_types", {"Name": "Typ", "ShortName": "T", "IsActive": True})
        else:
            service.update("session", "subject_types", str(uuid4()), {}, VERSION)
    assert db.writes == db.locks == []
    assert db.rollbacks == 1


@pytest.mark.parametrize("extra", [{"Timestamp": VERSION}, {"Id": str(uuid4())}, {"Duration": 30}])
def test_technical_and_nonexistent_fields_cannot_be_written(backend, extra):
    service, db, _ = backend
    with pytest.raises(ValidationError):
        service.create("session", "subject_types", {"Name": "Typ", "ShortName": "T", "IsActive": True, **extra})
    assert db.writes == []


def test_preparation_groups_does_not_require_active_semester(curriculum):
    service, db, _, semester, _, studies, _, _ = curriculum
    semester["IsActive"] = False
    saved = service.create("session", "groups", {"Name": "Grupa A", "SemesterNumber": 1, "IsActive": True,
        "StudiesId": studies["Id"], "SemesterId": semester["Id"], "SpecializationId": None})
    assert saved["SemesterId"] == semester["Id"]


def test_group_rejects_specialization_of_another_studies(curriculum):
    service, db, _, semester, _, studies, specialization, _ = curriculum
    specialization["StudiesId"] = str(uuid4())
    with pytest.raises(ValidationError, match="Specjalizacja"):
        service.create("session", "groups", {"Name": "Grupa A", "SemesterNumber": 1, "IsActive": True,
            "StudiesId": studies["Id"], "SemesterId": semester["Id"], "SpecializationId": specialization["Id"]})
    assert db.writes == []


def test_preparing_group_missing_semester_is_rejected(curriculum):
    service, db, _, _, _, studies, _, _ = curriculum
    with pytest.raises(ValidationError, match="nie istnieje"):
        service.create("session", "groups", {"Name": "Grupa A", "SemesterNumber": 1, "IsActive": True,
            "StudiesId": studies["Id"], "SemesterId": str(uuid4()), "SpecializationId": None})
    assert db.writes == []


def test_program_change_requires_new_subject_record(curriculum):
    service, db, _, _, _, _, _, subject = curriculum
    existing = subject()
    program = db.add("ProgramsOfStudies", ProgramName="Następny", IsActive=True)
    values = {key: existing.get(key) for key in CATALOGS["subjects"].allowed_fields}
    values["ProgramsOfStudyId"] = program["Id"]
    with pytest.raises(ValidationError, match="nowego rekordu"):
        service.update("session", "subjects", existing["Id"], values, VERSION)
    assert db.writes == []


def test_optimistic_snapshot_cannot_be_bypassed(backend):
    service, db, _ = backend
    row = db.add("SubjectTypes", Name="Typ", ShortName="T", IsActive=True)
    with pytest.raises(ConflictError, match="inna osoba"):
        service.update("session", "subject_types", row["Id"], {"Name": "Zmiana", "ShortName": "Z", "IsActive": True}, NEXT_VERSION)
    assert db.writes == []


def test_generation_filters_by_program_studies_semester_and_specialization(curriculum):
    service, db, _, semester, program, studies, specialization, subject = curriculum
    common = subject(Name="Wspólny")
    specialized = subject(Name="Specjalizacja", SpecializationId=specialization["Id"])
    subject(Name="Inny program", ProgramsOfStudyId=str(uuid4()))
    subject(Name="Inne studia", StudiesId=str(uuid4()))
    subject(Name="Inny semestr", Semester=2)
    subject(Name="Inna specjalizacja", SpecializationId=str(uuid4()))
    subject(Name="Nieaktywny", IsActive=False)
    result = service.generate_subjects("session", semester["Id"], program["Id"], studies["Id"], 1, specialization["Id"], hybrid_hours=4)
    assert {row["SubjectId"] for row in result.created} == {common["Id"], specialized["Id"]}
    assert all(row["NumberOfPlanningHours"] == 30 and row["NumberOfHybridHours"] == 4 for row in result.created)
    second = service.generate_subjects("session", semester["Id"], program["Id"], studies["Id"], 1, specialization["Id"])
    assert second.created == () and second.already_existing == 2


def test_missing_curriculum_hours_prevents_entire_generation(curriculum):
    service, db, _, semester, program, studies, _, subject = curriculum
    subject()
    subject(Name="Bez godzin", NumberOfHours=None)
    with pytest.raises(ValidationError, match="nie ma liczby godzin"):
        service.generate_subjects("session", semester["Id"], program["Id"], studies["Id"], 1)
    assert db.writes == []
    assert db.rows.get("SubjectsSemesters", []) == []


def test_generating_requires_active_semester_and_applicable_program(curriculum):
    service, db, _, semester, program, studies, _, subject = curriculum
    subject()
    semester["IsActive"] = False
    with pytest.raises(ValidationError, match="aktywny semestr"):
        service.generate_subjects("session", semester["Id"], program["Id"], studies["Id"], 1)
    semester = db.rows["Semesters"][0]
    semester["IsActive"] = True
    db.rows["ProgramsOfStudies"][0]["StartDate"] = date(2027, 1, 1)
    with pytest.raises(ValidationError, match="obowiązywać"):
        service.generate_subjects("session", semester["Id"], program["Id"], studies["Id"], 1)
    assert db.writes == []


def test_manual_realization_checks_subject_curriculum_number(curriculum):
    service, db, _, semester, _, _, _, subject = curriculum
    selected = subject(Semester=2)
    with pytest.raises(ValidationError, match="Numer semestru realizacji"):
        service.create("session", "subjects_semesters", {"SubjectId": selected["Id"], "SemesterId": semester["Id"],
            "SemesterNumber": 1, "NumberOfHybridHours": 0, "NumberOfPlanningHours": 30, "IsActive": True})
    assert db.writes == []


def test_calendar_date_must_be_inside_its_semester(curriculum):
    service, db, _, semester, _, _, _, _ = curriculum
    calendar = db.add("SemesterCalendars", Name="Kalendarz", ShortName="K", IsActive=True, SemesterId=semester["Id"])
    with pytest.raises(ValidationError, match="terminie semestru"):
        service.create("session", "semester_calendar_dates", {"SemesterCalendarId": calendar["Id"],
            "Date": date(2027, 3, 1), "Number": 1, "IsActive": True})
    assert db.writes == []


def test_daily_slots_cannot_overlap_but_adjacent_slots_are_valid(backend):
    service, db, _ = backend
    kind = db.add("DailyScheduleTypes", Name="Plan", ShortName="P", IsActive=True)
    db.add("DailySchedules", Number=1, StartHour="08:00", EndHour="08:45", IsActive=True, DailyScheduleTypeId=kind["Id"])
    values = {"Number": 2, "StartHour": "08:30", "EndHour": "09:15", "IsActive": True, "DailyScheduleTypeId": kind["Id"]}
    with pytest.raises(ConflictError, match="nakłada"):
        service.create("session", "daily_schedules", values)
    values["StartHour"] = "08:45"
    saved = service.create("session", "daily_schedules", values)
    assert saved["StartHour"] == "08:45"


def test_teacher_choices_use_person_activity_even_without_teacher_active_column(backend):
    service, db, _ = backend
    person = db.add("Persons", FirstName1="Imię", LastName="Nazwisko", IsActive=False)
    teacher = db.add("Teachers", PersonId=person["Id"], ShortName="IN", AcademicTitleId=None)
    listed = service.list("session", "teachers")
    assert listed[0]["Id"] == teacher["Id"] and listed[0]["_active"] is False
    assert "Nazwisko" in listed[0]["_label"]


def test_hours_cannot_be_reduced_below_active_uncanceled_slots(curriculum):
    service, db, _, semester, _, _, _, subject = curriculum
    selected = subject()
    realization = db.add("SubjectsSemesters", SubjectId=selected["Id"], SemesterId=semester["Id"],
        SemesterNumber=1, NumberOfHybridHours=0, NumberOfPlanningHours=2, IsActive=True)
    assignment = db.add("SemesterOrganizations", SubjectsSemesterId=realization["Id"], LectureGroupId=str(uuid4()), IsActive=True)
    db.add("SemesterOrganizationSchedules", SemesterOrganizationId=assignment["Id"], IsActive=True, IsCanceled=False)
    second = db.add("SemesterOrganizationSchedules", SemesterOrganizationId=assignment["Id"], IsActive=True, IsCanceled=False)
    values = {field: realization[field] for field in CATALOGS["subjects_semesters"].allowed_fields}
    values["NumberOfPlanningHours"] = 1
    with pytest.raises(ConflictError, match="już przydzielonych"):
        service.update("session", "subjects_semesters", realization["Id"], values, VERSION)
    db.rows["SemesterOrganizationSchedules"][1]["IsCanceled"] = True
    saved = service.update("session", "subjects_semesters", realization["Id"], values, VERSION)
    assert saved["NumberOfPlanningHours"] == 1
    assert "academic:organization-schedule" in db.locks


def test_same_calendar_day_from_datetime_round_trip_keeps_scheduled_hours(curriculum):
    service, db, _, semester, _, _, _, _ = curriculum
    calendar = db.add("SemesterCalendars", Name="Kalendarz", ShortName="K", IsActive=True, SemesterId=semester["Id"])
    day = db.add("SemesterCalendarDates", SemesterCalendarId=calendar["Id"], Date=datetime(2026, 10, 10), Number=1, IsActive=True)
    db.add("SemesterOrganizationSchedules", SemesterCalendarDateId=day["Id"], IsActive=True)
    saved = service.update("session", "semester_calendar_dates", day["Id"], {"SemesterCalendarId": calendar["Id"],
        "Date": date(2026, 10, 10), "Number": 1, "IsActive": True}, VERSION)
    assert saved["Date"] == date(2026, 10, 10)


@pytest.mark.parametrize("entity", ["subjects_semesters", "groups", "lecture_groups", "lecture_group_elements",
    "semester_calendars", "semester_calendar_dates", "daily_schedules", "daily_schedule_types", "lecture_group_types", "persons", "subjects"])
def test_deactivation_cannot_hide_existing_active_hours(planned_state, entity):
    service, db, records = planned_state
    row = records[entity]
    values = {field: row.get(field) for field in CATALOGS[entity].allowed_fields}
    values["IsActive"] = False
    with pytest.raises(ConflictError, match="aktywne godziny"):
        service.update("session", entity, row["Id"], values, VERSION)
    assert db.writes == []
    persisted = next(row for row in db.rows[CATALOGS[entity].table] if row["Id"] == records[entity]["Id"])
    assert persisted["IsActive"] is True


@pytest.mark.parametrize("operation", ["create", "change", "activate"])
def test_scheduled_lecture_group_membership_cannot_bypass_schedule_collision_checks(planned_state, operation):
    service, db, records = planned_state
    group = db.add("Groups", **{key: value for key, value in records["groups"].items() if key not in {"Id", "Timestamp"}})
    values = {"GroupId": group["Id"], "LectureGroupId": records["lecture_groups"]["Id"], "IsActive": True}
    with pytest.raises(ConflictError, match="przed zmianą jej składu"):
        if operation == "create":
            service.create("session", "lecture_group_elements", values)
        elif operation == "change":
            service.update("session", "lecture_group_elements", records["lecture_group_elements"]["Id"], values, VERSION)
        else:
            dormant = db.add("LectureGroupsElements", **{**values, "IsActive": False})
            service.update("session", "lecture_group_elements", dormant["Id"], values, VERSION)
    assert db.writes == []


def test_membership_of_unscheduled_organization_still_requires_matching_curriculum(planned_state):
    service, db, records = planned_state
    records["scheduled"]["IsActive"] = False
    group = db.add("Groups", **{key: value for key, value in records["groups"].items() if key not in {"Id", "Timestamp", "SemesterNumber"}}, SemesterNumber=2)
    with pytest.raises(ValidationError, match="przedmiotów już przypisanych"):
        service.create("session", "lecture_group_elements", {"GroupId": group["Id"], "LectureGroupId": records["lecture_groups"]["Id"], "IsActive": True})
    assert db.writes == []


def test_deactivation_is_available_after_hours_are_explicitly_disabled(planned_state):
    service, db, records = planned_state
    records["scheduled"]["IsActive"] = False
    row = records["groups"]
    values = {field: row.get(field) for field in CATALOGS["groups"].allowed_fields}
    values["IsActive"] = False
    saved = service.update("session", "groups", row["Id"], values, VERSION)
    assert saved["IsActive"] is False


def test_canceled_active_hours_remain_protected_for_makeup_balance(planned_state):
    service, db, records = planned_state
    records["scheduled"]["IsCanceled"] = True
    row = records["subjects_semesters"]
    values = {field: row.get(field) for field in CATALOGS["subjects_semesters"].allowed_fields}
    values["IsActive"] = False
    with pytest.raises(ConflictError, match="aktywne godziny"):
        service.update("session", "subjects_semesters", row["Id"], values, VERSION)
    assert db.writes == []


def catalog_ui_app():
    import streamlit as st
    from academic.ui.catalogs import render_catalogs
    render_catalogs(st.session_state["service"], "session", st.session_state["entity"])


def ui(service, entity):
    app = AppTest.from_function(catalog_ui_app)
    app.session_state["service"] = service
    app.session_state["entity"] = entity
    app.run()
    assert not app.exception
    return app


def test_catalog_ui_creates_only_on_explicit_save_and_revokes_editor(backend):
    service, db, auth = backend
    app = ui(service, "subject_types")
    app.button(key="catalog_subject_types_add").click().run()
    assert not app.exception
    assert db.writes == []
    app.text_input(key="catalog_subject_types_field_Name").set_value("Laboratorium")
    app.text_input(key="catalog_subject_types_field_ShortName").set_value("L").run()
    assert db.writes == []
    app.button(key="catalog_subject_types_save").click().run()
    assert not app.exception
    assert len(db.writes) == 1
    assert any("utworzony" in message.value for message in app.success)
    app.button(key="catalog_subject_types_add").click().run()
    auth.actions = {"view"}
    app.run()
    assert not app.exception
    assert not any(button.key == "catalog_subject_types_save" for button in app.button)


def test_catalog_ui_starts_with_no_invented_dates_or_names(backend):
    service, db, _ = backend
    app = ui(service, "programs")
    app.button(key="catalog_programs_add").click().run()
    assert not app.exception
    assert app.text_input(key="catalog_programs_field_ProgramName").value == ""
    assert app.date_input(key="catalog_programs_field_StartDate").value is None
    assert db.writes == []


def test_subject_semester_dropdown_uses_number_of_semesters_from_selected_studies(curriculum):
    service, db, _, _, _, studies, _, _ = curriculum
    app = ui(service, "subjects")
    app.button(key="catalog_subjects_add").click().run()
    assert app.selectbox(key="catalog_subjects_field_Semester").options == ["Wybierz…"]
    app.selectbox(key="catalog_subjects_field_StudiesId").set_value(studies["Id"]).run()
    assert not app.exception
    assert app.selectbox(key="catalog_subjects_field_Semester").options == ["Wybierz…", "1", "2", "3", "4", "5", "6"]
    assert db.writes == []


@pytest.mark.parametrize("entity", list(CATALOGS))
def test_every_source_catalog_editor_renders_without_creating_data(backend, entity):
    service, db, _ = backend
    app = ui(service, entity)
    app.button(key="catalog_" + entity + "_add").click().run()
    assert not app.exception
    assert db.writes == []
    assert any(input.disabled and input.label == "Znacznik czasu (Timestamp)" for input in app.text_input)


def test_catalog_ui_preserves_original_version_while_list_changes(backend):
    service, db, _ = backend
    row = db.add("SubjectTypes", Name="Typ", ShortName="T", IsActive=True)
    app = ui(service, "subject_types")
    app.button(key="catalog_subject_types_edit_" + row["Id"]).click().run()
    row["Timestamp"] = NEXT_VERSION
    app.text_input(key="catalog_subject_types_field_Name").set_value("Moja zmiana").run()
    app.button(key="catalog_subject_types_save").click().run()
    assert not app.exception
    assert db.writes == []
    assert any("inna osoba" in error.value for error in app.error)
    assert app.text_input(key="catalog_subject_types_field_Name").value == "Moja zmiana"
