"""Kontrola procesów i transakcji na izolowanych rekordach, bez zdalnych zapisów."""

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime
from uuid import uuid4

import pytest

from academic.auth.models import AuthorizationError
from academic.errors import ConflictError, ValidationError
from academic.organization.service import OrganizationService
import academic.organization.service as org_module
from academic.scheduling.models import ScheduleInput, calculate_balance, intervals_overlap
from academic.scheduling.service import ScheduleService
import academic.scheduling.service as schedule_module
from academic.records import validate_values

VERSION = b"\x00" * 7 + b"\x01"


class Grants:
    def __init__(self):
        self.actions = {"view", "create", "update"}
        self.checks = []

    def require(self, token, code, action, connection=None):
        self.checks.append((code, action, connection))
        if action not in self.actions:
            raise AuthorizationError("Brak uprawnień.")

    def can(self, token, code, action):
        return action in self.actions


class MemoryDB:
    def __init__(self):
        self.tables, self.commits, self.rollbacks, self.writes = {}, 0, 0, []

    @contextmanager
    def connection(self):
        original = deepcopy(self.tables)
        try:
            yield self
        except Exception:
            self.tables = original
            self.rollbacks += 1
            raise
        else:
            self.commits += 1

    def add(self, table, **values):
        row = {"Id": str(uuid4()), "IsActive": True, "Timestamp": VERSION, **values}
        self.tables.setdefault(table, []).append(row)
        return row

    def columns(self, table, **kwargs):
        fields = org_module.FIELDS if table == "SemesterOrganizations" else schedule_module.FIELDS
        return {name: {"DATA_TYPE": "uniqueidentifier" if name.endswith("Id") else "bit" if name.startswith("Is") else "varchar",
                       "IS_NULLABLE": "YES" if name in {"TeacherId", "LectureRoomName"} else "NO",
                       "CHARACTER_MAXIMUM_LENGTH": 50, "CODE_PAGE": 1250} for name in fields}


