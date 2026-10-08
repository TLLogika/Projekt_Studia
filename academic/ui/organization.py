"""Ekran organizacji semestru zgodny z polami źródłowego UI."""

import streamlit as st

from academic.errors import ApplicationError


def choose_record(label, rows, *, key, field="Name", selected=None, optional=False):
    options = [str(row["Id"]) for row in rows]
    labels = {str(row["Id"]): str(row.get(field) or row["Id"]) for row in rows}
    if optional:
        options = [None] + options
        labels[None] = "Brak"
    if not options:
        st.caption(f"{label}: brak danych do wyboru.")
        return None
    selected = str(selected) if selected is not None else None
    index = options.index(selected) if selected in options else None
    return st.selectbox(label, options, index=index, format_func=lambda value: labels[value], key=key)


def timestamp_text(value):
    return "0x" + bytes(value).hex().upper() if isinstance(value, (bytes, bytearray)) else ""


def _close():
    for key in list(st.session_state):
        if key.startswith("organization_editor"):
            del st.session_state[key]


def _open(row=None):
    _close()
    st.session_state["organization_editor"] = row or {}


def _editor(service, token, semester, lookups, snapshot):
    with st.form("organization_editor_form", border=True):
        st.subheader("Edytowanie organizacji semestru" if snapshot else "Dodawanie organizacji semestru")
        subject = choose_record("Przedmiot w semestrze", lookups.get("SubjectsSemesters", []),
            key="organization_editor_subject", field="SubjectName", selected=snapshot.get("SubjectsSemesterId"))
        group = choose_record("Grupa wykładowa", lookups.get("LectureGroups", []),
            key="organization_editor_group", selected=snapshot.get("LectureGroupId"))
        teacher = choose_record("Wykładowca", lookups.get("Teachers", []),
            key="organization_editor_teacher", field="TeacherName", selected=snapshot.get("TeacherId"))
        active = st.toggle("Aktywny przydział", value=bool(snapshot.get("IsActive", True)), key="organization_editor_active")
        st.text_input("Znacznik czasu (Timestamp)", value=timestamp_text(snapshot.get("Timestamp")),
            placeholder="Nadawany automatycznie", disabled=True, key="organization_editor_timestamp")
        left, right = st.columns([3, 1])
        with left:
            saved = st.form_submit_button("Zapisz zmiany" if snapshot else "Utwórz przydział", type="primary",
                              key="organization_editor_save", use_container_width=True)
        with right:
            st.form_submit_button("Anuluj", on_click=_close, key="organization_editor_cancel", use_container_width=True)
        if saved:
            try:
                values = {"SubjectsSemesterId": subject, "LectureGroupId": group, "TeacherId": teacher, "IsActive": active}
                if snapshot:
                    service.update(token, str(snapshot["Id"]), values, snapshot["Timestamp"])
                else:
                    service.create(token, values)
            except ApplicationError as error:
                st.error(str(error))
            else:
                _close()
                st.session_state["organization_notice"] = "Przydział został zapisany."
                st.rerun()


def render_organization(service, token):
    st.title("Organizacja semestru")
    try:
        initial = service.lookups(token)
        semesters = initial["Semesters"]
        if not semesters:
            st.info("Najpierw utwórz i włącz semestr.")
            return
        semester_id = choose_record("Semestr", semesters, key="organization_semester",
            selected=next((row["Id"] for row in semesters if row.get("IsActual") and row.get("IsActive")), None))
        if semester_id is None:
            return
        previous = st.session_state.get("organization_previous_semester")
        if previous != semester_id:
            _close()
            st.session_state["organization_previous_semester"] = semester_id
        semester = next(row for row in semesters if str(row["Id"]) == semester_id)
        rows = service.list(token, semester_id)
        lookups = service.lookups(token, semester_id)
        can_create, can_update = service.can(token, "create"), service.can(token, "update")
        report = service.readiness(token, semester_id)
    except ApplicationError as error:
        st.error(str(error))
        return
    if notice := st.session_state.pop("organization_notice", None):
        st.success(notice)
    if not semester["IsActive"]:
        st.warning("Semestr jest nieaktywny. Włącz go, aby organizować zajęcia.")
    if can_create and semester["IsActive"]:
        st.button("Dodaj organizację semestru", on_click=_open, key="organization_add", use_container_width=True)
    if "organization_editor" in st.session_state and semester["IsActive"]:
        snapshot = st.session_state["organization_editor"]
        if (snapshot and can_update) or (not snapshot and can_create):
            _editor(service, token, semester, lookups, snapshot)
    for row in rows:
        with st.expander(f"{row.get('SubjectName') or 'Przedmiot'} · {row.get('LectureGroupName') or 'Grupa'}"):
            for label, value in (("Przedmiot w semestrze", row.get("SubjectName")),
                                 ("Grupa wykładowa", row.get("LectureGroupName")),
                                 ("Wykładowca", row.get("TeacherName")),
                                 ("Aktywny przydział", "Tak" if row["IsActive"] else "Nie"),
                                 ("Znacznik czasu (Timestamp)", timestamp_text(row["Timestamp"]))):
                st.text(f"{label}: {value or '—'}")
            if can_update and semester["IsActive"]:
                st.button("Edytuj", key=f"organization_edit_{row['Id']}", on_click=_open, args=(row,))
    if not rows:
        st.info("Nie utworzono jeszcze przydziałów w tym semestrze. Wcześniej przygotuj przedmioty, grupy i wykładowców.")
    with st.container(border=True):
        st.subheader("Kompletność organizacji")
        st.caption(f"Przedmioty: {report.subjects_count} · Grupy: {report.groups_count} · Przydziały: {report.organizations_count}")
        if report.ready:
            st.success("Organizacja jest kompletna. Można przygotować harmonogram.")
        else:
            for issue in report.issues:
                st.warning(issue)
        if can_update and semester["IsActive"] and st.button("Zakończ organizację", key="organization_finalize", type="primary"):
            try:
                service.finalize(token, semester_id)
            except ApplicationError as error:
                st.error(str(error))
            else:
                st.success("Kontrola zakończona: wszystkie wymagane dane organizacji są kompletne.")
