"""Listy, szczegóły i formularze katalogów według układu sources/UI.jpg."""

from datetime import date, datetime
from decimal import Decimal

import streamlit as st

from academic.catalogs.specs import CATALOGS
from academic.errors import ApplicationError


def _prefix(key):
    return "catalog_" + key + "_"


def _timestamp(value):
    return "0x" + value.hex().upper() if isinstance(value, bytes) else str(value or "")


def _as_date(value):
    return value.date() if isinstance(value, datetime) else value


def _open(key, row=None):
    prefix = _prefix(key)
    for name in list(st.session_state):
        if name.startswith(prefix + "field_"):
            del st.session_state[name]
    st.session_state[prefix + "editor"] = row if row is not None else "new"


def _close(key):
    prefix = _prefix(key)
    st.session_state.pop(prefix + "editor", None)
    for name in list(st.session_state):
        if name.startswith(prefix + "field_"):
            del st.session_state[name]


def _filtered_choices(rows, field, draft, selected, allow_inactive_semester=False):
    result = []
    for row in rows:
        current = str(row["Id"]) == str(selected)
        if not current:
            preparing_semester = allow_inactive_semester and field.reference == "Semesters"
            if not preparing_semester and not row.get("_active", row.get("IsActive", True)):
                continue
            if field.reference == "Specializations" and draft.get("StudiesId") and str(row.get("StudiesId")) != str(draft["StudiesId"]):
                continue
            if field.reference == "SemesterCalendars" and draft.get("SemesterId") and str(row.get("SemesterId")) != str(draft["SemesterId"]):
                continue
        result.append(row)
    return result


def _field(service, token, spec, field, current, draft):
    value = current.get(field.name) if current else None
    widget_key = _prefix(spec.key) + "field_" + field.name
    label = field.label + (" *" if field.required else "")
    if field.kind == "reference":
        rows = _filtered_choices(service.choices(token, spec.key, field.name), field, draft, value, spec.table != "SubjectsSemesters")
        options = [None] + [str(row["Id"]) for row in rows]
        lookup = {str(row["Id"]): row for row in rows}
        selected = str(value) if value is not None else None
        index = options.index(selected) if selected in options else 0
        def title(item):
            if item is None:
                return "Wybierz…" if field.required else "Brak"
            row = lookup[item]
            marker = " (nieaktywny)" if not row.get("_active", row.get("IsActive", True)) else ""
            return row["_label"] + marker + " · " + item[:8]
        result = st.selectbox(label, options, index=index, format_func=title, key=widget_key)
        if field.required and not rows:
            st.caption(f"Brak danych dla pola „{field.label}”. Uzupełnij powiązany katalog.")
        return result
    if field.kind == "boolean":
        return st.toggle(label, value=bool(value) if value is not None else field.name == "IsActive", key=widget_key)
    if field.kind == "date":
        return st.date_input(label, value=_as_date(value), format="DD.MM.YYYY", key=widget_key)
    if field.kind in {"integer", "decimal"}:
        if spec.table == "Subjects" and field.name == "Semester":
            studies_id = st.session_state.get(_prefix(spec.key) + "field_StudiesId", current.get("StudiesId") if current else None)
            study_rows = service.choices(token, spec.key, "StudiesId")
            study = next((row for row in study_rows if str(row["Id"]) == str(studies_id)), None)
            count = study.get("NumberOfSemesters", 0) if study else 0
            options = [None] + list(range(1, count + 1))
            if value is not None and value not in options:
                options.append(value)
            if not count:
                st.caption("Wybierz studia z określoną liczbą semestrów, aby wybrać numer semestru.")
            return st.selectbox(label, options, index=options.index(value) if value in options else 0,
                format_func=lambda item: "Wybierz…" if item is None else str(item), key=widget_key)
        integer = field.kind == "integer"
        cast = int if integer else float
        minimum = cast(field.minimum) if field.minimum is not None else None
        maximum = cast(field.maximum) if field.maximum is not None else None
        result = st.number_input(label, min_value=minimum, max_value=maximum, value=cast(value) if value is not None else None,
            step=1 if integer else 0.01, key=widget_key)
        return Decimal(str(result)) if not integer and result is not None else result
    widget = st.text_area if field.kind == "multiline" else st.text_input
    return widget(label, value=str(value) if value is not None else "", key=widget_key) or None


def _render_editor(service, token, spec, current):
    with st.container(border=True):
        st.subheader("Edycja" if current else "Dodawanie")
        st.caption("* Pole wymagane")
        draft = {}
        for field in spec.fields:
            draft[field.name] = _field(service, token, spec, field, current, draft)
        st.text_input("Znacznik czasu (Timestamp)", value=_timestamp(current.get("Timestamp")) if current else "",
            placeholder="Nadawany automatycznie", disabled=True, key=_prefix(spec.key) + "timestamp")
        if spec.table in {"LectureGroups", "SubjectsSemesters"} and draft.get("SemesterId"):
            rows = service.choices(token, spec.key, "SemesterId")
            semester = next((row for row in rows if str(row["Id"]) == str(draft["SemesterId"])), None)
            if semester:
                duration = (_as_date(semester["EndDate"]) - _as_date(semester["StartDate"])).days
                st.text_input("Czas trwania semestru (dni)", value=str(duration), disabled=True,
                    key=_prefix(spec.key) + "duration")
        if spec.table == "SemesterCalendarDates" and draft.get("Date"):
            day = ("poniedziałek", "wtorek", "środa", "czwartek", "piątek", "sobota", "niedziela")[draft["Date"].weekday()]
            st.text_input("Dzień tygodnia", value=day, disabled=True, key=_prefix(spec.key) + "day")
        left, right = st.columns([3, 1])
        with left:
            submitted = st.button("Zapisz zmiany" if current else "Utwórz", type="primary", use_container_width=True,
                key=_prefix(spec.key) + "save")
        with right:
            st.button("Anuluj", use_container_width=True, on_click=_close, args=(spec.key,), key=_prefix(spec.key) + "cancel")
        if submitted:
            try:
                if current:
                    service.update(token, spec.key, str(current["Id"]), draft, current["Timestamp"])
                else:
                    service.create(token, spec.key, draft)
            except ApplicationError as error:
                st.error(str(error))
                return
            st.session_state[_prefix(spec.key) + "notice"] = "Zmiany zostały zapisane." if current else "Rekord został utworzony."
            _close(spec.key)
            st.rerun()