@pytest.fixture
def system(monkeypatch):
    db, auth = MemoryDB(), Grants()
    semester = db.add("Semesters", Name="Semestr", StartDate=datetime(2026, 1, 1), EndDate=datetime(2026, 6, 30))
    studies = db.add("Studies", Name="Studia")
    program = db.add("ProgramsOfStudies", ProgramName="Program", StartDate=datetime(2025, 1, 1))
    subject_type = db.add("SubjectTypes", Name="Wykład")
    lecture_type = db.add("LectureGroupTypes", Name="Grupa wykładowa")
    subject = db.add("Subjects", Name="Przedmiot", StudiesId=studies["Id"], ProgramsOfStudyId=program["Id"],
                     SubjectTypeId=subject_type["Id"], Semester=1, SpecializationId=None)
    ss = db.add("SubjectsSemesters", SubjectId=subject["Id"], SemesterId=semester["Id"], SemesterNumber=1, NumberOfPlanningHours=3)
    group = db.add("Groups", Name="Grupa", SemesterId=semester["Id"], StudiesId=studies["Id"], SemesterNumber=1, SpecializationId=None)
    calendar = db.add("SemesterCalendars", SemesterId=semester["Id"])
    day = db.add("SemesterCalendarDates", SemesterCalendarId=calendar["Id"], Date=datetime(2026, 2, 2), Number=1)
    day2 = db.add("SemesterCalendarDates", SemesterCalendarId=calendar["Id"], Date=datetime(2026, 2, 3), Number=2)
    daily_type = db.add("DailyScheduleTypes", Name="Typ")
    hour = db.add("DailySchedules", DailyScheduleTypeId=daily_type["Id"], StartHour="08:00", EndHour="08:45", Number=1)
    hour2 = db.add("DailySchedules", DailyScheduleTypeId=daily_type["Id"], StartHour="08:45", EndHour="09:30", Number=2)
    person = db.add("Persons", FirstName1="Jan", LastName="Kowalski")
    teacher = db.add("Teachers", PersonId=person["Id"])
    lg = db.add("LectureGroups", Name="Wykład", SemesterId=semester["Id"], SemesterCalendarId=calendar["Id"],
                LectureGroupTypeId=lecture_type["Id"], DailyScheduleTypeId=daily_type["Id"])
    db.add("LectureGroupsElements", LectureGroupId=lg["Id"], GroupId=group["Id"])
    org = db.add("SemesterOrganizations", SubjectsSemesterId=ss["Id"], LectureGroupId=lg["Id"], TeacherId=teacher["Id"])

    def get(conn, table, record_id, lock=False):
        return next((row for row in conn.tables.get(table, []) if str(row["Id"]) == str(record_id)), None)

    def listed(conn, table, filters=None, active_only=False):
        return [row for row in conn.tables.get(table, []) if
                all(str(row.get(key)) == str(value) for key, value in (filters or {}).items())
                and (not active_only or row.get("IsActive", True))]

    def insert(conn, table, values):
        conn.writes.append(("insert", table))
        return conn.add(table, **values)

    def update(conn, table, record_id, values, timestamp):
        row = get(conn, table, record_id)
        if row is None or timestamp != row["Timestamp"] or not isinstance(timestamp, bytes):
            raise ConflictError("Rekord zmieniła inna osoba.")
        row.update(values)
        row["Timestamp"] = b"\x00" * 7 + bytes([row["Timestamp"][-1] + 1])
        conn.writes.append(("update", table))
        return row

    def active_semester(conn, record_id):
        row = get(conn, "Semesters", record_id)
        if not row or not row["IsActive"]:
            raise ValidationError("Semestr nieaktywny.")
        return row

    def members(conn, group_id):
        ids = {row["GroupId"] for row in listed(conn, "LectureGroupsElements", {"LectureGroupId": group_id}, True)}
        return [row for row in listed(conn, "Groups", active_only=True) if row["Id"] in ids]

    def one(conn, sql, params=()):
        if "SemesterOrganizations WHERE" in sql:
            return next((row for row in listed(conn, "SemesterOrganizations", active_only=True) if
                (row["SubjectsSemesterId"], row["LectureGroupId"], row["TeacherId"]) == params[:3]
                and row["Id"] != params[3]), None)
        if "SemesterOrganizationSchedules" in sql:
            return next((row for row in listed(conn, "SemesterOrganizationSchedules", {"SemesterOrganizationId": params[0]}, True)), None)
        raise AssertionError(sql)

    def organization_rows(conn, semester_id):
        result = []
        for row in listed(conn, "SemesterOrganizations"):
            current_ss = get(conn, "SubjectsSemesters", row["SubjectsSemesterId"])
            if str(current_ss["SemesterId"]) == str(semester_id):
                result.append({**row, "SubjectName": get(conn, "Subjects", current_ss["SubjectId"])["Name"],
                               "LectureGroupName": get(conn, "LectureGroups", row["LectureGroupId"])["Name"]})
        return result

    def slots(conn, semester_id=None):
        result = []
        for row in listed(conn, "SemesterOrganizationSchedules"):
            current_org = get(conn, "SemesterOrganizations", row["SemesterOrganizationId"])
            current_ss = get(conn, "SubjectsSemesters", current_org["SubjectsSemesterId"])
            if semester_id and str(current_ss["SemesterId"]) != str(semester_id):
                continue
            current_day = get(conn, "SemesterCalendarDates", row["SemesterCalendarDateId"])
            current_hour = get(conn, "DailySchedules", row["DailyScheduleId"])
            result.append({**row, "SubjectsSemesterId": current_ss["Id"], "LectureGroupId": current_org["LectureGroupId"],
                           "OrganizationTeacherId": current_org["TeacherId"], "Date": current_day["Date"],
                           "StartHour": current_hour["StartHour"], "EndHour": current_hour["EndHour"]})
        return result

    for module in (org_module, schedule_module):
        for name, function in (("get_record", get), ("list_records", listed), ("insert_record", insert),
                               ("update_record", update), ("require_active_semester", active_semester),
                               ("lecture_members", members), ("acquire_write_lock", lambda *args: None)):
            monkeypatch.setattr(module, name, function)
        monkeypatch.setattr(module, "validate_values", validate_values)
    monkeypatch.setattr(org_module, "one", one)
    monkeypatch.setattr(OrganizationService, "_list", staticmethod(organization_rows))
    monkeypatch.setattr(ScheduleService, "_slots", staticmethod(slots))
    return {**locals(), "organizations": OrganizationService(db, auth), "schedules": ScheduleService(db, auth)}


