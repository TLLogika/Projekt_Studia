"""ODBC GUID representation must not break references across modules."""

from uuid import UUID

from academic.db import query
from academic.db import Database
from types import SimpleNamespace


def test_odbc_uppercase_guid_matches_canonical_reference_without_changing_text():
    guid = "71A0CF92-4C76-42BF-8E09-65A2C18B5C28"
    class Cursor:
        description = [(name,) for name in ("Id", "SubjectId", "Name", "TeamsGroupID")]
        closed = False
        def execute(self, sql, params):
            return self
        def fetchall(self):
            return [(guid, UUID(guid), guid, "teams-value")]
        def close(self):
            self.closed = True
    class Connection:
        instance = Cursor()
        def cursor(self):
            return self.instance
    connection = Connection()
    row, = query(connection, "SELECT existing_records")
    assert row["Id"] == row["SubjectId"] == guid.lower()
    assert row["Name"] == guid
    assert row["TeamsGroupID"] == "teams-value"
    assert connection.instance.closed


def test_immutable_schema_metadata_is_fetched_once_and_isolated_from_caller_changes(monkeypatch):
    import academic.db as module
    calls = []
    def metadata(connection, sql, params):
        calls.append(params)
        return [{'COLUMN_NAME': 'Name', 'DATA_TYPE': 'varchar', 'CHARACTER_MAXIMUM_LENGTH': 100}]
    monkeypatch.setattr(module, 'query', metadata)
    database = Database(SimpleNamespace())
    first = database.columns('Studies', connection=object())
    first['Name']['CHARACTER_MAXIMUM_LENGTH'] = 1
    assert database.columns('Studies', connection=object())['Name']['CHARACTER_MAXIMUM_LENGTH'] == 100
    assert calls == [('org', 'Studies')]
    database.columns('Subjects', connection=object())
    assert len(calls) == 2
