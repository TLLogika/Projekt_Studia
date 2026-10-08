"""The semester list and the creation/editing screen from sources/UI.jpg."""

from datetime import date, datetime
from typing import Protocol

import streamlit as st

from academic.errors import ApplicationError
from academic.semesters.models import Semester, SemesterInput

_EDITOR_KEY = "_semester_editor_id"
_SNAPSHOT_KEY = "_semester_editor_snapshot"
_FIELD_PREFIX = "semester_field_"
_NEW_SEMESTER = "__new__"


class SemesterService(Protocol):
    def list(self, token: str) -> list[Semester]: ...

    def can(self, token: str, action: str) -> bool: ...

    def create(self, token: str, data: SemesterInput) -> Semester: ...

    def update(
        self,
        token: str,
        semester_id: str,
        data: SemesterInput,
        expected_timestamp: bytes | str | None,
    ) -> Semester: ...


def _as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


def _timestamp_text(timestamp: bytes | str | None) -> str:
    if isinstance(timestamp, bytes):
        return "0x" + timestamp.hex().upper()
    return str(timestamp) if timestamp is not None else ""


def _open_editor(semester_id: str, semester: Semester | None = None) -> None:
    for key in list(st.session_state):
        if key.startswith(_FIELD_PREFIX):
            del st.session_state[key]
    st.session_state[_EDITOR_KEY] = semester_id
    st.session_state[_SNAPSHOT_KEY] = semester


def _close_editor() -> None:
    st.session_state.pop(_EDITOR_KEY, None)
    st.session_state.pop(_SNAPSHOT_KEY, None)
    for key in list(st.session_state):
        if key.startswith(_FIELD_PREFIX):
            del st.session_state[key]


def _render_editor(
    service: SemesterService,
    token: str,
    semester: Semester | None,
) -> None:
    """Zbierz zmiany i wyślij je dopiero po zatwierdzeniu formularza."""
    with st.form("semester_editor_form", border=True):
        st.subheader("Edytowanie semestru" if semester else "Dodawanie semestru")
        name = st.text_input(
            "Nazwa semestru",
            value=semester.name if semester else "",
            key=f"{_FIELD_PREFIX}name",
        )
        academic_year = st.text_input(
            "Rok akademicki",
            value=semester.academic_year if semester else "",
            key=f"{_FIELD_PREFIX}academic_year",
        )
        start_column, end_column = st.columns(2)
        with start_column:
            start_date = st.date_input(
                "Początek semestru",
                value=_as_date(semester.start_date) if semester else None,
                format="DD.MM.YYYY",
                key=f"{_FIELD_PREFIX}start_date",
            )
        with end_column:
            end_date = st.date_input(
                "Koniec semestru",
                value=_as_date(semester.end_date) if semester else None,
                format="DD.MM.YYYY",
                key=f"{_FIELD_PREFIX}end_date",
            )
        actual_column, active_column = st.columns(2)
        with actual_column:
            is_actual = st.toggle(
                "Aktualny semestr",
                value=semester.is_actual if semester else False,
                key=f"{_FIELD_PREFIX}is_actual",
            )
        with active_column:
            is_active = st.toggle(
                "Aktywny semestr",
                value=semester.is_active if semester else False,
                key=f"{_FIELD_PREFIX}is_active",
            )
        timestamp_column, duration_column = st.columns(2)
        with timestamp_column:
            st.text_input(
                "Znacznik czasu (Timestamp)",
                value=_timestamp_text(semester.timestamp) if semester else "",
                placeholder="Nadawany automatycznie",
                disabled=True,
            )
        with duration_column:
            duration = str((end_date - start_date).days) if start_date and end_date else ""
            st.text_input(
                "Czas trwania (dni)",
                value=duration,
                placeholder="Wyliczany z dat semestru",
                disabled=True,
            )
        save_column, cancel_column = st.columns([3, 1])
        with save_column:
            submitted = st.form_submit_button(
                "Zapisz zmiany" if semester else "Utwórz semestr",
                type="primary",
                use_container_width=True,
                key="semester_save",
            )
        with cancel_column:
            st.form_submit_button(
                "Anuluj",
                use_container_width=True,
                on_click=_close_editor,
                key="semester_cancel",
            )

        if not submitted:
            return
        if not name.strip():
            st.error("Podaj nazwę semestru.")
            return
        if not academic_year.strip():
            st.error("Podaj rok akademicki.")
            return
        if start_date is None or end_date is None:
            st.error("Wybierz początek i koniec semestru.")
            return
        if end_date < start_date:
            st.error("Koniec semestru nie może być wcześniejszy niż jego początek.")
            return

        data = SemesterInput(
            name=name,
            academic_year=academic_year,
            start_date=start_date,
            end_date=end_date,
            is_actual=is_actual,
            is_active=is_active,
        )
        try:
            with st.spinner("Zapisywanie semestru…"):
                if semester:
                    saved = service.update(token, semester.id, data, semester.timestamp)
                else:
                    saved = service.create(token, data)
        except ApplicationError as error:
            st.error(str(error))
            return
        st.session_state["_semesters_notice"] = (
            "Zmiany semestru zostały zapisane." if semester else "Semestr został utworzony."
        )
        st.session_state["_semesters_open_id"] = saved.id
        st.session_state.pop(_EDITOR_KEY, None)
        st.session_state.pop(_SNAPSHOT_KEY, None)
        st.rerun()


