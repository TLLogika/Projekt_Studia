"""Logiczne relacje notatek, uprawnienia obiektu i nieedytowalny audyt."""

from datetime import datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest
from streamlit.testing.v1 import AppTest

from test_catalogs import backend, VERSION, NEXT_VERSION
import academic.catalogs.service as catalogs
import academic.notes.service as module
from academic.auth.models import AuthorizationError, User
from academic.errors import ConflictError, ValidationError
from academic.notes.service import NotesService, NOTE_TARGETS


NOW = datetime(2026, 10, 8, 12, 30)
ACCOUNT_ID = str(uuid4())
PERSON_ID = str(uuid4())


@pytest.fixture
def notes_backend(backend, monkeypatch):
    _, db, auth = backend
    db.settings = SimpleNamespace(semesters_item_code="semesters")
    inspect = db.columns
    schema = {name: {"DATA_TYPE": kind, "IS_NULLABLE": nullable, "CHARACTER_MAXIMUM_LENGTH": length}
        for name, kind, nullable, length in (
            ("Table", "nvarchar", "YES", 100), ("TableId", "uniqueidentifier", "NO", None),
            ("Comment", "nvarchar", "YES", -1), ("Pin", "bit", "NO", None), ("IsActive", "bit", "NO", None),
            ("CreatedBy", "nvarchar", "YES", 100), ("ModifiedBy", "nvarchar", "YES", 100),
            ("CreatedAt", "datetime2", "NO", None), ("ModifiedAt", "datetime2", "NO", None),
        )}
    monkeypatch.setattr(db, "columns", lambda table, **kwargs: schema if table == "Notes" else inspect(table, **kwargs))
    for name in ("get_record", "list_records", "insert_record", "update_record", "acquire_write_lock"):
        monkeypatch.setattr(module, name, getattr(catalogs, name))
    auth.denied_items = set()
    monkeypatch.setattr(auth, "can", lambda token, code, action, connection=None: action in auth.actions and code not in auth.denied_items)
    monkeypatch.setattr(auth, "get_user", lambda token: User(PERSON_ID, ACCOUNT_ID, "Prawdziwa osoba", ("Rola",)), raising=False)
    sql_reads = []

    def clock_or_account(connection, sql, params=()):
        sql_reads.append((sql, params))
        if "Accounts" in sql:
            assert params == (ACCOUNT_ID, PERSON_ID)
            return {"Login": "prawdziwy.login"}
        if "SYSDATETIME" in sql:
            return {"ModifiedAt": NOW}
        raise AssertionError(sql)

    monkeypatch.setattr(module, "one", clock_or_account)
    target = db.add("Countries", Name="Kraj", Citizenship="Obywatelstwo", Code="XX", IsActive=True)
    return NotesService(db, auth), db, auth, target, sql_reads


def draft(target, **changes):
    return {"Table": "org.Countries", "TableId": target["Id"], "Comment": "Informacja do rekordu", "Pin": False, "IsActive": True, **changes}


def test_note_authorship_comes_from_logged_database_account_and_times_use_sql_default(notes_backend):
    service, db, _, target, _ = notes_backend
    saved = service.create("session", draft(target))
    assert saved["CreatedBy"] == saved["ModifiedBy"] == "prawdziwy.login"
    sent = db.writes[0][2]
    assert "CreatedAt" not in sent and "ModifiedAt" not in sent


@pytest.mark.parametrize("extra", [{"CreatedBy": "podszywanie"}, {"ModifiedBy": "podszywanie"}, {"CreatedAt": NOW}, {"Timestamp": VERSION}])
def test_caller_cannot_supply_audit_columns(notes_backend, extra):
    service, db, _, target, _ = notes_backend
    with pytest.raises(ValidationError):
        service.create("session", draft(target, **extra))
    assert db.writes == []


@pytest.mark.parametrize("table", ["org.Accounts", "org.Roles", "org.RolePermissions", "org.Notes", "Countries; DROP TABLE org.Subjects"])
def test_notes_exclude_security_tables_and_arbitrary_tables(notes_backend, table):
    service, db, _, target, _ = notes_backend
    with pytest.raises(ValidationError, match="dostępnej listy"):
        service.create("session", draft(target, Table=table))
    assert db.writes == []
    assert table not in NOTE_TARGETS


