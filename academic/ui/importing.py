"""Explicit source mapping and a concrete, reviewable preview before import."""

from hashlib import sha256

import streamlit as st

from academic.errors import ApplicationError
from academic.importing.models import ImportContext, ImportMapping
from academic.importing.reader import read_table, sheet_names

FIELD_LABELS = {
    "organization_id": "ID organizacji semestru",
    "subject_id": "ID przedmiotu", "subject_name": "Nazwa przedmiotu",
    "lecture_group_id": "ID grupy wykładowej", "lecture_group_name": "Nazwa grupy wykładowej",
    "group_id": "ID grupy studenckiej", "group_name": "Nazwa grupy studenckiej",
    "teacher_id": "ID wykładowcy", "teacher_name": "Imię i nazwisko / skrót wykładowcy",
    "date": "Data zajęć", "calendar_date_id": "ID dnia kalendarza",
    "start": "Godzina początku", "end": "Godzina końca", "time_range": "Zakres godzin",
    "slot_id": "ID pojedynczego slotu", "room": "Sala",
}


@st.cache_data(ttl=600, max_entries=8, show_spinner=False)
def _read_uploaded_table(session_key, content, filename, reader_options):
    return read_table(content, filename, **reader_options)


@st.cache_data(ttl=600, max_entries=8, show_spinner=False)
def _uploaded_sheets(session_key, content):
    return sheet_names(content)


def _choice(label, rows, name_field, *, key, optional=False):
    by_id = {str(row["Id"]): row for row in rows}
    return st.selectbox(label, ([None] if optional else []) + list(by_id), index=0 if optional else None,
                        placeholder="Wybierz istniejący rekord",
                        format_func=lambda value: "Bez ograniczenia" if value is None else
                        f"{by_id[value].get(name_field) or '(bez nazwy)'} · {value}", key=key)