def _detail(label, value):
    left, right = st.columns([2, 3])
    with left:
        st.caption(label)
    with right:
        if isinstance(value, bool):
            value = "Tak" if value else "Nie"
        elif isinstance(value, (date, datetime)):
            value = _as_date(value).strftime("%d.%m.%Y")
        st.text(str(value) if value is not None else "—")


def _render_generation(service, token, spec):
    with st.expander("Wybierz przedmioty z programu nauczania"):
        st.caption("Wybierz program obowiązujący na początku semestru. Lista obejmie przedmioty wspólne i wybraną specjalizację. Godziny planowane pochodzą z programu; istniejące realizacje są zachowywane.")
        criteria = {}
        criteria_fields = (
            ("semester_id", "Semestr", "subjects_semesters", "SemesterId"),
            ("program_id", "Program nauczania", "subjects", "ProgramsOfStudyId"),
            ("studies_id", "Rodzaj studiów", "subjects", "StudiesId"),
            ("specialization_id", "Specjalizacja", "subjects", "SpecializationId"),
        )
        for name, label, key, field_name in criteria_fields:
            try:
                rows = service.generation_choices(token, name)
            except ApplicationError as error:
                st.error(str(error))
                return
            rows = [row for row in rows if row.get("IsActive", True)]
            if name == "specialization_id" and criteria.get("studies_id"):
                rows = [row for row in rows if str(row.get("StudiesId")) == str(criteria["studies_id"])]
            lookup = {str(row["Id"]): row["_label"] + " · " + str(row["Id"])[:8] for row in rows}
            placeholder = "Bez specjalizacji" if name == "specialization_id" else "Wybierz…"
            criteria[name] = st.selectbox(label, [None] + list(lookup), format_func=lambda item, names=lookup, empty=placeholder: names.get(item, empty),
                key="subject_generation_" + name)
        criteria["semester_number"] = st.number_input("Numer semestru studiów", min_value=1, value=None, step=1, key="subject_generation_number")
        hybrid_hours = st.number_input("Godziny hybrydowe dla generowanych realizacji", min_value=0, max_value=255, value=0, step=1, key="subject_generation_hybrid")
        ready = all(criteria.get(key) is not None for key in ("semester_id", "program_id", "studies_id", "semester_number"))
        if ready:
            try:
                subjects = service.preview_subjects(token, **criteria)
            except ApplicationError as error:
                st.error(str(error))
                return
            if not subjects:
                st.info("Brak przedmiotów odpowiadających wybranym kryteriom.")
            else:
                st.dataframe([{"Przedmiot": row["_label"], "Liczba godzin z programu": row.get("NumberOfHours")} for row in subjects], hide_index=True, use_container_width=True)
                if service.can(token, spec.key, "create") and st.button("Utwórz przedmioty w semestrze", type="primary", key="subject_generation_save"):
                    try:
                        result = service.generate_subjects(token, **criteria, hybrid_hours=hybrid_hours)
                    except ApplicationError as error:
                        st.error(str(error))
                        return
                    st.session_state[_prefix(spec.key) + "notice"] = f"Utworzono realizacji: {len(result.created)}. Już istniejące: {result.already_existing}."
                    st.rerun()


def render_catalogs(service, token: str, entity_key: str):
    spec = CATALOGS[entity_key]
    try:
        rows = service.list(token, entity_key)
        can_create = service.can(token, entity_key, "create")
        can_update = service.can(token, entity_key, "update")
        if notice := st.session_state.pop(_prefix(entity_key) + "notice", None):
            st.success(notice)
        if can_create:
            st.button("Dodaj " + spec.singular, use_container_width=True, on_click=_open, args=(entity_key,), key=_prefix(entity_key) + "add")
        snapshot = st.session_state.get(_prefix(entity_key) + "editor")
        if snapshot == "new" and can_create:
            _render_editor(service, token, spec, None)
        elif isinstance(snapshot, dict) and can_update and any(str(row["Id"]) == str(snapshot["Id"]) for row in rows):
            _render_editor(service, token, spec, snapshot)
        elif snapshot is not None:
            _close(entity_key)
        if entity_key == "subjects_semesters":
            _render_generation(service, token, spec)
        if not rows:
            st.info("Brak rekordów w tym katalogu.")
        for index, row in enumerate(rows):
            active = " (nieaktywny)" if not row.get("IsActive", True) else ""
            with st.expander(row["_label"] + active, expanded=index == 0):
                for field in spec.fields:
                    value = row.get("_related", {}).get(field.name) if field.reference else row.get(field.name)
                    _detail(field.label, value or None if field.reference else value)
                _detail("Znacznik czasu (Timestamp)", _timestamp(row.get("Timestamp")))
                if row.get("_duration_days") is not None:
                    _detail("Czas trwania semestru", f"{row['_duration_days']} dni")
                if can_update:
                    st.button("Edytuj", key=_prefix(entity_key) + "edit_" + str(row["Id"]), on_click=_open, args=(entity_key, row))
    except ApplicationError as error:
        st.error(str(error))
