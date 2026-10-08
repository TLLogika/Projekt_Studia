"""Harmonogram pojedynczych godzin i bieżący bilans przedmiotów."""

from datetime import datetime

import streamlit as st

from academic.errors import ApplicationError
from academic.scheduling.models import ScheduleInput
from .organization import choose_record, timestamp_text


def _close():
    for key in list(st.session_state):
        if key.startswith("schedule_editor"):
            del st.session_state[key]


def _open(row=None, makeup=False):
    _close()
    st.session_state["schedule_editor"] = row or {}
    st.session_state["schedule_editor_makeup"] = makeup


def _date_text(value):
    return value.strftime("%d.%m.%Y") if hasattr(value, "strftime") else "—"


def _editor(service, token, semester_id, snapshot, lookups):
    makeup = st.session_state.get("schedule_editor_makeup", False)
    with st.container(border=True):
        st.subheader("Wyznaczanie odróbki" if makeup else "Edytowanie godziny" if snapshot else "Dodawanie godziny")
        organizations = [dict(row, Label=f"{row.get('SubjectName')} · {row.get('LectureGroupName')}") for row in lookups["Organizations"]]
        if makeup:
            organization_id = str(snapshot["SemesterOrganizationId"])
            st.text(f"Organizacja semestru: {snapshot.get('SubjectName')} · {snapshot.get('LectureGroupName')}")
        else:
            organization_id = choose_record("Organizacja semestru", organizations, field="Label",
                key="schedule_editor_org", selected=snapshot.get("SemesterOrganizationId"))
        try:
            choices = service.lookups(token, semester_id, organization_id) if organization_id else {}
        except ApplicationError as error:
            st.error(str(error))
            return
        dates = [dict(row, Label=f"{_date_text(row['Date'])} · zjazd {row['Number']}") for row in choices.get("Dates", [])]
        hours = [dict(row, Label=f"{row.get('StartHour') or '—'}–{row.get('EndHour') or '—'} · godzina {row['Number']}")
                 for row in choices.get("Hours", [])]
        day = choose_record("Data / zjazd", dates, field="Label", key="schedule_editor_date", selected=snapshot.get("SemesterCalendarDateId"))
        hour = choose_record("Godzina", hours, field="Label", key="schedule_editor_hour", selected=snapshot.get("DailyScheduleId"))
        teacher = choose_record("Dodatkowy wykładowca", lookups["Teachers"], field="TeacherName", optional=True,
            key="schedule_editor_teacher", selected=snapshot.get("TeacherId"))
        room = st.text_input("Sala", value=snapshot.get("LectureRoomName") or "", key="schedule_editor_room")
        if makeup:
            completed, canceled, active = False, False, True
        else:
            completed = st.toggle("Czy wykonane", value=bool(snapshot.get("IsComplited", False)), key="schedule_editor_completed")
            canceled = st.toggle("Czy odwołane", value=bool(snapshot.get("IsCanceled", False)), key="schedule_editor_canceled")
            active = st.toggle("Czy aktywne", value=bool(snapshot.get("IsActive", True)), key="schedule_editor_active")
        st.text_input("Znacznik czasu (Timestamp)", value=timestamp_text(snapshot.get("Timestamp")),
            disabled=True, placeholder="Nadawany automatycznie", key="schedule_editor_timestamp")
        left, right = st.columns([3, 1])
        with left:
            submit = st.button("Zapisz odróbkę" if makeup else "Zapisz zmiany" if snapshot else "Utwórz godzinę",
                type="primary", key="schedule_editor_save", use_container_width=True)
        with right:
            st.button("Anuluj", on_click=_close, key="schedule_editor_cancel", use_container_width=True)
        if submit:
            draft = ScheduleInput(organization_id, day, hour, teacher, room, active, canceled, completed)
            try:
                if makeup:
                    service.make_up(token, str(snapshot["Id"]), draft, snapshot["Timestamp"])
                elif snapshot:
                    service.update(token, str(snapshot["Id"]), draft, snapshot["Timestamp"])
                else:
                    service.create(token, draft)
            except ApplicationError as error:
                st.error(str(error))
            else:
                _close()
                st.session_state["schedule_notice"] = "Odróbka została zapisana." if makeup else "Godzina została zapisana."
                st.rerun()


def _action(service, token, row, name):
    try:
        getattr(service, name)(token, str(row["Id"]), row["Timestamp"])
    except ApplicationError as error:
        st.error(str(error))
    else:
        st.session_state["schedule_notice"] = "Godzina została odwołana i wróciła do nieprzydzielonych." if name == "cancel" else "Godzina została oznaczona jako wykonana."
        st.rerun()


