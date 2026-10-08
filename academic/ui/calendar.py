"""Day, week and month calendars for actual rows allowed for the session."""

import calendar
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import streamlit as st

from academic.errors import ApplicationError

SCOPE_LABELS = {"dean": "Dziekanat — wszystkie zajęcia", "teacher": "Mój kalendarz wykładowcy", "student": "Mój plan studenta"}
WEEKDAYS = ("Poniedziałek", "Wtorek", "Środa", "Czwartek", "Piątek", "Sobota", "Niedziela")


def _date(value):
    return value.date() if isinstance(value, datetime) else value


def _status(row):
    if row["IsCanceled"]:
        return "Odwołane"
    if row["IsComplited"]:
        return "Wykonane"
    if row["IsMoved"]:
        return "Odróbka"
    return "Zaplanowane"


def _event(row):
    st.markdown(f"**{row['StartHour']}–{row['EndHour']}**")
    st.write(row["SubjectName"])
    st.caption(row["LectureGroupName"])
    names = [str(row.get("TeacherName") or "").strip(), str(row.get("AdditionalTeacherName") or "").strip()]
    names = list(dict.fromkeys(name for name in names if name))
    if names:
        st.caption(", ".join(names))
    if row.get("LectureRoomName"):
        st.caption("Sala: " + row["LectureRoomName"])
    status = _status(row)
    if status == "Odwołane":
        st.error(status)
    else:
        st.caption(status)
    st.divider()


def render_calendar(service, token):
    st.title("Kalendarz zajęć")
    try:
        scopes = service.scopes(token)
        if not scopes:
            st.error("Brak uprawnień do kalendarza.")
            return
        scope = st.selectbox("Widok", scopes, format_func=SCOPE_LABELS.get, key="calendar.scope")
        semesters = service.semesters(token, scope)
        by_id = {str(row["Id"]): row for row in semesters}
        semester = st.selectbox("Semestr", [None] + list(by_id), format_func=lambda value: "Wszystkie dostępne semestry" if value is None else by_id[value]["Name"], key="calendar.semester." + scope)
        lecture_group = None
        if scope == "dean":
            lecture_groups = service.lecture_groups(token, scope, semester)
            groups_by_id = {str(row["Id"]): row for row in lecture_groups}
            lecture_group = st.selectbox(
                "Grupa wykładowa", [None] + list(groups_by_id),
                format_func=lambda value: "Wszystkie grupy wykładowe" if value is None else
                f"{groups_by_id[value]['Name']} · {value}",
                key="calendar.lecture_group." + (semester or "all"),
            )
        selected = st.date_input("Data", value=datetime.now(ZoneInfo("Europe/Warsaw")).date(), key="calendar.date")
        period = st.radio("Zakres", ["Dzień", "Tydzień", "Miesiąc"], horizontal=True, key="calendar.period")
        if period == "Dzień":
            start = end = selected
        elif period == "Tydzień":
            start = selected - timedelta(days=selected.weekday())
            end = start + timedelta(days=6)
        else:
            start = selected.replace(day=1)
            end = selected.replace(day=calendar.monthrange(selected.year, selected.month)[1])
        rows = service.list(token, scope, start, end, semester, lecture_group_id=lecture_group)
        st.caption(f"{start:%d.%m.%Y}–{end:%d.%m.%Y} · {len(rows)} pojedynczych godzin")
        by_day = {}
        for row in rows:
            by_day.setdefault(_date(row["Date"]), []).append(row)
        if period == "Dzień":
            st.subheader(f"{WEEKDAYS[selected.weekday()]} · {selected:%d.%m.%Y}")
            if not rows:
                st.info("Brak zajęć w wybranym zakresie.")
            for row in rows:
                _event(row)
        elif period == "Tydzień":
            columns = st.columns(7)
            for offset, column in enumerate(columns):
                day = start + timedelta(days=offset)
                with column:
                    st.markdown(f"**{WEEKDAYS[offset]}**")
                    st.caption(f"{day:%d.%m.%Y}")
                    for row in by_day.get(day, []):
                        _event(row)
        else:
            for column, weekday in zip(st.columns(7), WEEKDAYS):
                column.markdown("**" + weekday + "**")
            for week in calendar.monthcalendar(selected.year, selected.month):
                for column, number in zip(st.columns(7), week):
                    with column:
                        if number:
                            day = selected.replace(day=number)
                            st.markdown(f"**{number}**")
                            events = by_day.get(day, [])
                            if events:
                                with st.expander(f"Godziny: {len(events)}"):
                                    for row in events:
                                        _event(row)
                            st.divider()
        if rows:
            with st.expander("Szczegóły wszystkich godzin w wybranym zakresie"):
                st.dataframe([{
                    "Data": _date(row["Date"]), "Od": row["StartHour"], "Do": row["EndHour"],
                    "Przedmiot": row["SubjectName"], "Grupa wykładowa": row["LectureGroupName"],
                    "Wykładowca": row["TeacherName"], "Dodatkowy wykładowca": row["AdditionalTeacherName"],
                    "Sala": row["LectureRoomName"], "Stan": _status(row),
                } for row in rows], hide_index=True, use_container_width=True)
    except ApplicationError as error:
        st.error(str(error))
