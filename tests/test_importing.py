"""Offline boundary tests only; no writes or sample records in the real database."""

from datetime import date, datetime, time
from io import BytesIO
from contextlib import contextmanager

import pytest

from academic.errors import ConflictError, ValidationError
from academic.importing.models import ImportContext, ImportMapping, ParsedRow
from academic.importing.parser import parse_rows, parse_time, split_slots
from academic.importing.reader import read_table, sheet_names
from academic.importing.resolver import resolve_rows
from academic.importing.service import ImportService

IDS = {name: f"00000000-0000-0000-0000-{index:012d}" for index, name in enumerate(
    ("semester", "program", "studies", "org", "subject", "lecture", "teacher", "calendar", "day", "slot1", "slot2", "group"), 1)}


def source(text):
    return read_table(text.encode(), "plan.csv")


def snapshot():
    return {
        "organizations": [{"Id": IDS["org"], "TeacherId": IDS["teacher"], "LectureGroupId": IDS["lecture"],
                           "SubjectId": IDS["subject"], "SubjectName": "Przedmiot istniejący", "LectureGroupName": "Grupa istniejąca",
                           "ProgramId": IDS["program"], "StudiesId": IDS["studies"], "SemesterNumber": 1,
                           "SpecializationId": None, "SemesterCalendarId": IDS["calendar"], "DailyScheduleTypeId": "type",
                           "TeacherName": "Osoba testu jednostkowego"}],
        "dates": [{"Id": IDS["day"], "Date": datetime(2026, 10, 7), "SemesterCalendarId": IDS["calendar"]}],
        "slots": [{"Id": IDS["slot1"], "StartHour": "08:00", "EndHour": "08:45", "DailyScheduleTypeId": "type"},
                  {"Id": IDS["slot2"], "StartHour": "08:45", "EndHour": "09:30", "DailyScheduleTypeId": "type"}],
        "teachers": [{"Id": IDS["teacher"], "Name": "Osoba testu jednostkowego", "ShortName": "OT"}],
        "groups": [{"Id": IDS["group"], "Name": "Grupa studencka istniejąca"}],
        "group_links": [{"GroupId": IDS["group"], "LectureGroupId": IDS["lecture"]}],
    }


def mapped_source():
    table = source(f"organizacja;data;godziny;sala\n{IDS['org']};2026-10-07;08:00–09:30;101\n")
    mapping = ImportMapping({"organization_id": "organizacja", "date": "data", "time_range": "godziny", "room": "sala"})
    return table, mapping


def test_csv_does_not_infer_delimiter_or_header():
    table = read_table(b"preamble\ndate,start,end\n2026-10-07,08:00,08:45\n", "plan.csv", header_row=2, delimiter=",")
    assert table.columns == ("date", "start", "end")
    assert table.rows[0].number == 3
    assert table.rows[0].values["start"] == "08:00"


@pytest.mark.parametrize("text", ["date;date\na;b\n", "date;\na;b\n", "\n"])
def test_bad_headers_are_rejected(text):
    with pytest.raises(ValidationError):
        source(text)


def test_xlsx_uses_selected_sheet_header_and_native_date_time():
    from openpyxl import Workbook
    workbook = Workbook()
    workbook.active.title = "Inny arkusz"
    sheet = workbook.create_sheet("Plan")
    sheet.append(["Tytuł"])
    sheet.append(["Organizacja", "Data", "Początek", "Koniec"])
    sheet.append([IDS["org"], date(2026, 10, 7), time(8), time(8, 45)])
    buffer = BytesIO()
    workbook.save(buffer)
    assert sheet_names(buffer.getvalue()) == ("Inny arkusz", "Plan")
    with pytest.raises(ValidationError, match="arkusz"):
        read_table(buffer.getvalue(), "plan.xlsx")
    table = read_table(buffer.getvalue(), "plan.xlsx", sheet="Plan", header_row=2)
    rows, errors = parse_rows(table, ImportMapping({"organization_id": "Organizacja", "date": "Data", "start": "Początek", "end": "Koniec"}))
    assert not errors
    assert rows[0].number == 3
    assert rows[0].day == date(2026, 10, 7)
    assert rows[0].start == time(8)