def draft(system, **changes):
    values = {"SemesterOrganizationId": system["org"]["Id"], "SemesterCalendarDateId": system["day"]["Id"],
              "DailyScheduleId": system["hour"]["Id"], "LectureRoomName": "101"}
    return {**values, **changes}


def test_hour_balance_canceled_returns_to_unassigned_and_completed_counted():
    rows = [{"IsActive": True, "IsCanceled": False, "IsComplited": True},
            {"IsActive": True, "IsCanceled": True, "IsComplited": False},
            {"IsActive": True, "IsCanceled": False, "IsComplited": False},
            {"IsActive": False, "IsCanceled": False, "IsComplited": True}]
    assert calculate_balance(3, rows) == {"required": 3, "planned": 2, "completed": 1,
        "canceled": 1, "to_complete": 1, "unassigned": 1, "excess": 0}


def test_overlapping_actual_clock_ranges_are_detected_with_touching_edges_allowed():
    assert intervals_overlap("08:00", "08:45", "08:30", "09:15")
    assert not intervals_overlap("08:00", "08:45", "08:45", "09:30")


def test_creation_requires_real_active_semester(system):
    system["semester"]["IsActive"] = False
    with pytest.raises(ValidationError):
        system["schedules"].create("token", draft(system))
    assert system["db"].writes == []


def test_creation_checks_permissions_before_any_write(system):
    system["auth"].actions.remove("create")
    with pytest.raises(AuthorizationError):
        system["schedules"].create("token", draft(system))
    assert system["db"].writes == []


def test_import_batch_is_atomic_on_second_row_collision(system):
    with pytest.raises(ConflictError, match="wykładowca"):
        system["schedules"].create_many("token", [draft(system), draft(system)])
    assert not system["db"].tables.get("SemesterOrganizationSchedules")
    assert system["db"].rollbacks == 1
    assert system["db"].commits == 0


def test_import_preview_detects_internal_collision_without_writing(system):
    with pytest.raises(ConflictError, match="wykładowca"):
        system["schedules"].validate_many("token", [draft(system), draft(system)])
    assert system["db"].writes == []


def test_adjacent_single_hours_saved_as_two_real_rows(system):
    created = system["schedules"].create_many("token", [draft(system), draft(system, DailyScheduleId=system["hour2"]["Id"])])
    assert len(created) == 2
    assert len(system["db"].tables["SemesterOrganizationSchedules"]) == 2
    assert system["db"].commits == 1


def test_batch_checks_required_planning_hours_without_counting_other_groups(system):
    system["ss"]["NumberOfPlanningHours"] = 1
    with pytest.raises(ConflictError, match="liczbę godzin"):
        system["schedules"].create_many("token", [draft(system), draft(system, DailyScheduleId=system["hour2"]["Id"])])
    assert not system["db"].tables.get("SemesterOrganizationSchedules")


def test_cancel_removes_conflict_and_makeup_reuses_original_record(system):
    hour = system["schedules"].create("token", draft(system))
    canceled = system["schedules"].cancel("token", hour["Id"], hour["Timestamp"])
    assert canceled["IsCanceled"]
    other = system["schedules"].create("token", draft(system))
    made_up = system["schedules"].make_up("token", canceled["Id"], draft(system, SemesterCalendarDateId=system["day2"]["Id"]), canceled["Timestamp"])
    assert made_up["Id"] == canceled["Id"]
    assert made_up["IsMoved"] and not made_up["IsCanceled"]
    assert len(system["db"].tables["SemesterOrganizationSchedules"]) == 2
    assert other["Id"] != made_up["Id"]


def test_completion_cannot_apply_to_canceled_hour(system):
    hour = system["schedules"].create("token", draft(system))
    system["schedules"].cancel("token", hour["Id"], hour["Timestamp"])
    with pytest.raises(ValidationError, match="odwołanej"):
        system["schedules"].complete("token", hour["Id"], hour["Timestamp"])


