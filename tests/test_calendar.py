"""Personal calendar filters must stay in the database query itself."""

from contextlib import contextmanager
from datetime import date, datetime
from types import SimpleNamespace

import pytest

from academic.auth.models import AuthorizationError
from academic.errors import ValidationError
from academic.views.calendar import CalendarService, calendar_sql


class DB:
    @contextmanager
    def connection(self):
        yield object()


class Auth:
    def __init__(self, allowed):
        self.allowed = allowed
        self.required = []

    def can(self, token, code, action):
        return code in self.allowed

    def require(self, token, code, action, connection=None):
        self.required.append((code, action))
        if code not in self.allowed:
            raise AuthorizationError("Brak uprawnień")

    def get_user(self, token):
        return SimpleNamespace(person_id="person-from-session")


def capture(monkeypatch, rows=None):
    calls = []

    def query(connection, sql, params=()):
        calls.append((sql, params))
        return rows or []

    monkeypatch.setattr("academic.views.calendar.query", query)
    return calls


def test_teacher_query_includes_primary_and_additional_only_current_session(monkeypatch):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({"calendar.teacher"}))
    service.list("session", "teacher", date(2026, 10, 7), date(2026, 10, 7))
    sql, params = calls[0]
    assert "teacher.PersonId=? OR additional_teacher.PersonId=?" in sql
    assert params[:2] == ("person-from-session", "person-from-session")
    assert params[-1] == datetime(2026, 10, 8)


def test_student_query_checks_group_membership_valid_at_slot_time_and_links(monkeypatch):
    calls = capture(monkeypatch)
    auth = Auth({"calendar.student"})
    service = CalendarService(DB(), auth)
    service.list("session", "student", date(2026, 10, 7), date(2026, 10, 7))
    sql, params = calls[0]
    assert "org.GroupMembers" in sql and "org.LectureGroupsElements" in sql
    assert "member.StartDate<=clock.EventStart" in sql
    assert "member.EndDate>clock.EventStart" in sql
    assert "clock.EventStart IS NOT NULL" in sql
    assert "clock.EventStart IS NULL" not in sql
    assert "student.GroupId" not in sql
    assert params[0] == "person-from-session"
    assert auth.required == [("calendar.student", "view")]


def test_scope_selection_cannot_escalate_student_to_dean(monkeypatch):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({"calendar.student"}))
    assert service.scopes("session") == ("student",)
    with pytest.raises(AuthorizationError):
        service.list("session", "dean", date(2026, 10, 7), date(2026, 10, 7))
    assert not calls


def test_scope_cannot_be_injected_into_sql(monkeypatch):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({"calendar.dean"}))
    with pytest.raises(AuthorizationError):
        service.list("session", "dean' OR 1=1", date(2026, 10, 7), date(2026, 10, 7))
    assert not calls


def test_personal_semester_selector_uses_same_scope_filter(monkeypatch):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({"calendar.teacher"}))
    service.semesters("session", "teacher")
    assert "teacher.PersonId=? OR additional_teacher.PersonId=?" in calls[0][0]
    assert calls[0][1] == ("person-from-session", "person-from-session")


def test_invalid_existing_time_is_reported_instead_of_a_fabricated_calendar_event(monkeypatch):
    capture(monkeypatch, [{"StartHour": "nieznana", "EndHour": "09:30"}])
    service = CalendarService(DB(), Auth({"calendar.dean"}))
    with pytest.raises(ValidationError, match="niepoprawnych godzinach"):
        service.list("session", "dean", date(2026, 10, 7), date(2026, 10, 7))


def test_invalid_range_does_not_query_database(monkeypatch):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({"calendar.dean"}))
    with pytest.raises(ValidationError):
        service.list("session", "dean", date(2026, 10, 8), date(2026, 10, 7))
    assert not calls


@pytest.mark.parametrize("scope,code,person_params", [
    ("dean", "calendar.dean", ()),
    ("teacher", "calendar.teacher", ("person-from-session", "person-from-session")),
    ("student", "calendar.student", ("person-from-session",)),
])
def test_group_filter_only_narrows_existing_authorized_scope(monkeypatch, scope, code, person_params):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({code}))
    group_id = "00000000-0000-0000-0000-000000000001"
    semester_id = "00000000-0000-0000-0000-000000000002"
    service.list("session", scope, date(2026, 10, 7), date(2026, 10, 7), semester_id,
                 lecture_group_id=group_id)
    sql, params = calls[0]
    assert "AND ss.SemesterId=? AND orgn.LectureGroupId=?" in sql
    assert params[:len(person_params)] == person_params
    assert params[-2:] == (semester_id, group_id)
    if scope == "teacher":
        assert "teacher.PersonId=? OR additional_teacher.PersonId=?" in sql
    if scope == "student":
        assert "student.PersonId=?" in sql and "member.EndDate>clock.EventStart" in sql


def test_group_selector_is_bound_to_personal_scope_and_semester(monkeypatch):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({"calendar.student"}))
    semester_id = "00000000-0000-0000-0000-000000000002"
    service.lecture_groups("session", "student", semester_id)
    sql, params = calls[0]
    assert "SELECT DISTINCT lg.Id,lg.Name,lg.SemesterId" in sql
    assert "student.PersonId=?" in sql and "member.StartDate<=clock.EventStart" in sql
    assert "AND ss.SemesterId=?" in sql
    assert params == ("person-from-session", semester_id)


def test_group_selector_cannot_escalate_student_to_dean(monkeypatch):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({"calendar.student"}))
    with pytest.raises(AuthorizationError):
        service.lecture_groups("session", "dean")
    assert not calls


def test_group_filter_rejects_invalid_uuid_without_calendar_query(monkeypatch):
    calls = capture(monkeypatch)
    service = CalendarService(DB(), Auth({"calendar.dean"}))
    with pytest.raises(ValidationError):
        service.list("session", "dean", date(2026, 10, 7), date(2026, 10, 7),
                     lecture_group_id="' OR 1=1")
    assert not calls
