"""Diagnostyka tylko do odczytu: python -m academic.diagnostics."""

from academic.config import Settings
from academic.db import Database, one
from academic.errors import ApplicationError
from academic.auth.local_credentials import load_local_credentials


def main() -> int:
    try:
        db = Database(Settings.load())
        with db.connection() as connection:
            actual_database = one(connection, "SELECT DB_NAME() AS Name")["Name"]
            print("Połączenie z SQL Server: OK")
            print("Baza:", actual_database)
            required = [
                "Semesters", "Persons", "Accounts", "Roles", "PersonRoles",
                "SystemItems", "RolePermissions", "PersonPermissionOverrides",
            ]
            incomplete = False
            for table in required:
                if not db.table_exists(table, connection=connection):
                    print(f"org.{table}: brak tabeli")
                    incomplete = True
                    continue
                count = one(connection, f"SELECT COUNT_BIG(*) AS n FROM [org].[{table}]")["n"]
                print(f"org.{table}: {count} rekordów")
                if table not in {"Semesters", "PersonPermissionOverrides"} and count == 0:
                    incomplete = True
            columns = db.columns("Semesters", connection=connection)
            for name in ["Name", "AcademicYearName", "StartDate", "EndDate", "Timestamp"]:
                column = columns.get(name)
                if column:
                    print(f"Semesters.{name}: {column['DATA_TYPE']}, "
                          f"limit={column['CHARACTER_MAXIMUM_LENGTH']}, "
                          f"codepage={column['CODE_PAGE']}")
                else:
                    incomplete = True
                    print(f"Semesters.{name}: brak kolumny")
            settings = db.settings
            if settings.auth_provider == "local_file":
                has_credentials = bool(load_local_credentials(settings))
                print("Lokalne hashe kont testowych:", "dostępne" if has_credentials else "brak")
            else:
                has_credentials = db.table_exists(
                    settings.auth_table, settings.auth_schema, settings.auth_database,
                    connection=connection,
                )
                print("Skonfigurowana tabela haseł:", "dostępna" if has_credentials else "brak")
            if incomplete or not has_credentials:
                print("Logowanie i uprawnienia wymagają konfiguracji rzeczywistych kont.")
                return 2
            print("Wymagane obiekty i dane dostępne; zweryfikuj rzeczywiste logowanie w aplikacji.")
            return 0
    except ApplicationError as error:
        print(str(error))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
