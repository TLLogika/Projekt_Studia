"""Przepływ między modułami działa na jednej izolowanej bazie w pamięci."""

from datetime import datetime

import pytest

from academic.catalogs.service import CatalogService
import academic.catalogs.service as catalogs_module
from academic.errors import ConflictError
from academic.importing.models import ImportContext, ImportMapping
from academic.importing.service import ImportService
import academic.importing.service as importing_module
import academic.scheduling.service as schedule_module
from test_organization_schedule import system, draft


@pytest.fixture
def workflow(system, monkeypatch):
    # Podmieniamy wyłącznie I/O; logika katalogów, organizacji, importu i godzin
    # korzysta z publicznych metod tych samych usług co aplikacja.
    for name in ("get_record", "list_records", "insert_record", "update_record", "require_active_semester"):
        monkeypatch.setattr(catalogs_module, name, system[{"get_record": "get", "list_records": "listed",
            "insert_record": "insert", "update_record": "update", "require_active_semester": "active_semester"}[name]])
    monkeypatch.setattr(catalogs_module, "acquire_write_lock", lambda *args: None)
    original_columns = system["db"].columns

    def columns(table, **kwargs):
        if table != "SubjectsSemesters":
            return original_columns(table, **kwargs)
        fields = {"SubjectId": "uniqueidentifier", "SemesterId": "uniqueidentifier", "NumberOfHybridHours": "tinyint",
                  "NumberOfPlanningHours": "tinyint", "SemesterNumber": "int", "IsActive": "bit"}
        return {name: {"DATA_TYPE": kind, "IS_NULLABLE": "NO"} for name, kind in fields.items()}

    monkeypatch.setattr(system["db"], "columns", columns)
    system["program"]["StartDate"] = datetime(2025, 1, 1)
    catalog = CatalogService(system["db"], system["auth"])

    def query_balance(conn, sql, params=()):
        if "GROUP BY o.SubjectsSemesterId" in sql:
            result, seen = [], set()
            for organization in system["listed"](conn, "SemesterOrganizations", active_only=True):
                ss = system["get"](conn, "SubjectsSemesters", organization["SubjectsSemesterId"])
                if str(ss["SemesterId"]) != str(params[0]) or not ss["IsActive"]:
                    continue
                pair = ss["Id"], organization["LectureGroupId"]
                if pair in seen:
                    continue
                seen.add(pair)
                result.append({"SubjectsSemesterId": ss["Id"], "LectureGroupId": pair[1],
                    "NumberOfPlanningHours": ss["NumberOfPlanningHours"],
                    "SubjectName": system["get"](conn, "Subjects", ss["SubjectId"])["Name"],
                    "LectureGroupName": system["get"](conn, "LectureGroups", pair[1])["Name"]})
            return result
        if "SELECT ss.Id AS SubjectsSemesterId" in sql:
            return [{"SubjectsSemesterId": ss["Id"], "NumberOfPlanningHours": ss["NumberOfPlanningHours"],
                    "SubjectName": system["get"](conn, "Subjects", ss["SubjectId"])["Name"]}
                    for ss in system["listed"](conn, "SubjectsSemesters", {"SemesterId": params[0]}, True)]
        raise AssertionError(sql)

    monkeypatch.setattr(schedule_module, "query", query_balance)
    return {**system, "catalogs": catalog}


