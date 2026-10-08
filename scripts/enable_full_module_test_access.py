"""Jawne rozszerzenie istniejącego konta testowego na cały moduł sources.

Nie uruchamia się ze Streamlit. Wymaga zgody na konkretne dodatkowe uprawnienia.
Nie zmienia haseł, połączenia SQL, danych dydaktycznych ani struktury bazy.
"""

from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from academic.config import Settings
from academic.db import Database, one, query
from academic.errors import ApplicationError

ITEMS = (
    ("groups", "Grupy", "/groups", ("view", "create", "update")),
    ("lecture_groups", "Grupy wykładowe", "/lecture-groups", ("view", "create", "update")),
    ("lecture_group_types", "Typy grup wykładowych", "/lecture-group-types", ("view", "create", "update")),
    ("subjects", "Przedmioty", "/subjects", ("view", "create", "update")),
    ("subject_types", "Typy przedmiotów", "/subject-types", ("view", "create", "update")),
    ("subjects_semesters", "Przedmioty w semestrze", "/subjects-semesters", ("view", "create", "update")),
    ("dictionaries", "Słowniki", "/dictionaries", ("view", "create", "update")),
    ("organization", "Organizacja semestru", "/organization", ("view", "create", "update")),
    ("schedule", "Harmonogram i bilans", "/schedule", ("view", "create", "update")),
    ("schedule_import", "Import planu", "/import", ("view", "create")),
    ("calendar.dean", "Kalendarz dziekanatu", "/calendar", ("view",)),
)


def main():
    database = Database(Settings.load())
    with database.connection() as connection:
        query(connection, "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        identity = one(connection,
                       "SELECT r.Id AS RoleId FROM org.Accounts a JOIN org.Persons p ON p.Id=a.PersonId "
                       "JOIN org.PersonRoles pr ON pr.PersonId=p.Id JOIN org.Roles r ON r.Id=pr.RoleId "
                       "WHERE a.Login=? AND r.Code=? AND a.IsActive=1 AND p.IsActive=1 AND pr.IsActive=1 AND r.IsActive=1",
                       ("dziekanat.test", "semester_test_operator"))
        if identity is None:
            raise ApplicationError("Brak aktywnego konta dziekanat.test z przypisaną rolą testową.")
        role_id = str(identity["RoleId"])
        for position, (code, name, route, actions) in enumerate(ITEMS, 10):
            item = one(connection, "SELECT Id,IsActive,ShowInMenu FROM org.SystemItems WITH(UPDLOCK,HOLDLOCK) WHERE Code=?", (code,))
            if item is None:
                item = one(connection,
                           "INSERT INTO org.SystemItems (Code,Name,ItemType,Route,ComponentKey,SortOrder,ShowInMenu,IsActive) "
                           "OUTPUT INSERTED.Id,INSERTED.IsActive,INSERTED.ShowInMenu VALUES (?, ?, 'PAGE', ?, ?, ?, 1, 1)",
                           (code, name, route, code, position))
            if not item["IsActive"]:
                raise ApplicationError("Istniejąca pozycja menu jest wyłączona; nie zmieniono jej konfiguracji.")
            if not item["ShowInMenu"]:
                raise ApplicationError("Istniejąca pozycja menu jest ukryta; nie zmieniono jej konfiguracji.")
            for action in actions:
                if one(connection, "SELECT Id FROM org.RolePermissions WHERE RoleId=? AND SystemItemId=? AND ActionCode=? AND IsActive=1",
                       (role_id, str(item["Id"]), action)) is None:
                    query(connection, "INSERT INTO org.RolePermissions (RoleId,SystemItemId,ActionCode,IsActive) VALUES (?, ?, ?, 1)",
                          (role_id, str(item["Id"]), action))
    print("dziekanat.test: dostęp do całego modułu organizacji semestru został nadany.")


if __name__ == "__main__":
    try:
        main()
    except ApplicationError as error:
        print(str(error))
        raise SystemExit(1)
