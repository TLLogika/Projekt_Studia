"""Integracja nawigacji prawdziwego app.py; żadne SQL ani sekrety nie są czytane."""

from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest
import streamlit as st
from streamlit.testing.v1 import AppTest

from academic.auth.models import User
from academic.modules import modules


class NavigationAuth:
    def __init__(self):
        self.codes = {code for module in modules() for code in module.permission_codes}
        self.user = User("f0572b92-8997-4e99-ac1d-3182c5a32f0e", "b7154f50-399d-448e-8f83-7ef4cfd8b6aa", "Użytkownik testu", ())

    def get_user(self, token):
        assert token == "isolated_navigation_session"
        return self.user

    def menu(self, token):
        return [{"Code": code} for code in sorted(self.codes)]

    def can(self, token, code, action):
        return action == "view" and code in self.codes


class EmptyService:
    """Puste odpowiedzi o kształcie kontraktów usług, wyłącznie w tym teście."""
    def __init__(self, kind, calls):
        self.kind, self.calls = kind, calls

    def can(self, *args):
        return False

    def list(self, *args, **kwargs):
        self.calls.append((self.kind, "list", args))
        return []

    def lookups(self, *args):
        self.calls.append((self.kind, "lookups", args))
        return {"Semesters": []}

    def generation_choices(self, *args):
        self.calls.append((self.kind, "generation_choices", args))
        return []

    def options(self, *args):
        self.calls.append((self.kind, "options", args))
        return {name: [] for name in ("Semesters", "ProgramsOfStudies", "Studies", "Specializations")}

    def scopes(self, *args):
        self.calls.append((self.kind, "scopes", args))
        return ("dean", "teacher", "student")

    def semesters(self, *args):
        self.calls.append((self.kind, "semesters", args))
        return []

    def lecture_groups(self, *args):
        self.calls.append((self.kind, "lecture_groups", args))
        return []

    def __getattr__(self, name):
        if name in {"create", "update", "commit", "generate_subjects", "finalize", "cancel", "complete", "make_up"}:
            def forbidden(*args, **kwargs):
                pytest.fail(f"Widok tylko do odczytu wywołał zapis {self.kind}.{name}.")
            return forbidden
        raise AttributeError(name)


@pytest.fixture
def navigation(monkeypatch):
    import pyodbc
    import academic.auth.local_credentials as credentials_module
    import academic.auth.service as auth_module
    import academic.catalogs.service as catalog_module
    import academic.config as config_module
    import academic.db as db_module
    import academic.importing.service as importing_module
    import academic.notes.service as notes_module
    import academic.organization.service as organization_module
    import academic.semesters.service as semesters_module
    import academic.scheduling.service as scheduling_module
    import academic.views.calendar as calendar_module

    st.cache_resource.clear()
    st.cache_data.clear()
    calls, auth = [], NavigationAuth()
    settings = SimpleNamespace(semesters_item_code="semesters")

    class ForbiddenDatabase:
        def __init__(self, supplied_settings):
            self.settings = supplied_settings

        @contextmanager
        def connection(self):
            pytest.fail("Test nawigacji próbował otworzyć SQL.")
            yield

    def forbidden_io(*args, **kwargs):
        pytest.fail("Test nawigacji próbował odczytać poświadczenia lub otworzyć SQL.")

    monkeypatch.setattr(config_module.Settings, "load", classmethod(lambda cls: settings))
    monkeypatch.setattr(db_module, "Database", ForbiddenDatabase)
    monkeypatch.setattr(pyodbc, "connect", forbidden_io)
    monkeypatch.setattr(credentials_module, "load_local_credentials", forbidden_io)
    monkeypatch.setattr(auth_module, "AuthService", lambda db: auth)
    for module, name, kind in ((semesters_module, "SemesterService", "semesters"),
            (catalog_module, "CatalogService", "catalog"), (organization_module, "OrganizationService", "organization"),
            (scheduling_module, "ScheduleService", "schedule"), (importing_module, "ImportService", "import"),
            (calendar_module, "CalendarService", "calendar"), (notes_module, "NotesService", "notes")):
        monkeypatch.setattr(module, name, lambda *args, service_kind=kind: EmptyService(service_kind, calls))

    app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"), default_timeout=5)
    app.session_state["auth_token"] = "isolated_navigation_session"
    app.run()
    assert not app.exception
    yield app, auth, calls
    st.cache_resource.clear()
    st.cache_data.clear()


def test_clean_start_and_all_registered_navigation_branches(navigation):
    app, auth, calls = navigation
    assert app.session_state["_module"] == "semesters"
    assert {button.key for button in app.button if str(button.key).startswith("nav_")} == {
        "nav_" + module.key for module in modules()}
    for module in modules():
        before = len(calls)
        app.button(key="nav_" + module.key).click().run()
        assert not app.exception, module.key
        assert app.session_state["_module"] == module.key
        rendered = calls[before:]
        assert any(call[0] == module.kind for call in rendered), module.key
        if module.kind == "catalog":
            assert any(call[:2] == ("catalog", "list") and call[2][-1] == module.key for call in rendered)
        # Auth fixtures grant only view; every rendered branch must hide all
        # write buttons while retaining navigation and the logout button.
        assert all(str(button.key).startswith("nav_") or button.label in {"Główna", "Wyloguj się", "Odśwież dane", "Pokaż przedmioty"}
                   for button in app.button), module.key


def test_revoked_selected_module_returns_to_home(navigation):
    app, auth, calls = navigation
    app.button(key="nav_schedule").click().run()
    assert app.session_state["_module"] == "schedule"
    auth.codes.remove("schedule")
    before = len(calls)
    app.run()
    assert not app.exception
    assert app.session_state["_module"] == "home"
    assert not any(button.key == "nav_schedule" for button in app.button)
    assert not calls[before:]
    assert any(title.value == "Główna" for title in app.title)


def test_no_visible_modules_leaves_home_without_write_controls(navigation):
    app, auth, calls = navigation
    auth.codes.clear()
    before = len(calls)
    app.run()
    assert not app.exception
    assert app.session_state["_module"] == "home"
    assert not calls[before:]
    assert not any(str(button.key).startswith("nav_") for button in app.button)
    assert any("Nie przypisano Ci dostępu" in message.value for message in app.info)
