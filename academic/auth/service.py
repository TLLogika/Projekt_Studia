"""Existing SQL Server identities and grants; no schema/account mutations.

Explicitly configured local_file test credentials store hashes under secret
and still require the account, person and every permission in SQL Server.

Password sources are explicitly configured as AccountCredentials(AccountId,
PasswordHash[, IsActive]) or AspNetUsers(Id, PasswordHash[, IsActive]). The
latter is reached exclusively through org.Accounts.AspNetUserId, never by a
guessed matching login. A missing source prevents authentication.

Session tokens are opaque 256-bit secrets. Only SHA-256 token digests are
kept in process memory. Restarting the process revokes all of its sessions.
Grant validity is evaluated by SQL Server's UTC clock, with an exclusive
ValidTo. User identity and grants are re-read for every authorization check.
"""

from contextlib import nullcontext
from dataclasses import dataclass
import hashlib
import secrets
from threading import RLock
import time
from typing import Any

from academic.db import Database, identifier, one, query
from academic.errors import DatabaseError

from .models import AuthenticationError, AuthConfigurationError, AuthorizationError, User
from .passwords import verify_password
from .local_credentials import load_local_credentials


_INVALID_LOGIN = "Nieprawidłowy login lub hasło."
_EXPIRED_SESSION = "Sesja wygasła. Zaloguj się ponownie."
_ACTIONS = frozenset({"view", "create", "update"})
_PERSON_ROLES_VALID = """
    pr.IsActive = 1 AND r.IsActive = 1
    AND (pr.ValidFrom IS NULL OR pr.ValidFrom <= SYSUTCDATETIME())
    AND (pr.ValidTo IS NULL OR pr.ValidTo > SYSUTCDATETIME())
"""


@dataclass(slots=True)
class _Session:
    account_id: str
    person_id: str
    issued_at: float
    last_seen: float


def _identifier(value: str) -> str:
    if not isinstance(value, str):
        raise AuthConfigurationError("Nieprawidłowa konfiguracja źródła haseł.")
    try:
        return identifier(value)
    except DatabaseError:
        raise AuthConfigurationError("Nieprawidłowa konfiguracja źródła haseł.") from None