def test_parser_retains_source_row_errors_and_never_silently_skips_bad_rows():
    table = source(f"org;date;time\n{IDS['org']};2026-10-07;08:00-09:30\n{IDS['org']};07.10.2026;08:00-09:30\nBAD;2026-10-07;08:00-09:30\n")
    rows, errors = parse_rows(table, ImportMapping({"organization_id": "org", "date": "date", "time_range": "time"}))
    assert len(rows) == 1
    assert [error.row for error in errors] == [3, 4]


def test_mapping_requires_date_and_time_and_source_entity():
    table, _ = mapped_source()
    with pytest.raises(ValidationError):
        parse_rows(table, ImportMapping({"room": "sala"}))
    with pytest.raises(ValidationError, match="kilku"):
        parse_rows(table, ImportMapping({"organization_id": "organizacja", "date": "data", "time_range": "godziny", "room": "data"}))


def test_blank_organization_reference_never_selects_only_existing_organization():
    table = source("org;date;time\n;2026-10-07;08:00-08:45\n")
    rows, errors = parse_rows(table, ImportMapping({"organization_id": "org", "date": "date", "time_range": "time"}))
    assert not rows and len(errors) == 1
    assert "brakuje ID organizacji" in errors[0].message


def test_excel_fraction_and_invalid_precision():
    assert parse_time(1 / 3) == time(8)
    assert parse_time(time(8, 45)) == time(8, 45)
    for value in ("25:00", "8.00", 1, time(8, 0, 1), True):
        with pytest.raises(ValidationError):
            parse_time(value)


def test_range_uses_two_existing_45_minute_ids():
    slots = split_slots(time(8), time(9, 30), snapshot()["slots"])
    assert [slot["Id"] for slot in slots] == [IDS["slot1"], IDS["slot2"]]


def test_range_gaps_ambiguities_and_90_minute_slots_are_rejected():
    slots = snapshot()["slots"]
    with pytest.raises(ValidationError, match="pokryć"):
        split_slots(time(8), time(9, 30), slots[:1])
    with pytest.raises(ValidationError, match="kilku"):
        split_slots(time(8), time(9, 30), slots + [{**slots[0], "Id": "duplicate"}])
    with pytest.raises(ValidationError, match="45"):
        split_slots(time(8), time(9, 30), [{"Id": "long", "StartHour": "08:00", "EndHour": "09:30"}])


def test_resolver_splits_only_real_ids_and_no_dictionary_generation():
    table, mapping = mapped_source()
    rows, errors = parse_rows(table, mapping)
    result = resolve_rows(rows, ImportContext(IDS["semester"]), snapshot(), errors)
    assert result.ready
    assert [row["DailyScheduleId"] for row in result.entries] == [IDS["slot1"], IDS["slot2"]]
    assert all(row["SemesterOrganizationId"] == IDS["org"] for row in result.entries)
    assert all(row["TeacherId"] is None for row in result.entries)


def test_subject_name_requires_program_studies_and_semester():
    row = ParsedRow(2, {"subject_name": "Przedmiot istniejący", "lecture_group_name": "Grupa istniejąca"}, date(2026, 10, 7), time(8), time(8, 45), "")
    unresolved = resolve_rows((row,), ImportContext(IDS["semester"]), snapshot())
    assert not unresolved.ready
    assert "programu" in unresolved.issues[0].message
    resolved = resolve_rows((row,), ImportContext(IDS["semester"], IDS["program"], IDS["studies"], 1), snapshot())
    assert resolved.ready