def test_version_conflict_does_not_silently_overwrite(system):
    hour = system["schedules"].create("token", draft(system))
    old_timestamp = hour["Timestamp"]
    system["schedules"].complete("token", hour["Id"], old_timestamp)
    with pytest.raises(ConflictError):
        system["schedules"].update("token", hour["Id"], draft(system), old_timestamp)


@pytest.mark.parametrize("field,change", [
    ("day", {"Date": datetime(2027, 2, 2)}),
    ("day", {"SemesterCalendarId": str(uuid4())}),
    ("hour", {"DailyScheduleTypeId": str(uuid4())}),
    ("person", {"IsActive": False}),
    ("hour", {"EndHour": "09:30"}),
])
def test_invalid_calendar_teacher_or_multi_hour_slot_rejected_before_insert(system, field, change):
    system[field].update(change)
    with pytest.raises(ValidationError):
        system["schedules"].create("token", draft(system))
    assert system["db"].writes == []


def test_readiness_requires_membership_and_all_subject_group_assignments_but_not_students(system):
    report = system["organizations"].readiness("token", system["semester"]["Id"])
    assert report.ready
    assert "GroupMembers" not in system["db"].tables
    system["db"].tables["LectureGroupsElements"] = []
    report = system["organizations"].readiness("token", system["semester"]["Id"])
    assert not report.ready
    assert any("przypisanych aktywnych grup" in issue for issue in report.issues)


def test_finalization_refuses_missing_organization_and_writes_no_state(system):
    system["db"].tables["SemesterOrganizations"] = []
    with pytest.raises(ValidationError, match="Nie można zakończyć"):
        system["organizations"].finalize("token", system["semester"]["Id"])
    assert system["db"].writes == []


def test_finalization_checks_calendar_bounds_and_program_links(system):
    system["day"]["Date"] = datetime(2027, 1, 1)
    system["subject"]["ProgramsOfStudyId"] = None
    report = system["organizations"].readiness("token", system["semester"]["Id"])
    assert not report.ready
    assert any("poza zakresem" in issue for issue in report.issues)
    assert any("programu" in issue for issue in report.issues)


def test_organization_rejects_duplicate_and_cross_studies_group(system):
    values = {key: system["org"][key] for key in org_module.FIELDS}
    with pytest.raises(ConflictError):
        system["organizations"].create("token", values)
    system["db"].tables["Groups"][0]["StudiesId"] = str(uuid4())
    with pytest.raises(ValidationError, match="nie odpowiada"):
        system["organizations"].create("token", values)


def test_both_primary_and_additional_teacher_have_conflict_check(system):
    person2 = system["db"].add("Persons", FirstName1="Anna", LastName="Nowak")
    teacher2 = system["db"].add("Teachers", PersonId=person2["Id"])
    system["schedules"].create("token", draft(system, TeacherId=teacher2["Id"]))
    another_org = system["db"].add("SemesterOrganizations", SubjectsSemesterId=system["ss"]["Id"],
                                  LectureGroupId=system["lg"]["Id"], TeacherId=teacher2["Id"])
    with pytest.raises(ConflictError, match="wykładowca"):
        system["schedules"].create("token", draft(system, SemesterOrganizationId=another_org["Id"]))


def test_shared_student_group_conflicts_even_between_different_lecture_groups(system):
    system["schedules"].create("token", draft(system))
    person2 = system["db"].add("Persons", FirstName1="Anna", LastName="Nowak")
    teacher2 = system["db"].add("Teachers", PersonId=person2["Id"])
    lg2 = system["db"].add("LectureGroups", Name="Laboratorium", SemesterId=system["semester"]["Id"],
         SemesterCalendarId=system["calendar"]["Id"], DailyScheduleTypeId=system["daily_type"]["Id"])
    system["db"].add("LectureGroupsElements", LectureGroupId=lg2["Id"], GroupId=system["group"]["Id"])
    org2 = system["db"].add("SemesterOrganizations", SubjectsSemesterId=system["ss"]["Id"], LectureGroupId=lg2["Id"], TeacherId=teacher2["Id"])
    with pytest.raises(ConflictError, match="Grupa"):
        system["schedules"].create("token", draft(system, SemesterOrganizationId=org2["Id"], LectureRoomName="102"))