def render_scheduling(service, token):
    st.title("Harmonogram semestru")
    try:
        semesters = service.lookups(token)["Semesters"]
        if not semesters:
            st.info("Najpierw utwórz i włącz semestr.")
            return
        semester_id = choose_record("Semestr", semesters, key="schedule_semester",
            selected=next((row["Id"] for row in semesters if row.get("IsActual") and row.get("IsActive")), None))
        if semester_id is None:
            return
        if st.session_state.get("schedule_previous_semester") != semester_id:
            _close()
            st.session_state["schedule_previous_semester"] = semester_id
        semester = next(row for row in semesters if str(row["Id"]) == semester_id)
        rows, balance = service.list(token, semester_id), service.balance(token, semester_id)
        lookups = service.lookups(token, semester_id)
        can_create, can_update = service.can(token, "create"), service.can(token, "update")
    except ApplicationError as error:
        st.error(str(error))
        return
    if notice := st.session_state.pop("schedule_notice", None):
        st.success(notice)
    if not semester["IsActive"]:
        st.warning("Semestr jest nieaktywny. Włącz go, aby zmieniać harmonogram.")
    if can_create and semester["IsActive"]:
        st.button("Dodaj godzinę", on_click=_open, key="schedule_add", use_container_width=True)
    if "schedule_editor" in st.session_state and semester["IsActive"]:
        snapshot = st.session_state["schedule_editor"]
        if (snapshot and can_update) or (not snapshot and can_create):
            _editor(service, token, semester_id, snapshot, lookups)
    st.subheader("Bilans realizacji")
    if balance:
        st.dataframe([{"Przedmiot": row["SubjectName"], "Grupa wykładowa": row["LectureGroupName"],
                       "Wymagane": row["required"], "Zaplanowane": row["planned"], "Wykonane": row["completed"],
                       "Odwołane": row["canceled"], "Do wykonania": row["to_complete"],
                       "Nieprzydzielone": row["unassigned"], "Nadmiar": row["excess"]} for row in balance],
                     hide_index=True, use_container_width=True)
    else:
        st.info("Brak przedmiotów w semestrze do zbilansowania.")
    st.subheader("Godziny zajęć")
    day_filter = st.date_input("Dzień zajęć", value=None, format="DD.MM.YYYY", key="schedule_filter_date")
    for row in rows:
        date = row.get("Date")
        if day_filter and (date.date() if isinstance(date, datetime) else date) != day_filter:
            continue
        status = "Nieaktywne" if not row["IsActive"] else "Odwołane" if row["IsCanceled"] else "Wykonane" if row["IsComplited"] else "Zaplanowane"
        with st.expander(f"{_date_text(date)} · {row.get('StartHour') or '—'}–{row.get('EndHour') or '—'} · {row.get('SubjectName') or 'Przedmiot'} · {status}"):
            for label, value in (("Organizacja semestru", f"{row.get('SubjectName')} · {row.get('LectureGroupName')}"),
                                 ("Wykładowca", row.get("TeacherName")), ("Dodatkowy wykładowca", row.get("AdditionalTeacherName")),
                                 ("Sala", row.get("LectureRoomName")), ("Stan", status),
                                 ("Przeniesiono", "Tak" if row.get("IsMoved") else "Nie"),
                                 ("Znacznik czasu (Timestamp)", timestamp_text(row["Timestamp"]))):
                st.text(f"{label}: {value or '—'}")
            if can_update and semester["IsActive"]:
                columns = st.columns(3)
                with columns[0]:
                    st.button("Edytuj", key=f"schedule_edit_{row['Id']}", on_click=_open, args=(row,))
                if row["IsCanceled"]:
                    with columns[1]:
                        st.button("Wyznacz odróbkę", key=f"schedule_makeup_{row['Id']}", on_click=_open, args=(row, True))
                elif row["IsActive"] and not row["IsComplited"]:
                    with columns[1]:
                        if st.button("Odwołaj", key=f"schedule_cancel_{row['Id']}"):
                            _action(service, token, row, "cancel")
                    with columns[2]:
                        if st.button("Oznacz jako wykonane", key=f"schedule_complete_{row['Id']}"):
                            _action(service, token, row, "complete")
    if not rows:
        st.info("Nie zaplanowano jeszcze żadnych godzin w tym semestrze.")
