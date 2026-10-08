"""Osobne konto do testów nowych modułów, bez zmian struktury i danych zajęć."""

import json
from pathlib import Path
import secrets
import sys

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from argon2 import PasswordHasher
from academic.auth.local_credentials import credentials_path, load_local_credentials
from academic.config import ROOT, Settings
from academic.db import Database, one, query
from academic.errors import ApplicationError
from scripts.create_test_account import write_secret
from scripts.enable_full_module_test_access import ITEMS

LOGIN = "dziekanat.moduly.test"
ROLE_CODE = "full_module_test_operator"
RECOVERY = ROOT / "secret" / "full-module-test-account.txt"


def main():
    settings = Settings.load()
    if settings.auth_provider != "local_file":
        raise ApplicationError("To konto korzysta z już zatwierdzonego dostawcy local_file; konfiguracja nie została zmieniona.")
    path = credentials_path(settings)
    accounts = load_local_credentials(settings)
    database = Database(settings)
    with database.connection() as connection:
        query(connection, "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        existing = query(connection,
            "SELECT a.Id,a.PersonId,a.IsActive,p.FirstName1,p.LastName,p.IsActive AS PersonActive "
            "FROM org.Accounts a WITH(UPDLOCK,HOLDLOCK) JOIN org.Persons p ON p.Id=a.PersonId WHERE a.Login=?", (LOGIN,))
        if len(existing) > 1:
            raise ApplicationError("Login nie jest jednoznaczny; nie zmieniono kont.")
        if existing:
            account = existing[0]
            if (account['FirstName1'], account['LastName']) != ('Konto', 'Testowe modułów dziekanatu'):
                raise ApplicationError("Login zajęty przez inną osobę.")
            if not account['IsActive'] or not account['PersonActive']:
                raise ApplicationError("Istniejące konto jest wyłączone.")
            account_id, person_id = str(account['Id']), str(account['PersonId'])
        else:
            person_id = str(one(connection,
                "INSERT INTO org.Persons (FirstName1,LastName,IsActive) OUTPUT INSERTED.Id VALUES (?, ?, 1)",
                ('Konto', 'Testowe modułów dziekanatu'))['Id'])
            account_id = str(one(connection,
                "INSERT INTO org.Accounts (PersonId,Login,IsPrimary,IsActive) OUTPUT INSERTED.Id VALUES (?, ?, 1, 1)",
                (person_id, LOGIN))['Id'])
        role = one(connection, "SELECT Id,IsActive FROM org.Roles WITH(UPDLOCK,HOLDLOCK) WHERE Code=?", (ROLE_CODE,))
        if role is None:
            role = one(connection,
                "INSERT INTO org.Roles (Code,Name,Description,IsActive) OUTPUT INSERTED.Id,INSERTED.IsActive VALUES (?, ?, ?, 1)",
                (ROLE_CODE, 'Testy — organizacja semestru', 'Osobne konto testowe nowych modułów na życzenie użytkownika.'))
        if not role['IsActive']:
            raise ApplicationError("Rola testowa jest wyłączona.")
        role_id = str(role['Id'])
        if one(connection, "SELECT Id FROM org.PersonRoles WHERE PersonId=? AND RoleId=? AND IsActive=1", (person_id, role_id)) is None:
            query(connection, "INSERT INTO org.PersonRoles (PersonId,RoleId,IsActive) VALUES (?, ?, 1)", (person_id, role_id))
        items = ((settings.semesters_item_code, 'Semestr', '/semesters', ('view','create','update')), ) + ITEMS
        for position, (code, name, route, actions) in enumerate(items):
            item = one(connection, "SELECT Id,IsActive,ShowInMenu FROM org.SystemItems WITH(UPDLOCK,HOLDLOCK) WHERE Code=?", (code,))
            if item is None:
                item = one(connection,
                    "INSERT INTO org.SystemItems (Code,Name,ItemType,Route,ComponentKey,SortOrder,ShowInMenu,IsActive) "
                    "OUTPUT INSERTED.Id,INSERTED.IsActive,INSERTED.ShowInMenu VALUES (?, ?, 'PAGE', ?, ?, ?, 1, 1)",
                    (code, name, route, code, position))
            if not item['IsActive'] or not item['ShowInMenu']:
                raise ApplicationError("Istniejąca pozycja menu jest wyłączona lub ukryta.")
            for action in actions:
                if one(connection, "SELECT Id FROM org.RolePermissions WHERE RoleId=? AND SystemItemId=? AND ActionCode=? AND IsActive=1", (role_id, str(item['Id']), action)) is None:
                    query(connection, "INSERT INTO org.RolePermissions (RoleId,SystemItemId,ActionCode,IsActive) VALUES (?, ?, ?, 1)", (role_id, str(item['Id']), action))
        if account_id.lower() not in accounts:
            password = 'Test-' + secrets.token_urlsafe(15)
            accounts[account_id.lower()] = PasswordHasher().hash(password)
            write_secret(path, json.dumps({'version': 1, 'accounts': accounts}, indent=2) + '\n')
            write_secret(RECOVERY, f'Login: {LOGIN}\nHasło: {password}\n')
        elif not RECOVERY.exists():
            raise ApplicationError("Konto ma już hash, lecz nie ma pliku odzyskania hasła; hasło nie zostało zmienione.")
    print('Osobne konto testowe utworzone z dostępem do wszystkich modułów dziekanatu.')
    print('Dane logowania: secret/full-module-test-account.txt')


if __name__ == '__main__':
    try:
        main()
    except ApplicationError as error:
        print(str(error))
        raise SystemExit(1)
