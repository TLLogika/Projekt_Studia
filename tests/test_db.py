"""ODBC GUID representation must not break references across modules."""

from uuid import UUID

from academic.db import query


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
