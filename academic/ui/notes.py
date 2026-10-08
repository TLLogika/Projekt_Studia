"""Notatki do wybranego rzeczywistego rekordu; autor i daty tylko do odczytu."""

from datetime import datetime

import streamlit as st

from academic.errors import ApplicationError


def _timestamp(value):
    return "0x" + value.hex().upper() if isinstance(value, bytes) else str(value or "")


def _date(value):
    return value.strftime("%d.%m.%Y %H:%M:%S") if isinstance(value, datetime) else str(value or "")


def _open(note=None):
    for key in list(st.session_state):
        if key.startswith("note_field_"):
            del st.session_state[key]
    st.session_state["note_editor"] = dict(note) if note is not None else "new"


def _close():
    st.session_state.pop("note_editor", None)
    for key in list(st.session_state):
        if key.startswith("note_field_"):
            del st.session_state[key]


def _editor(service, token, note):
    with st.container(border=True):
        st.subheader("Edycja notatki" if note else "Dodawanie notatki")
        targets = service.targets(token)
        titles = {row["Table"]: row["title"] for row in targets}
        tables = [None] + list(titles)
        current_table = note.get("Table") if note else None
        table = st.selectbox("Obiekt", tables, index=tables.index(current_table) if current_table in tables else 0,
            format_func=lambda value: titles.get(value, "Wybierz…"), key="note_field_Table")
        rows = service.records(token, table) if table else []
        names = {str(row["Id"]): row["_label"] + " · " + str(row["Id"])[:8] for row in rows}
        ids = [None] + list(names)
        current_id = str(note["TableId"]) if note and table == current_table else None
        with st.form("note_editor_form", border=False):
            target_id = st.selectbox("Rekord", ids, index=ids.index(current_id) if current_id in ids else 0,
                format_func=lambda value: names.get(value, "Wybierz…"), key="note_field_TableId")
            if table and not rows:
                st.caption("Brak rekordów w wybranym katalogu.")
            comment = st.text_area("Treść notatki", value=note.get("Comment") or "" if note else "", key="note_field_Comment")
            pin = st.toggle("Przypięta", value=bool(note.get("Pin")) if note else False, key="note_field_Pin")
            active = st.toggle("Aktywna", value=bool(note.get("IsActive")) if note else True, key="note_field_IsActive")
            for field, label, formatter in (
                ("CreatedBy", "Autor", str), ("CreatedAt", "Utworzono", _date),
                ("ModifiedBy", "Ostatnio zmienił", str), ("ModifiedAt", "Ostatnia zmiana", _date),
                ("Timestamp", "Znacznik czasu (Timestamp)", _timestamp),
            ):
                value = note.get(field) if note else None
                st.text_input(label, value=formatter(value) if value is not None else "", disabled=True,
                    placeholder="Nadawane automatycznie", key="note_readonly_" + field)
            left, right = st.columns([3, 1])
            with left:
                save = st.form_submit_button("Zapisz zmiany" if note else "Utwórz notatkę", type="primary", use_container_width=True, key="note_save")
            with right:
                st.form_submit_button("Anuluj", on_click=_close, use_container_width=True, key="note_cancel")
        if save:
            if not table or not target_id:
                st.error("Wybierz obiekt i rzeczywisty rekord notatki.")
                return
            values = {"Table": table, "TableId": target_id, "Comment": comment, "Pin": pin, "IsActive": active}
            try:
                if note:
                    service.update(token, str(note["Id"]), values, note["Timestamp"])
                else:
                    service.create(token, values)
            except ApplicationError as error:
                st.error(str(error))
                return
            st.session_state["note_notice"] = "Zmiany notatki zostały zapisane." if note else "Notatka została utworzona."
            _close()
            st.rerun()


def render_notes(service, token):
    try:
        notes = service.list(token)
        can_create = service.can(token, "create")
        can_update = service.can(token, "update")
        if notice := st.session_state.pop("note_notice", None):
            st.success(notice)
        if can_create:
            st.button("Dodaj notatkę", use_container_width=True, on_click=_open, key="note_add")
        snapshot = st.session_state.get("note_editor")
        if snapshot == "new" and can_create:
            _editor(service, token, None)
        elif isinstance(snapshot, dict) and can_update and any(str(note["Id"]) == str(snapshot["Id"]) for note in notes):
            _editor(service, token, snapshot)
        elif snapshot is not None:
            _close()
        if not notes:
            st.info("Brak notatek do dostępnych rekordów.")
        for note in notes:
            label = ("📌 " if note.get("Pin") else "") + note["_target_title"] + " · " + note["_record_label"]
            if not note.get("IsActive"):
                label += " (nieaktywna)"
            with st.expander(label, expanded=bool(note.get("Pin"))):
                st.text(note.get("Comment") or "")
                st.caption("Autor: " + str(note.get("CreatedBy") or "") + " · Utworzono: " + _date(note.get("CreatedAt")))
                st.caption("Ostatnio zmienił: " + str(note.get("ModifiedBy") or "") + " · Ostatnia zmiana: " + _date(note.get("ModifiedAt")))
                st.caption("Znacznik czasu: " + _timestamp(note.get("Timestamp")))
                if can_update:
                    st.button("Edytuj", key="note_edit_" + str(note["Id"]), on_click=_open, args=(note,))
    except ApplicationError as error:
        st.error(str(error))