def test_generate_organize_plan_cancel_makeup_and_complete(workflow):
    w = workflow
    new_subject = w["db"].add("Subjects", Name="Drugi przedmiot", NumberOfHours=2, Semester=1,
        StudiesId=w["studies"]["Id"], ProgramsOfStudyId=w["program"]["Id"],
        SubjectTypeId=w["subject_type"]["Id"], SpecializationId=None)
    generated = w["catalogs"].generate_subjects("token", w["semester"]["Id"], w["program"]["Id"], w["studies"]["Id"], 1)
    assert len(generated.created) == 1 and generated.already_existing == 1
    ss = generated.created[0]
    assert ss["SubjectId"] == new_subject["Id"]
    assert ss["NumberOfPlanningHours"] == 2
    assert not w["organizations"].readiness("token", w["semester"]["Id"]).ready
    organization = w["organizations"].create("token", {"SubjectsSemesterId": ss["Id"],
        "LectureGroupId": w["lg"]["Id"], "TeacherId": w["teacher"]["Id"], "IsActive": True})
    assert w["organizations"].finalize("token", w["semester"]["Id"]).ready
    inputs = [draft(w, SemesterOrganizationId=organization["Id"]),
              draft(w, SemesterOrganizationId=organization["Id"], DailyScheduleId=w["hour2"]["Id"])]
    slots = w["schedules"].create_many("token", inputs)
    first = w["schedules"].complete("token", slots[0]["Id"], slots[0]["Timestamp"])
    canceled = w["schedules"].cancel("token", slots[1]["Id"], slots[1]["Timestamp"])
    balance = next(row for row in w["schedules"].balance("token", w["semester"]["Id"]) if row["SubjectsSemesterId"] == ss["Id"])
    assert (balance["required"], balance["planned"], balance["completed"], balance["canceled"], balance["unassigned"]) == (2, 1, 1, 1, 1)
    made_up = w["schedules"].make_up("token", canceled["Id"],
        draft(w, SemesterOrganizationId=organization["Id"], SemesterCalendarDateId=w["day2"]["Id"]), canceled["Timestamp"])
    assert made_up["Id"] == canceled["Id"] and made_up["IsMoved"]
    balance = next(row for row in w["schedules"].balance("token", w["semester"]["Id"]) if row["SubjectsSemesterId"] == ss["Id"])
    assert (balance["planned"], balance["completed"], balance["unassigned"], balance["to_complete"]) == (2, 1, 0, 1)
    assert first["IsComplited"]


def test_actual_import_services_share_same_atomic_schedule_transaction(workflow, monkeypatch):
    w = workflow
    importer = ImportService(w["db"], w["auth"], w["schedules"])
    organization = {"Id": w["org"]["Id"], "TeacherId": w["teacher"]["Id"], "TeacherName": "Jan Kowalski",
        "LectureGroupId": w["lg"]["Id"], "LectureGroupName": "Wykład", "SubjectsSemesterId": w["ss"]["Id"],
        "SubjectId": w["subject"]["Id"], "SubjectName": "Przedmiot", "ProgramId": w["program"]["Id"],
        "StudiesId": w["studies"]["Id"], "SemesterNumber": 1, "SpecializationId": None,
        "SemesterCalendarId": w["calendar"]["Id"], "DailyScheduleTypeId": w["daily_type"]["Id"]}

    def snapshot(connection, context):
        return {"organizations": [organization], "dates": w["db"].tables["SemesterCalendarDates"],
                "slots": w["db"].tables["DailySchedules"], "groups": w["db"].tables["Groups"],
                "group_links": w["db"].tables["LectureGroupsElements"], "teachers": []}

    monkeypatch.setattr(importer, "_snapshot", snapshot)
    monkeypatch.setattr(importing_module, "acquire_write_lock", lambda *args: None)
    content = f"organizacja;data;godziny;sala\n{w['org']['Id']};2026-02-02;08:00-09:30;101\n".encode()
    mapping = ImportMapping({"organization_id": "organizacja", "date": "data", "time_range": "godziny", "room": "sala"})
    preview = importer.preview("token", content, "plan.csv", context=ImportContext(w["semester"]["Id"]), mapping=mapping)
    assert preview.ready and len(preview.entries) == 2
    assert w["db"].writes == []
    commits = w["db"].commits
    created = importer.commit("token", preview)
    assert len(created) == 2 and w["db"].commits == commits + 1
    before = len(w["db"].tables["SemesterOrganizationSchedules"])
    with pytest.raises(ConflictError):
        importer.commit("token", preview)
    assert len(w["db"].tables["SemesterOrganizationSchedules"]) == before