def test_target_must_be_a_real_existing_record(notes_backend):
    service, db, _, target, _ = notes_backend
    with pytest.raises(ValidationError, match="już nie istnieje"):
        service.create("session", draft(target, TableId=str(uuid4())))
    assert db.writes == []


def test_target_view_grant_is_required_for_create_and_list_hides_inaccessible_notes(notes_backend):
    service, db, auth, target, _ = notes_backend
    group = db.add("Groups", Name="Grupa", IsActive=True)
    db.add("Notes", **draft(group, Table="org.Groups"), CreatedAt=NOW)
    visible = db.add("Notes", **draft(target), CreatedAt=NOW)
    auth.denied_items.add("groups")
    with pytest.raises(AuthorizationError):
        service.create("session", draft(group, Table="org.Groups"))
    assert [note["Id"] for note in service.list("session")] == [visible["Id"]]
    assert "org.Groups" not in [item["Table"] for item in service.targets("session")]
    assert db.writes == []


def test_note_update_requires_both_old_and_new_target_view_grants(notes_backend):
    service, db, auth, target, _ = notes_backend
    group = db.add("Groups", Name="Grupa", IsActive=True)
    old = db.add("Notes", **draft(group, Table="org.Groups"), CreatedBy="pierwotny.autor", CreatedAt=NOW)
    auth.denied_items.add("groups")
    with pytest.raises(AuthorizationError):
        service.update("session", old["Id"], draft(target), VERSION)
    auth.denied_items.clear()
    auth.denied_items.add("subjects")
    subject = db.add("Subjects", Name="Przedmiot", IsActive=True)
    with pytest.raises(AuthorizationError):
        service.update("session", old["Id"], draft(subject, Table="org.Subjects"), VERSION)
    assert db.writes == []


def test_note_update_preserves_original_author_and_uses_sql_server_clock(notes_backend):
    service, db, _, target, _ = notes_backend
    note = db.add("Notes", **draft(target), CreatedBy="pierwotny.autor", CreatedAt=datetime(2026, 10, 1), ModifiedAt=datetime(2026, 10, 1))
    saved = service.update("session", note["Id"], draft(target, Comment="Zmieniona informacja"), VERSION)
    assert saved["CreatedBy"] == "pierwotny.autor"
    assert saved["CreatedAt"] == datetime(2026, 10, 1)
    assert saved["ModifiedBy"] == "prawdziwy.login" and saved["ModifiedAt"] == NOW
    assert saved["Timestamp"] == NEXT_VERSION


def test_note_snapshot_and_module_permission_cannot_be_bypassed(notes_backend):
    service, db, auth, target, _ = notes_backend
    note = db.add("Notes", **draft(target))
    with pytest.raises(ConflictError):
        service.update("session", note["Id"], draft(target), NEXT_VERSION)
    auth.actions.remove("create")
    with pytest.raises(AuthorizationError):
        service.create("session", draft(target))
    assert db.writes == []


def notes_ui_app():
    import streamlit as st
    from academic.ui.notes import render_notes
    render_notes(st.session_state["service"], "session")


def test_notes_ui_has_real_record_selectors_and_readonly_audit(notes_backend):
    service, db, _, target, _ = notes_backend
    app = AppTest.from_function(notes_ui_app)
    app.session_state["service"] = service
    app.run()
    app.button(key="note_add").click().run()
    assert not app.exception
    assert app.selectbox(key="note_field_TableId").options == ["Wybierz…"]
    assert all(input.disabled for input in app.text_input)
    app.selectbox(key="note_field_Table").set_value("org.Countries").run()
    assert not app.exception
    assert len(app.selectbox(key="note_field_TableId").options) == 2
    assert db.writes == []
    app.selectbox(key="note_field_TableId").set_value(target["Id"])
    app.text_area(key="note_field_Comment").set_value("Treść notatki").run()
    assert db.writes == []
    app.button(key="note_save").click().run()
    assert not app.exception
    assert len(db.writes) == 1
    assert db.writes[0][2]["CreatedBy"] == "prawdziwy.login"
