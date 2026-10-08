"""Punkt wejścia Streamlit. Uruchom: python -m streamlit run app.py."""

import html

import streamlit as st

from academic.auth.service import AuthService, AuthenticationError
from academic.auth.local_credentials import load_local_credentials
from academic.config import Settings
from academic.db import Database, one
from academic.errors import ApplicationError
from academic.semesters.service import SemesterService
from academic.ui.login import render_login
from academic.ui.semesters import render_semesters
from academic.ui.styles import apply_styles
from academic.catalogs.service import CatalogService
from academic.organization.service import OrganizationService
from academic.scheduling.service import ScheduleService
from academic.importing.service import ImportService
from academic.views.calendar import CalendarService
from academic.modules import available_modules
from academic.ui.catalogs import render_catalogs
from academic.ui.organization import render_organization
from academic.ui.scheduling import render_scheduling
from academic.ui.importing import render_importing
from academic.ui.calendar import render_calendar
from academic.notes.service import NotesService
from academic.ui.notes import render_notes

st.set_page_config(page_title="Organizacja semestru", layout="wide", initial_sidebar_state="expanded")
apply_styles()


@st.cache_resource
def services():
    database = Database(Settings.load())
    auth = AuthService(database)
    return database, auth, SemesterService(database, auth)


@st.cache_resource
def module_services():
    database, auth, _ = services()
    schedule = ScheduleService(database, auth)
    return {
        "catalog": CatalogService(database, auth),
        "organization": OrganizationService(database, auth),
        "schedule": schedule,
        "import": ImportService(database, auth, schedule),
        "calendar": CalendarService(database, auth),
        "notes": NotesService(database, auth),
    }


@st.cache_data(ttl=15, show_spinner=False)
def login_is_configured() -> bool:
    database, _, _ = services()
    settings = database.settings
    with database.connection() as connection:
        if settings.auth_provider == "local_file":
            credentials = load_local_credentials(settings)
            return any(
                one(connection,
                    "SELECT 1 AS present FROM org.Accounts a JOIN org.Persons p ON p.Id=a.PersonId "
                    "WHERE a.Id=? AND a.IsActive=1 AND p.IsActive=1", (account_id,)) is not None
                for account_id in credentials
            )
        if not database.table_exists(
            settings.auth_table, settings.auth_schema, settings.auth_database,
            connection=connection,
        ):
            return False
        return one(
            connection,
            "SELECT TOP (1) 1 AS present FROM [org].[Accounts] a "
            "JOIN [org].[Persons] p ON p.Id=a.PersonId "
            "WHERE a.IsActive=1 AND p.IsActive=1",
        ) is not None


def logout(auth):
    token = st.session_state.get("auth_token")
    if token:
        auth.logout(token)
    st.session_state.clear()


def main():
    try:
        database, auth, semesters = services()
    except ApplicationError as error:
        st.error(str(error))
        return

    token = st.session_state.get("auth_token")
    if not token:
        try:
            configured = login_is_configured()
        except ApplicationError as error:
            st.error(str(error))
            return
        if not configured:
            st.info("Logowanie oczekuje na konfigurację kont przez administratora.")
        render_login(auth)
        return

    try:
        user = auth.get_user(token)
        menu = auth.menu(token)
        available = available_modules(menu, database.settings.semesters_item_code)
    except AuthenticationError as error:
        logout(auth)
        st.session_state["_login_error"] = str(error)
        st.rerun()
    except ApplicationError as error:
        st.error(str(error))
        return

    with st.sidebar:
        initials = "".join(part[0] for part in user.display_name.split()[:2])
        st.markdown(
            '<div style="width:52px;height:52px;border:1px solid #777;'
            'border-radius:50%;display:flex;align-items:center;justify-content:center;'
            'margin:0 0 12px auto;">' + html.escape(initials) + "</div>",
            unsafe_allow_html=True,
        )
        st.text(user.display_name)
        if user.roles:
            st.caption(" · ".join(user.roles))

        if "_module" not in st.session_state:
            st.session_state["_module"] = available[0].key if available else "home"
        if st.session_state["_module"] != "home" and not any(
            module.key == st.session_state["_module"] for module in available
        ):
            st.session_state["_module"] = "home"
        if st.button("Główna", use_container_width=True):
            st.session_state["_module"] = "home"
        for module in available:
            if not module.group and st.button(module.title, key="nav_" + module.key, use_container_width=True):
                st.session_state["_module"] = module.key
        for group in ("Zarządzanie grupami", "Przedmioty", "Organizacja i harmonogram", "Słowniki"):
            members = [module for module in available if module.group == group]
            if not members:
                continue
            with st.expander(group, expanded=group != "Słowniki"):
                for module in members:
                    if st.button(module.title, key="nav_" + module.key, use_container_width=True):
                        st.session_state["_module"] = module.key
        st.divider()
        st.button("Wyloguj się", on_click=logout, args=(auth,), use_container_width=True)

    selected = next((module for module in available if module.key == st.session_state["_module"]), None)
    if selected is None:
        st.title("Główna")
        st.write("Wybierz moduł z menu.")
        if not available:
            st.info("Nie przypisano Ci dostępu do modułów. Skontaktuj się z administratorem.")
        return
    try:
        if selected.kind == "semesters":
            render_semesters(semesters, token)
        else:
            service = module_services()[selected.kind]
            if selected.kind == "catalog":
                render_catalogs(service, token, selected.key)
            elif selected.kind == "organization":
                render_organization(service, token)
            elif selected.kind == "schedule":
                render_scheduling(service, token)
            elif selected.kind == "import":
                render_importing(service, token)
            elif selected.kind == "calendar":
                render_calendar(service, token)
            elif selected.kind == "notes":
                render_notes(service, token)
    except ApplicationError as error:
        st.error(str(error))


main()
