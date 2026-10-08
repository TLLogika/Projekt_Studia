"""Exercise the real Streamlit editor with a process-local test service only."""

from dataclasses import replace
from datetime import date

from streamlit.testing.v1 import AppTest

from academic.errors import ConflictError
from academic.semesters.models import Semester


SEMESTER_ID = "71a0cf92-4c76-42bf-8e09-65a2c18b5c28"
ORIGINAL_VERSION = b"\x00\x00\x00\x00\x00\x00\x00\x01"
NEXT_VERSION = b"\x00\x00\x00\x00\x00\x00\x00\x02"


class TestService:
    __test__ = False

    def __init__(self, semesters=()):
        self.semesters = list(semesters)
        self.actions = {"create", "update"}
        self.creates = []
        self.updates = []

    def list(self, token):
        return list(self.semesters)

    def can(self, token, action):
        return action in self.actions

    def create(self, token, data):
        self.creates.append(data)
        saved = Semester(
            SEMESTER_ID, data.name, data.academic_year, data.start_date,
            data.end_date, data.is_actual, data.is_active, NEXT_VERSION,
        )
        self.semesters.append(saved)
        return saved

    def update(self, token, semester_id, data, expected_timestamp):
        self.updates.append((semester_id, data, expected_timestamp))
        current = next(item for item in self.semesters if item.id == semester_id)
        if expected_timestamp != current.timestamp:
            raise ConflictError("Semestr został zmieniony przez inną osobę.")
        saved = replace(
            current, name=data.name, academic_year=data.academic_year,
            start_date=data.start_date, end_date=data.end_date,
            is_actual=data.is_actual, is_active=data.is_active, timestamp=NEXT_VERSION,
        )
        self.semesters = [saved if item.id == semester_id else item for item in self.semesters]
        return saved


def ui_app():
    import streamlit as st
    from academic.ui.semesters import render_semesters

    render_semesters(st.session_state["test_service"], "test_session")


def open_ui(service):
    app = AppTest.from_function(ui_app, default_timeout=5)
    app.session_state["test_service"] = service
    app.run()
    assert not app.exception
    return app


def field(app, label):
    return next(item for item in app.text_input if item.label == label)


def test_creation_writes_only_after_explicit_save_and_keeps_user_choices():
    service = TestService()
    app = open_ui(service)
    app.button(key="semester_add").click().run()
    assert not app.exception
    assert app.text_input(key="semester_field_name").value == ""
    assert app.date_input(key="semester_field_start_date").value is None
    assert service.creates == []

    app.text_input(key="semester_field_name").set_value("Semestr testowy")
    app.text_input(key="semester_field_academic_year").set_value("Rok testowy")
    app.date_input(key="semester_field_start_date").set_value(date(2026, 1, 1))
    app.date_input(key="semester_field_end_date").set_value(date(2026, 1, 11))
    app.toggle(key="semester_field_is_active").set_value(True)
    app.run()

    assert not app.exception
    assert service.creates == []
    assert field(app, "Czas trwania (dni)").value == "10"
    assert field(app, "Czas trwania (dni)").disabled
    assert field(app, "Znacznik czasu (Timestamp)").disabled

    app.button(key="semester_save").click().run()

    assert not app.exception
    assert len(service.creates) == 1
    assert service.creates[0].is_active is True
    assert service.creates[0].is_actual is False
    assert any("utworzony" in message.value for message in app.success)


def test_edit_keeps_original_snapshot_when_database_list_changes():
    original = Semester(
        SEMESTER_ID, "Pierwotna nazwa testowa", "Rok testowy",
        date(2026, 1, 1), date(2026, 6, 30), False, True, ORIGINAL_VERSION,
    )
    service = TestService([original])
    app = open_ui(service)
    app.button(key=f"semester_edit_{SEMESTER_ID}").click().run()

    service.semesters = [replace(original, name="Zmiana innej osoby", timestamp=NEXT_VERSION)]
    app.text_input(key="semester_field_name").set_value("Moja nazwa testowa").run()

    assert not app.exception
    assert service.updates == []
    assert app.session_state["_semester_editor_snapshot"] == original
    assert field(app, "Znacznik czasu (Timestamp)").value == "0x0000000000000001"
    assert app.text_input(key="semester_field_name").value == "Moja nazwa testowa"

    app.button(key="semester_save").click().run()

    assert not app.exception
    assert len(service.updates) == 1
    assert service.updates[0][2] == ORIGINAL_VERSION
    assert app.session_state["_semester_editor_snapshot"] == original
    assert app.text_input(key="semester_field_name").value == "Moja nazwa testowa"
    assert any("zmieniony" in message.value for message in app.error)
    assert not app.success


def test_permission_revocation_closes_editor_without_writing():
    service = TestService()
    app = open_ui(service)
    app.button(key="semester_add").click().run()
    service.actions.clear()
    app.run()

    assert not app.exception
    assert not app.text_input
    assert not any(button.key == "semester_save" for button in app.button)
    assert service.creates == service.updates == []