def test_supplied_import_transaction_does_not_open_nested_commit(system):
    system["schedules"].create_many("token", [draft(system)], connection=system["db"])
    assert system["db"].commits == 0
    assert system["auth"].checks[-1][2] is system["db"]


def test_room_conflict_detected_for_unrelated_group_and_teacher(system):
    system["schedules"].create("token", draft(system, LectureRoomName=" 101 "))
    db = system["db"]
    group2 = db.add("Groups", Name="Inna grupa", SemesterId=system["semester"]["Id"],
                    StudiesId=system["studies"]["Id"], SemesterNumber=1, SpecializationId=None)
    lg2 = db.add("LectureGroups", Name="Inny wykład", SemesterId=system["semester"]["Id"],
                SemesterCalendarId=system["calendar"]["Id"], DailyScheduleTypeId=system["daily_type"]["Id"])
    db.add("LectureGroupsElements", LectureGroupId=lg2["Id"], GroupId=group2["Id"])
    person2 = db.add("Persons", FirstName1="Anna", LastName="Nowak")
    teacher2 = db.add("Teachers", PersonId=person2["Id"])
    org2 = db.add("SemesterOrganizations", SubjectsSemesterId=system["ss"]["Id"], LectureGroupId=lg2["Id"], TeacherId=teacher2["Id"])
    with pytest.raises(ConflictError, match="sala"):
        system["schedules"].create("token", draft(system, SemesterOrganizationId=org2["Id"], LectureRoomName="101"))


def test_separate_lecture_groups_each_require_full_number_of_planning_hours(system):
    system["ss"]["NumberOfPlanningHours"] = 1
    system["schedules"].create("token", draft(system))
    db = system["db"]
    group2 = db.add("Groups", Name="Inna grupa", SemesterId=system["semester"]["Id"],
                    StudiesId=system["studies"]["Id"], SemesterNumber=1, SpecializationId=None)
    lg2 = db.add("LectureGroups", Name="Inny wykład", SemesterId=system["semester"]["Id"],
                SemesterCalendarId=system["calendar"]["Id"], DailyScheduleTypeId=system["daily_type"]["Id"])
    db.add("LectureGroupsElements", LectureGroupId=lg2["Id"], GroupId=group2["Id"])
    org2 = db.add("SemesterOrganizations", SubjectsSemesterId=system["ss"]["Id"], LectureGroupId=lg2["Id"], TeacherId=system["teacher"]["Id"])
    created = system["schedules"].create("token", draft(system, SemesterOrganizationId=org2["Id"], SemesterCalendarDateId=system["day2"]["Id"]))
    assert created["IsActive"]


def test_organization_without_subject_records_produces_actionable_readiness(system):
    system["ss"]["SubjectId"] = None
    # Broken external FK records should be reported instead of producing a crash.
    system["db"].tables["SemesterOrganizations"] = []
    report = system["organizations"].readiness("token", system["semester"]["Id"])
    assert not report.ready
    assert any("aktywnego przedmiotu" in issue for issue in report.issues)


@pytest.mark.parametrize("field,changes,expected", [
    ("subject", {"SubjectTypeId": None}, "typu przedmiotu"),
    ("lg", {"LectureGroupTypeId": None}, "Typ grupy wykładowej"),
    ("program", {"StartDate": datetime(2027, 1, 1)}, "nie obowiązuje"),
    ("ss", {"SemesterNumber": 2}, "niezgodny z programem"),
    ("subject_type", {"IsActive": False}, "typu przedmiotu"),
    ("lecture_type", {"IsActive": False}, "Typ grupy wykładowej"),
])
def test_finalization_revalidates_external_curriculum_and_types(system, field, changes, expected):
    system[field].update(changes)
    report = system["organizations"].readiness("token", system["semester"]["Id"])
    assert not report.ready
    assert any(expected in issue for issue in report.issues)


def test_active_schedule_prevents_deactivating_or_reassigning_organization(system):
    system["schedules"].create("token", draft(system))
    values = {key: system["org"][key] for key in org_module.FIELDS}
    with pytest.raises(ConflictError, match="aktywne godziny"):
        system["organizations"].update("token", system["org"]["Id"], {**values, "IsActive": False}, system["org"]["Timestamp"])
    assert system["db"].tables["SemesterOrganizations"][0]["IsActive"]