def render_importing(service, token):
    st.title("Import planu dziekanatu")
    st.write("Wybierz własny plik CSV lub XLSX i przypisz jego kolumny. Import korzysta z przedmiotów, grup, wykładowców, organizacji, dat i godzin już istniejących w bazie.")
    try:
        options = service.options(token)
        semester_id = _choice("Semestr", options["Semesters"], "Name", key="import.semester")
        with st.expander("Program, studia i numer semestru studiów"):
            st.caption("Przy dopasowaniu przedmiotów po nazwie wybierz program, studia oraz numer semestru studiów. ID przedmiotu identyfikuje go jednoznacznie.")
            program_id = _choice("Program nauczania", options["ProgramsOfStudies"], "ProgramName", key="import.program", optional=True)
            studies_id = _choice("Studia", options["Studies"], "Name", key="import.studies", optional=True)
            specify_number = st.checkbox("Ogranicz do numeru semestru studiów", key="import.number.enabled")
            number = int(st.number_input("Numer semestru studiów", min_value=1, step=1, key="import.number")) if specify_number else None
            specializations = [row for row in options["Specializations"] if not studies_id or str(row.get("StudiesId")) == studies_id]
            specialization_id = _choice("Specjalizacja", specializations, "Name", key="import.specialization", optional=True)
        uploaded = st.file_uploader("Plan zajęć", type=["csv", "xlsx"], key="import.file")
        if uploaded is None:
            st.info("W sources nie ma arkusza planu. Wgraj rzeczywisty plik dziekanatu, aby przygotować podgląd.")
            return
        content = uploaded.getvalue()
        digest = sha256(content + uploaded.name.encode()).hexdigest()[:20]
        reader_options = {}
        with st.expander("Odczyt pliku", expanded=True):
            reader_options["header_row"] = int(st.number_input("Wiersz z nazwami kolumn", min_value=1, max_value=10000, step=1, key="import.header." + digest))
            if uploaded.name.lower().endswith(".xlsx"):
                reader_options["sheet"] = st.selectbox("Arkusz", _uploaded_sheets(sha256(token.encode()).hexdigest(), content), index=None, placeholder="Wybierz arkusz", key="import.sheet." + digest)
                if reader_options["sheet"] is None:
                    return
            else:
                reader_options["delimiter"] = st.selectbox("Separator CSV", [";", ",", "\t"], format_func=lambda value: "Tabulator" if value == "\t" else value, key="import.separator." + digest)
                reader_options["encoding"] = st.selectbox("Kodowanie CSV", ["utf-8-sig", "cp1250"], key="import.encoding." + digest)
        table = _read_uploaded_table(sha256(token.encode()).hexdigest(), content, uploaded.name, reader_options)
        st.caption(f"Odczytano {len(table.rows)} wierszy. Poniżej pierwsze 20 wierszy wybranego pliku.")
        st.dataframe([row.values for row in table.rows[:20]], hide_index=True, use_container_width=True)
        mapped = {}
        with st.expander("Mapowanie kolumn", expanded=True):
            st.caption("Wskaż ID organizacji albo przedmiot i grupę. Wybierz datę lub ID dnia oraz zakres godzin, początek/koniec albo ID slotu. Przy ID organizacji pole wykładowcy może wskazywać dodatkowego wykładowcę.")
            left, right = st.columns(2)
            for index, (field, label) in enumerate(FIELD_LABELS.items()):
                with (left if index % 2 == 0 else right):
                    column = st.selectbox(label, [None] + list(table.columns),
                                          format_func=lambda value: "Nie przypisano" if value is None else value,
                                          key="import.map." + digest + "." + field)
                    if column:
                        mapped[field] = column
            date_format = st.selectbox("Format dat w kolumnie tekstowej", ["%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y"],
                                       format_func=lambda value: {"%Y-%m-%d": "RRRR-MM-DD", "%d.%m.%Y": "DD.MM.RRRR", "%d/%m/%Y": "DD/MM/RRRR"}[value], key="import.dateformat." + digest)
        mapping = ImportMapping(mapped, date_format)
        context = ImportContext(semester_id, program_id, studies_id, number, specialization_id)
        signature = (digest, repr(reader_options), repr(mapping), repr(context))
        preview_key = "import.preview"
        if st.button("Przygotuj podgląd", disabled=semester_id is None, key="import.preview.button"):
            result = service.preview(token, content, uploaded.name, context=context, mapping=mapping, reader_options=reader_options)
            st.session_state[preview_key] = (signature, result)
        saved = st.session_state.get(preview_key)
        if saved is None:
            return
        if saved[0] != signature:
            st.info("Zmieniono plik, mapowanie lub kontekst. Przygotuj podgląd ponownie.")
            return
        preview = saved[1]
        st.subheader("Podgląd importu")
        st.write(f"Wiersze źródłowe: {preview.source_rows}. Pojedyncze godziny do zapisania: {len(preview.entries)}.")
        for issue in preview.issues:
            st.error((f"Wiersz {issue.row}: " if issue.row else "Kontrola harmonogramu: ") + issue.message)
        if preview.entries:
            st.dataframe([{
                "Wiersz pliku": row["SourceRow"], "Data": row["Date"], "Od": row["StartHour"], "Do": row["EndHour"],
                "Przedmiot": row["SubjectName"], "Grupa wykładowa": row["LectureGroupName"],
                "Wykładowca": row["TeacherName"], "Sala": row["LectureRoomName"],
            } for row in preview.entries], hide_index=True, use_container_width=True)
        st.caption("Zapis obejmie cały poprawny plan w jednej transakcji. Przy błędzie żadna godzina z tego importu nie zostanie zapisana.")
        if st.button("Zapisz plan w bazie", disabled=not preview.ready or not service.can(token, "create"), type="primary", key="import.commit"):
            created = service.commit(token, preview)
            del st.session_state[preview_key]
            st.success(f"Zapisano {len(created)} pojedynczych godzin w bazie.")
    except ApplicationError as error:
        st.error(str(error))