def _detail(label: str, value: str) -> None:
    left, right = st.columns([2, 3])
    with left:
        st.caption(label)
    with right:
        st.text(value)


def _render_semester(semester: Semester, *, expanded: bool, can_update: bool) -> None:
    with st.expander(semester.name, expanded=expanded):
        _detail("Nazwa semestru", semester.name)
        _detail("Rok akademicki", semester.academic_year)
        _detail("Początek semestru", _as_date(semester.start_date).strftime("%d.%m.%Y"))
        _detail("Koniec semestru", _as_date(semester.end_date).strftime("%d.%m.%Y"))
        _detail("Aktualny semestr", "Tak" if semester.is_actual else "Nie")
        _detail("Aktywny semestr", "Tak" if semester.is_active else "Nie")
        _detail("Znacznik czasu (Timestamp)", _timestamp_text(semester.timestamp))
        _detail("Czas trwania", f"{semester.duration_days} dni")
        if can_update:
            _, edit_column = st.columns([4, 1])
            with edit_column:
                st.button(
                    "Edytuj",
                    use_container_width=True,
                    key=f"semester_edit_{semester.id}",
                    on_click=_open_editor,
                    args=(semester.id, semester),
                )


def render_semesters(service: SemesterService, token: str) -> None:
    """Show only persisted semesters and actions granted to the signed-in user."""
    try:
        semesters = service.list(token)
        can_create = service.can(token, "create")
        can_update = service.can(token, "update")
    except ApplicationError as error:
        st.error(str(error))
        return

    if notice := st.session_state.pop("_semesters_notice", None):
        st.success(notice)

    if can_create:
        st.button(
            "Dodaj semestr",
            use_container_width=True,
            on_click=_open_editor,
            args=(_NEW_SEMESTER,),
            key="semester_add",
        )
    else:
        st.caption("Semestry — widok do odczytu")

    editor_id = st.session_state.get(_EDITOR_KEY)
    if editor_id == _NEW_SEMESTER and can_create:
        _render_editor(service, token, None)
    elif editor_id:
        selected = next((item for item in semesters if item.id == editor_id), None)
        snapshot = st.session_state.get(_SNAPSHOT_KEY)
        if selected is not None and snapshot is not None and can_update:
            _render_editor(service, token, snapshot)
        else:
            _close_editor()

    if not semesters:
        st.info("Nie utworzono jeszcze żadnego semestru.")
        return

    open_id = st.session_state.get("_semesters_open_id")
    for index, semester in enumerate(semesters):
        _render_semester(
            semester,
            expanded=semester.id == open_id if open_id else index == 0,
            can_update=can_update,
        )