def test_missing_date_and_ambiguous_group_fail_instead_of_guessing():
    table, mapping = mapped_source()
    rows, _ = parse_rows(table, mapping)
    data = snapshot()
    data["dates"] = []
    assert not resolve_rows(rows, ImportContext(IDS["semester"]), data).ready
    data = snapshot()
    data["organizations"].append({**data["organizations"][0], "Id": "other-organization"})
    row = ParsedRow(2, {"subject_id": IDS["subject"], "lecture_group_id": IDS["lecture"]}, date(2026, 10, 7), time(8), time(8, 45), "")
    result = resolve_rows((row,), ImportContext(IDS["semester"]), data)
    assert "Niejednoznaczne" in result.issues[0].message


def test_student_group_matches_lecture_link_and_teacher_primary_not_added_twice():
    row = ParsedRow(2, {"subject_id": IDS["subject"], "group_id": IDS["group"], "teacher_id": IDS["teacher"]}, date(2026, 10, 7), time(8), time(8, 45), "")
    result = resolve_rows((row,), ImportContext(IDS["semester"]), snapshot())
    assert result.ready
    assert result.entries[0]["TeacherId"] is None


def test_duplicate_import_rows_block_whole_preview():
    table, mapping = mapped_source()
    rows, _ = parse_rows(table, mapping)
    result = resolve_rows(rows * 2, ImportContext(IDS["semester"]), snapshot())
    assert not result.ready
    assert "powtarza" in result.issues[0].message


class FakeDB:
    def __init__(self):
        self.connection_object = object()
        self.rollbacks = 0

    @contextmanager
    def connection(self):
        try:
            yield self.connection_object
        except Exception:
            self.rollbacks += 1
            raise


class Auth:
    def __init__(self):
        self.calls = []

    def require(self, token, code, action, connection=None):
        self.calls.append((code, action, connection))


class Schedule:
    def __init__(self):
        self.validations = []
        self.writes = []
        self.fail = False

    def validate_many(self, token, inputs, connection=None):
        self.validations.append((inputs, connection))

    def create_many(self, token, inputs, connection=None):
        if self.fail:
            raise ConflictError("Kolizja")
        self.writes.append((inputs, connection))
        return inputs


def service_fixture(monkeypatch):
    db, auth, schedule = FakeDB(), Auth(), Schedule()
    service = ImportService(db, auth, schedule)
    data = snapshot()
    monkeypatch.setattr(service, "_snapshot", lambda connection, context: data)
    monkeypatch.setattr("academic.importing.service.acquire_write_lock", lambda connection, resource: None)
    table, mapping = mapped_source()
    content = ("organizacja;data;godziny;sala\n" + IDS["org"] + ";2026-10-07;08:00–09:30;101\n").encode()
    result = service.preview("session", content, "plan.csv", context=ImportContext(IDS["semester"]), mapping=mapping)
    return db, auth, schedule, service, data, result


def test_preview_is_read_only_and_commit_uses_same_transaction_and_fresh_resolution(monkeypatch):
    db, auth, schedule, service, data, result = service_fixture(monkeypatch)
    assert result.ready and not schedule.writes
    assert len(schedule.validations) == 1
    created = service.commit("session", result)
    assert len(created) == 2
    assert schedule.writes[0][1] is db.connection_object
    assert all(set(row) == {"SemesterOrganizationId", "SemesterCalendarDateId", "DailyScheduleId", "TeacherId", "LectureRoomName", "IsActive"} for row in created)
    assert ("schedule_import", "create", db.connection_object) in auth.calls


def test_database_change_since_preview_blocks_commit(monkeypatch):
    db, auth, schedule, service, data, result = service_fixture(monkeypatch)
    data["organizations"][0]["SubjectName"] = "Zmieniona nazwa"
    with pytest.raises(ConflictError, match="zmieniły"):
        service.commit("session", result)
    assert not schedule.writes and db.rollbacks == 1


def test_schedule_rejection_rolls_back_import_transaction(monkeypatch):
    db, auth, schedule, service, data, result = service_fixture(monkeypatch)
    schedule.fail = True
    with pytest.raises(ConflictError):
        service.commit("session", result)
    assert db.rollbacks == 1 and not schedule.writes
