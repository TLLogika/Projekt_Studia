"""Unambiguous matching against a snapshot of existing, active database rows."""

from datetime import date, datetime

from academic.errors import ValidationError
from .models import ImportContext, ImportIssue, ImportPreview, ParsedRow
from .parser import parse_time, split_slots


def key(value) -> str:
    return str(value or "").strip().casefold()


def unique(rows: list[dict], label: str) -> dict:
    # Join rows may contain repetitions of the same actual entity.
    candidates = {key(row["Id"]): row for row in rows}
    if not candidates:
        raise ValidationError(f"Nie znaleziono aktywnego rekordu: {label}.")
    if len(candidates) > 1:
        raise ValidationError(f"Niejednoznaczne dopasowanie: {label}. Wskaż ID istniejącego rekordu.")
    return next(iter(candidates.values()))


def matching(rows, references, prefix, label, name_fields=("Name",)):
    identifier, name = references.get(prefix + "_id"), references.get(prefix + "_name")
    found = list(rows)
    if identifier:
        found = [row for row in found if key(row["Id"]) == key(identifier)]
    if name:
        found = [row for row in found if any(key(row.get(field)) == key(name) for field in name_fields)]
    if not identifier and not name:
        raise ValidationError(f"W wierszu nie wskazano: {label}.")
    return unique(found, label)


def _day(value) -> date:
    return value.date() if isinstance(value, datetime) else value


def resolve_row(row: ParsedRow, context: ImportContext, snapshot: dict) -> tuple[dict, ...]:
    refs = row.references
    organizations = list(snapshot["organizations"])
    for column, value in (
        ("ProgramId", context.program_id), ("StudiesId", context.studies_id),
        ("SemesterNumber", context.semester_number), ("SpecializationId", context.specialization_id),
    ):
        if value is not None:
            organizations = [org for org in organizations if key(org.get(column)) == key(value)]

    if refs.get("organization_id"):
        organizations = [org for org in organizations if key(org["Id"]) == key(refs["organization_id"])]

    if refs.get("subject_id") or refs.get("subject_name"):
        if refs.get("subject_name") and not refs.get("subject_id") and not refs.get("organization_id"):
            if not context.program_id or not context.studies_id or context.semester_number is None:
                raise ValidationError("Dopasowanie przedmiotu po nazwie wymaga programu, studiów i numeru semestru studiów.")
        subjects = [{**org, "Id": org["SubjectId"], "Name": org["SubjectName"]} for org in organizations]
        subject = matching(subjects, refs, "subject", "przedmiot")
        organizations = [org for org in organizations if key(org["SubjectId"]) == key(subject["Id"])]

    if refs.get("lecture_group_id") or refs.get("lecture_group_name"):
        groups = [{**org, "Id": org["LectureGroupId"], "Name": org["LectureGroupName"]} for org in organizations]
        group = matching(groups, refs, "lecture_group", "grupa wykładowa")
        organizations = [org for org in organizations if key(org["LectureGroupId"]) == key(group["Id"])]
    if refs.get("group_id") or refs.get("group_name"):
        group = matching(snapshot["groups"], refs, "group", "grupa studencka")
        lecture_ids = {key(link["LectureGroupId"]) for link in snapshot["group_links"] if key(link["GroupId"]) == key(group["Id"])}
        organizations = [org for org in organizations if key(org["LectureGroupId"]) in lecture_ids]

    teacher = None
    if refs.get("teacher_id") or refs.get("teacher_name"):
        teacher = matching(snapshot["teachers"], refs, "teacher", "wykładowca", ("Name", "ShortName"))
        # A teacher column names the existing organizational assignment. A replacement
        # teacher requires the explicit organization ID to avoid creating a new assignment.
        if not refs.get("organization_id"):
            organizations = [org for org in organizations if key(org.get("TeacherId")) == key(teacher["Id"])]
    organization = unique(organizations, "organizacja semestru")
    if not organization.get("SemesterCalendarId") or not organization.get("DailyScheduleTypeId"):
        raise ValidationError("Grupa wykładowa nie ma kalendarza semestru lub typu planu dnia.")

    dates = [item for item in snapshot["dates"] if key(item["SemesterCalendarId"]) == key(organization["SemesterCalendarId"])]
    if refs.get("calendar_date_id"):
        dates = [item for item in dates if key(item["Id"]) == key(refs["calendar_date_id"])]
    if row.day:
        dates = [item for item in dates if _day(item["Date"]) == row.day]
    calendar_date = unique(dates, "dzień kalendarza grupy wykładowej")

    slots = [item for item in snapshot["slots"] if key(item["DailyScheduleTypeId"]) == key(organization["DailyScheduleTypeId"])]
    if refs.get("slot_id"):
        selected = unique([item for item in slots if key(item["Id"]) == key(refs["slot_id"])], "slot planu dnia")
        slots = list(split_slots(parse_time(selected["StartHour"]), parse_time(selected["EndHour"]), [selected]))
    else:
        slots = list(split_slots(row.start, row.end, slots))
    result = []
    for slot in slots:
        result.append({
            "SemesterOrganizationId": str(organization["Id"]),
            "SemesterCalendarDateId": str(calendar_date["Id"]),
            "DailyScheduleId": str(slot["Id"]),
            "TeacherId": str(teacher["Id"]) if teacher and key(teacher["Id"]) != key(organization.get("TeacherId")) else None,
            "LectureRoomName": row.room or None,
            "IsActive": True,
            "SourceRow": row.number,
            "Date": _day(calendar_date["Date"]),
            "StartHour": slot["StartHour"], "EndHour": slot["EndHour"],
            "SubjectName": organization["SubjectName"],
            "LectureGroupName": organization["LectureGroupName"],
            "TeacherName": teacher["Name"] if teacher else organization.get("TeacherName", ""),
        })
    return tuple(result)


def resolve_rows(rows: tuple[ParsedRow, ...], context: ImportContext, snapshot: dict,
                 parse_issues: tuple[ImportIssue, ...] = (), source_rows: int | None = None) -> ImportPreview:
    entries, issues, seen = [], list(parse_issues), set()
    for row in rows:
        try:
            additions = resolve_row(row, context, snapshot)
            row_keys = {(key(item["SemesterOrganizationId"]), key(item["SemesterCalendarDateId"]), key(item["DailyScheduleId"])) for item in additions}
            if seen & row_keys:
                raise ValidationError("W pliku powtarza się ta sama organizacja, data i godzina zajęć.")
            seen.update(row_keys)
            entries.extend(additions)
        except ValidationError as error:
            issues.append(ImportIssue(row.number, str(error)))
    return ImportPreview(tuple(entries), tuple(issues), source_rows if source_rows is not None else len(rows))