class AuthService:
    """Keep a single shared instance in the app process (e.g. cache_resource)."""

    def __init__(self, db: Database):
        self.db = db
        self._sessions: dict[bytes, _Session] = {}
        self._lock = RLock()
        self._ttl = db.settings.session_ttl_minutes * 60
        self._idle = db.settings.session_idle_minutes * 60
        if self._ttl <= 0 or self._idle <= 0:
            raise AuthConfigurationError("Czas ważności sesji musi być dodatni.")

    def _source(self, connection: Any) -> tuple[str | None, bool, bool]:
        settings = self.db.settings
        provider = getattr(settings, "auth_provider", "database")
        if provider == "local_file":
            load_local_credentials(settings)
            return None, False, False
        if provider != "database":
            raise AuthConfigurationError("Nieprawidłowy AUTH_PROVIDER w konfiguracji.")
        table = settings.auth_table
        schema = settings.auth_schema
        database = settings.auth_database
        if table not in {"AccountCredentials", "AspNetUsers"}:
            raise AuthConfigurationError(
                "Źródło haseł musi wskazywać istniejącą tabelę AccountCredentials lub AspNetUsers."
            )
        source = ".".join(_identifier(part) for part in (database, schema, table) if part is not None)
        if not self.db.table_exists(table, schema=schema, database=database, connection=connection):
            raise AuthConfigurationError(
                "Brak skonfigurowanego źródła haseł: wskazana tabela nie istnieje. "
                "Administrator musi wskazać rzeczywiste źródło haseł w konfiguracji AUTH_DATABASE, "
                "AUTH_SCHEMA i AUTH_TABLE."
            )
        catalog = f"{_identifier(database)}." if database is not None else ""
        columns = query(
            connection,
            f"SELECT COLUMN_NAME AS ColumnName FROM {catalog}INFORMATION_SCHEMA.COLUMNS "
            "WHERE TABLE_SCHEMA = ? AND TABLE_NAME = ?",
            (schema, table),
        )
        names = {str(row["ColumnName"]).casefold() for row in columns}
        is_identity = table == "AspNetUsers"
        required = {"passwordhash", "id" if is_identity else "accountid"}
        if not required.issubset(names):
            raise AuthConfigurationError(
                "Skonfigurowane źródło haseł nie zawiera wymaganych kolumn."
            )
        return source, is_identity, "isactive" in names

    def _credentials(self, connection: Any, source: tuple[str | None, bool, bool], account: dict) -> str | None:
        table, is_identity, has_active = source
        if table is None:
            return load_local_credentials(self.db.settings).get(str(account["AccountId"]).lower())
        key_column = "Id" if is_identity else "AccountId"
        key = account.get("AspNetUserId") if is_identity else account["AccountId"]
        if key is None:
            return None
        active = " AND IsActive = 1" if has_active else ""
        rows = query(
            connection,
            f"SELECT PasswordHash FROM {table} WHERE {key_column} = ?{active}",
            (key,),
        )
        # Ambiguous credentials must not silently select an arbitrary record.
        if len(rows) != 1 or not isinstance(rows[0].get("PasswordHash"), str):
            return None
        return rows[0]["PasswordHash"]

    @staticmethod
    def _account(connection: Any, account_id: str) -> dict | None:
        return one(
            connection,
            "SELECT a.Id AS AccountId, a.PersonId, p.FirstName1, p.LastName "
            "FROM org.Accounts a JOIN org.Persons p ON p.Id = a.PersonId "
            "WHERE a.Id = ? AND a.IsActive = 1 AND p.IsActive = 1",
            (account_id,),
        )

    @staticmethod
    def _user(connection: Any, account: dict) -> User:
        roles = query(
            connection,
            "SELECT DISTINCT r.Code, r.Name FROM org.PersonRoles pr "
            "JOIN org.Roles r ON r.Id = pr.RoleId "
            f"WHERE pr.PersonId = ? AND {_PERSON_ROLES_VALID} ORDER BY r.Name, r.Code",
            (account["PersonId"],),
        )
        name = " ".join(str(account[field] or "").strip() for field in ("FirstName1", "LastName")).strip()
        return User(
            person_id=str(account["PersonId"]),
            account_id=str(account["AccountId"]),
            display_name=name,
            roles=tuple(str(row["Name"] if row["Name"] is not None else row["Code"]) for row in roles),
        )

    @staticmethod
    def _digest(token: str) -> bytes:
        if not isinstance(token, str) or not 32 <= len(token) <= 128:
            raise AuthenticationError(_EXPIRED_SESSION)
        try:
            return hashlib.sha256(token.encode("utf-8")).digest()
        except UnicodeError:
            raise AuthenticationError(_EXPIRED_SESSION) from None

    def _prune(self, now: float) -> None:
        for digest, session in tuple(self._sessions.items()):
            if now - session.issued_at >= self._ttl or now - session.last_seen >= self._idle:
                del self._sessions[digest]

    def _session(self, token: str, *, touch: bool = False) -> _Session:
        digest = self._digest(token)
        with self._lock:
            now = time.monotonic()
            self._prune(now)
            session = self._sessions.get(digest)
            if session is None:
                raise AuthenticationError(_EXPIRED_SESSION)
            if touch:
                session.last_seen = now
            return _Session(session.account_id, session.person_id, session.issued_at, session.last_seen)

    def login(self, login: str, password: str) -> str:
        if not isinstance(login, str) or not isinstance(password, str) or not login.strip() or not password:
            raise AuthenticationError(_INVALID_LOGIN)
        if len(login) > 320:
            raise AuthenticationError(_INVALID_LOGIN)
        with self.db.connection() as connection:
            source = self._source(connection)
            accounts = query(
                connection,
                "SELECT a.Id AS AccountId, a.PersonId, a.AspNetUserId, p.FirstName1, p.LastName "
                "FROM org.Accounts a JOIN org.Persons p ON p.Id = a.PersonId "
                "WHERE (a.Login = ? OR a.Email = ?) AND a.IsActive = 1 AND p.IsActive = 1",
                (login.strip(), login.strip()),
            )
            if len(accounts) != 1:
                raise AuthenticationError(_INVALID_LOGIN)
            account = accounts[0]
            hashed_password = self._credentials(connection, source, account)
            if hashed_password is None or not verify_password(password, hashed_password):
                raise AuthenticationError(_INVALID_LOGIN)
            self._user(connection, account)
        token = secrets.token_urlsafe(32)
        now = time.monotonic()
        with self._lock:
            self._prune(now)
            self._sessions[self._digest(token)] = _Session(
                str(account["AccountId"]), str(account["PersonId"]), now, now
            )
        return token

    def _get_user(self, token: str, connection: Any) -> User:
        session = self._session(token)
        account = self._account(connection, session.account_id)
        if account is None or str(account["PersonId"]) != session.person_id:
            self.logout(token)
            raise AuthenticationError(_EXPIRED_SESSION)
        user = self._user(connection, account)
        self._session(token, touch=True)
        return user

    def get_user(self, token: str) -> User:
        self._session(token)
        with self.db.connection() as connection:
            return self._get_user(token, connection)

    def logout(self, token: str) -> None:
        try:
            digest = self._digest(token)
        except AuthenticationError:
            return
        with self._lock:
            self._sessions.pop(digest, None)

    @staticmethod
    def _allowed(connection: Any, user: User, item_code: str, action: str) -> bool:
        if action not in _ACTIONS or not isinstance(item_code, str) or not item_code:
            return False
        row = one(
            connection,
            f"""
            SELECT
                CASE WHEN EXISTS (
                    SELECT 1 FROM org.PersonPermissionOverrides o
                    WHERE o.PersonId = ? AND o.SystemItemId = si.Id
                      AND o.ActionCode = ? AND o.IsAllowed = 0 AND o.IsActive = 1
                      AND (o.ValidFrom IS NULL OR o.ValidFrom <= SYSUTCDATETIME())
                      AND (o.ValidTo IS NULL OR o.ValidTo > SYSUTCDATETIME())
                ) THEN 1 ELSE 0 END AS HasDeny,
                CASE WHEN EXISTS (
                    SELECT 1 FROM org.PersonPermissionOverrides o
                    WHERE o.PersonId = ? AND o.SystemItemId = si.Id
                      AND o.ActionCode = ? AND o.IsAllowed = 1 AND o.IsActive = 1
                      AND (o.ValidFrom IS NULL OR o.ValidFrom <= SYSUTCDATETIME())
                      AND (o.ValidTo IS NULL OR o.ValidTo > SYSUTCDATETIME())
                ) THEN 1 ELSE 0 END AS HasAllow,
                CASE WHEN EXISTS (
                    SELECT 1 FROM org.PersonRoles pr
                    JOIN org.Roles r ON r.Id = pr.RoleId
                    JOIN org.RolePermissions rp ON rp.RoleId = r.Id
                    WHERE pr.PersonId = ? AND {_PERSON_ROLES_VALID}
                      AND rp.SystemItemId = si.Id AND rp.ActionCode = ? AND rp.IsActive = 1
                ) THEN 1 ELSE 0 END AS HasRole
            FROM org.SystemItems si WHERE si.Code = ? AND si.IsActive = 1
            """,
            (user.person_id, action, user.person_id, action, user.person_id, action, item_code),
        )
        return bool(row and not row["HasDeny"] and (row["HasAllow"] or row["HasRole"]))

    def can(self, token: str, item_code: str, action: str, connection: Any = None) -> bool:
        try:
            self._session(token)
            with (nullcontext(connection) if connection is not None else self.db.connection()) as conn:
                user = self._get_user(token, conn)
                allowed = self._allowed(conn, user, item_code, action)
                self._session(token)
                return allowed
        except AuthenticationError:
            return False

    def require(self, token: str, item_code: str, action: str, connection: Any = None) -> None:
        if not self.can(token, item_code, action, connection=connection):
            raise AuthorizationError("Brak uprawnień do wykonania tej operacji.")

    def menu(self, token: str) -> list[dict]:
        self._session(token)
        with self.db.connection() as connection:
            user = self._get_user(token, connection)
            items = query(
                connection,
                "SELECT Id, ParentItemId, Code, Name, Route, ComponentKey, SortOrder "
                "FROM org.SystemItems WHERE IsActive = 1 AND ShowInMenu = 1 "
                "ORDER BY SortOrder, Name, Code",
            )
            permitted = [item for item in items if self._allowed(connection, user, item["Code"], "view")]
            self._session(token)
            return permitted

    def presentation(self, token: str) -> tuple[User, list[dict], dict]:
        """Jedno połączenie dla profilu, menu i wszystkich akcji UI.

        Wynik dotyczy bieżącego renderowania. Zapisy nadal używają require()
        i sprawdzają aktualne uprawnienia we własnej transakcji.
        """
        self._session(token)
        with self.db.connection() as connection:
            user = self._get_user(token, connection)
            rows = query(connection, f"""
                SELECT si.Id,si.ParentItemId,si.Code,si.Name,si.Route,
                       si.ComponentKey,si.SortOrder,si.ShowInMenu,actions.ActionCode,
                       CASE WHEN EXISTS (
                         SELECT 1 FROM org.PersonPermissionOverrides o
                         WHERE o.PersonId=? AND o.SystemItemId=si.Id
                           AND o.ActionCode=actions.ActionCode AND o.IsAllowed=0 AND o.IsActive=1
                           AND (o.ValidFrom IS NULL OR o.ValidFrom<=SYSUTCDATETIME())
                           AND (o.ValidTo IS NULL OR o.ValidTo>SYSUTCDATETIME())
                       ) THEN 1 ELSE 0 END AS HasDeny,
                       CASE WHEN EXISTS (
                         SELECT 1 FROM org.PersonPermissionOverrides o
                         WHERE o.PersonId=? AND o.SystemItemId=si.Id
                           AND o.ActionCode=actions.ActionCode AND o.IsAllowed=1 AND o.IsActive=1
                           AND (o.ValidFrom IS NULL OR o.ValidFrom<=SYSUTCDATETIME())
                           AND (o.ValidTo IS NULL OR o.ValidTo>SYSUTCDATETIME())
                       ) THEN 1 ELSE 0 END AS HasAllow,
                       CASE WHEN EXISTS (
                         SELECT 1 FROM org.PersonRoles pr
                         JOIN org.Roles r ON r.Id=pr.RoleId
                         JOIN org.RolePermissions rp ON rp.RoleId=r.Id
                         WHERE pr.PersonId=? AND {_PERSON_ROLES_VALID}
                           AND rp.SystemItemId=si.Id AND rp.ActionCode=actions.ActionCode AND rp.IsActive=1
                       ) THEN 1 ELSE 0 END AS HasRole
                FROM org.SystemItems si
                CROSS JOIN (VALUES ('view'),('create'),('update')) actions(ActionCode)
                WHERE si.IsActive=1 ORDER BY si.SortOrder,si.Name,si.Code,actions.ActionCode
            """, (user.person_id, user.person_id, user.person_id))
            permissions = {
                (row['Code'], row['ActionCode']): bool(not row['HasDeny'] and (row['HasAllow'] or row['HasRole']))
                for row in rows
            }
            menu = [
                {key: row[key] for key in ('Id','ParentItemId','Code','Name','Route','ComponentKey','SortOrder')}
                for row in rows if row['ActionCode']=='view' and row['ShowInMenu']
                and permissions[(row['Code'], 'view')]
            ]
            self._session(token)
            return user, menu, permissions
