"""Streamlit renderuje wymagane pola i nie przyznaje akcji w widoku do odczytu."""

from datetime import datetime

from streamlit.testing.v1 import AppTest

from academic.organization.models import ReadinessReport


class OrganizationFixture:
    def __init__(self, writable=True):
        self.writable = writable

    def can(self, token, action):
        return action == "view" or self.writable

    def lookups(self, token, semester_id=None):
        return {"Semesters": [{"Id": "s", "Name": "Semestr źródłowy", "IsActive": True, "IsActual": True}],
                "SubjectsSemesters": [{"Id": "ss", "SubjectName": "Przedmiot źródłowy"}],
                "LectureGroups": [{"Id": "lg", "Name": "Grupa źródłowa"}],
                "Teachers": [{"Id": "t", "TeacherName": "Osoba źródłowa"}]}

    def list(self, token, semester_id):
        return []

    def readiness(self, token, semester_id):
        return ReadinessReport(semester_id, ("Brak przydziałów.",), 1, 1, 0)


class ScheduleFixture(OrganizationFixture):
    def lookups(self, token, semester_id=None, organization_id=None):
        return {**super().lookups(token, semester_id),
                "Organizations": [{"Id": "o", "SubjectName": "Przedmiot źródłowy", "LectureGroupName": "Grupa źródłowa"}],
                "Dates": [{"Id": "d", "Date": datetime(2026, 2, 2), "Number": 1}],
                "Hours": [{"Id": "h", "StartHour": "08:00", "EndHour": "08:45", "Number": 1}]}

    def balance(self, token, semester_id):
        return [{"SubjectName": "Przedmiot źródłowy", "LectureGroupName": "Grupa źródłowa", "required": 30,
                 "planned": 0, "completed": 0, "canceled": 0, "to_complete": 0, "unassigned": 30, "excess": 0}]


def organization_app(service):
    from academic.ui.organization import render_organization
    render_organization(service, "session")


def schedule_app(service):
    from academic.ui.scheduling import render_scheduling
    render_scheduling(service, "session")


def test_organization_editor_contains_all_ui_fields():
    app = AppTest.from_function(organization_app, args=(OrganizationFixture(),)).run()
    app.button(key="organization_add").click().run()
    assert not app.exception
    assert {item.label for item in app.selectbox} >= {"Semestr", "Przedmiot w semestrze", "Grupa wykładowa", "Wykładowca"}
    assert app.toggle[0].label == "Aktywny przydział"
    assert app.text_input[0].label == "Znacznik czasu (Timestamp)"
    assert app.text_input[0].disabled


def test_readonly_organization_view_hides_write_actions():
    app = AppTest.from_function(organization_app, args=(OrganizationFixture(writable=False),)).run()
    assert not app.exception
    assert not app.button


def test_schedule_editor_contains_required_fields_and_balance():
    app = AppTest.from_function(schedule_app, args=(ScheduleFixture(),)).run()
    app.button(key="schedule_add").click().run()
    app.selectbox(key="schedule_editor_org").select("o").run()
    assert not app.exception
    assert {item.label for item in app.selectbox} >= {"Organizacja semestru", "Data / zjazd", "Godzina", "Dodatkowy wykładowca"}
    assert {item.label for item in app.toggle} == {"Czy wykonane", "Czy odwołane", "Czy aktywne"}
    assert {item.label for item in app.text_input} == {"Sala", "Znacznik czasu (Timestamp)"}
    assert len(app.dataframe) == 1


def test_readonly_schedule_view_hides_add_button():
    app = AppTest.from_function(schedule_app, args=(ScheduleFixture(writable=False),)).run()
    assert not app.exception
    assert not app.button
