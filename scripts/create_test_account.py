"""Ręczne utworzenie konta testowego bez zmiany struktury bazy.

Uruchom z katalogu projektu: .venv/bin/python scripts/create_test_account.py
Skrypt nie uruchamia się podczas startu aplikacji. Ponowne wywołanie zachowuje
istniejące konto oraz hasło, jeśli lokalny plik poświadczeń jest kompletny.
"""

import json
import os
from pathlib import Path
import secrets
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from argon2 import PasswordHasher

from academic.auth.local_credentials import credentials_path, load_local_credentials
from academic.config import ROOT, Settings
from academic.db import Database, one, query
from academic.errors import ApplicationError

LOGIN = "dziekanat.test"
ROLE_CODE = "semester_test_operator"


def write_secret(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".account-")
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            stream.write(content)
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def configure_local_provider():
    path = ROOT / "secret" / ".env"
    lines = path.read_text(encoding="utf-8").splitlines()
    keys = {"AUTH_PROVIDER", "AUTH_LOCAL_CREDENTIALS_FILE"}
    # Zastępujemy wyłącznie konfigurację źródła haseł; połączenie SQL zostaje.
    lines = [line for line in lines if line.partition("=")[0].strip().upper() not in keys
             and line.partition(":")[0].strip().upper() not in keys]
    lines.extend(["AUTH_PROVIDER=local_file", "AUTH_LOCAL_CREDENTIALS_FILE=secret/test_accounts.json"])
    write_secret(path, "\n".join(lines) + "\n")


def main():
    settings = Settings.load()
    database = Database(settings)
    path = credentials_path(settings)
    accounts = load_local_credentials(settings) if path.exists() else {}
    password = None
    with database.connection() as connection:
        query(connection, "SET TRANSACTION ISOLATION LEVEL SERIALIZABLE")
        existing = query(
            connection,
            "SELECT a.Id, a.PersonId, a.IsActive, p.FirstName1, p.LastName, p.IsActive AS PersonActive "
            "FROM org.Accounts a WITH(UPDLOCK,HOLDLOCK) JOIN org.Persons p ON p.Id=a.PersonId "
            "WHERE a.Login=?", (LOGIN,)
        )
        if len(existing) > 1:
            raise ApplicationError("Login testowy nie jest jednoznaczny; nie zmieniono kont.")
        if existing:
            account = existing[0]
            if (account["FirstName1"], account["LastName"]) != ("Konto", "Testowe dziekanatu"):
                raise ApplicationError("Login zajęty przez inne konto; nie zmieniono jego danych.")
            if not account["IsActive"] or not account["PersonActive"]:
                raise ApplicationError("Istniejące konto testowe jest wyłączone.")
            account_id, person_id = str(account["Id"]), str(account["PersonId"])
        else:
            person_id = str(one(
                connection, "INSERT INTO org.Persons (FirstName1,LastName,IsActive) "
                "OUTPUT INSERTED.Id VALUES (?, ?, 1)", ("Konto", "Testowe dziekanatu")
            )["Id"])
            account_id = str(one(
                connection, "INSERT INTO org.Accounts (PersonId,Login,IsPrimary,IsActive) "
                "OUTPUT INSERTED.Id VALUES (?, ?, 1, 1)", (person_id, LOGIN)
            )["Id"])
        role = one(connection, "SELECT Id FROM org.Roles WITH(UPDLOCK,HOLDLOCK) WHERE Code=?", (ROLE_CODE,))
        if role is None:
            role = one(connection,
                       "INSERT INTO org.Roles (Code,Name,Description,IsActive) OUTPUT INSERTED.Id VALUES (?, ?, ?, 1)",
                       (ROLE_CODE, "Testy — semestry", "Konto do testów modułu semestrów, utworzone na życzenie użytkownika."))
        role_id = str(role["Id"])
        item = one(connection, "SELECT Id FROM org.SystemItems WITH(UPDLOCK,HOLDLOCK) WHERE Code=?", (settings.semesters_item_code,))
        if item is None:
            item = one(connection,
                       "INSERT INTO org.SystemItems (Code,Name,ItemType,Route,ComponentKey,ShowInMenu,IsActive) "
                       "OUTPUT INSERTED.Id VALUES (?, ?, 'PAGE', ?, ?, 1, 1)",
                       (settings.semesters_item_code, "Semestr", "/semesters", "semesters"))
        item_id = str(item["Id"])
        if one(connection, "SELECT Id FROM org.PersonRoles WHERE PersonId=? AND RoleId=? AND IsActive=1",
               (person_id, role_id)) is None:
            query(connection, "INSERT INTO org.PersonRoles (PersonId,RoleId,IsActive) VALUES (?, ?, 1)", (person_id, role_id))
        for action in ("view", "create", "update"):
            if one(connection, "SELECT Id FROM org.RolePermissions WHERE RoleId=? AND SystemItemId=? AND ActionCode=? AND IsActive=1",
                   (role_id, item_id, action)) is None:
                query(connection, "INSERT INTO org.RolePermissions (RoleId,SystemItemId,ActionCode,IsActive) VALUES (?, ?, ?, 1)",
                      (role_id, item_id, action))
        if account_id.lower() not in accounts:
            password = "Test-" + secrets.token_urlsafe(15)
            accounts[account_id.lower()] = PasswordHasher().hash(password)
            write_secret(path, json.dumps({"version": 1, "accounts": accounts}, indent=2) + "\n")
            write_secret(ROOT / "secret" / "test-account.txt", f"Login: {LOGIN}\nHasło: {password}\n")
    configure_local_provider()
    print(f"Konto {LOGIN}: utworzone i przypisane do uprawnień view/create/update semestrów.")
    print("Dane logowania: secret/test-account.txt. Weryfikacja hasła używa wyłącznie hasha Argon2.")


if __name__ == "__main__":
    try:
        main()
    except ApplicationError as error:
        print(str(error))
        raise SystemExit(1)
