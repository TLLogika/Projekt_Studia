"""Isolated security checks. Fixtures never connect to or populate the real DB."""

import base64
from contextlib import contextmanager
import hashlib
import struct
from types import SimpleNamespace

import bcrypt
import pytest
from argon2 import PasswordHasher

from academic.auth import (
    AuthenticationError,
    AuthConfigurationError,
    AuthorizationError,
    AuthService,
)
from academic.auth import passwords
from academic.auth import service


PASSWORD = "Testowe hasło żółć"


def identity_hash(prf=2, rounds=1000, version=3):
    salt = b"0123456789abcdef"
    digest = {0: "sha1", 1: "sha256", 2: "sha512"}[prf] if version == 3 else "sha1"
    key = hashlib.pbkdf2_hmac(digest, PASSWORD.encode(), salt, rounds, dklen=32)
    header = b"\x01" + struct.pack(">III", prf, rounds, len(salt)) if version == 3 else b"\x00"
    return base64.b64encode(header + salt + key).decode()


def django_hash(digest="sha256"):
    result = hashlib.pbkdf2_hmac(digest, PASSWORD.encode(), b"test-salt", 1000)
    return f"pbkdf2_{digest}$1000$test-salt$" + base64.b64encode(result).decode()


def werkzeug_hash(digest="sha256"):
    result = hashlib.pbkdf2_hmac(digest, PASSWORD.encode(), b"test-salt", 1000)
    return f"pbkdf2:{digest}:1000$test-salt$" + result.hex()


@pytest.mark.parametrize("encoded", [
    identity_hash(version=2),
    identity_hash(prf=0),
    identity_hash(prf=1),
    identity_hash(prf=2),
    django_hash("sha1"),
    django_hash("sha256"),
    werkzeug_hash("sha256"),
    werkzeug_hash("sha512"),
    bcrypt.hashpw(PASSWORD.encode(), bcrypt.gensalt(rounds=4)).decode(),
    PasswordHasher(time_cost=1, memory_cost=1024, parallelism=1).hash(PASSWORD),
])
def test_supported_existing_hashes_verify_and_reject_a_wrong_password(encoded):
    assert passwords.verify_password(PASSWORD, encoded)
    assert not passwords.verify_password(PASSWORD + "!", encoded)


@pytest.mark.parametrize("encoded", [
    PASSWORD,
    "",
    "5f4dcc3b5aa765d61d8327deb882cf99",
    "not-base64!",
    "pbkdf2_sha256$bad$salt$Zm9v",
    "pbkdf2_sha256$1000$salt$Zm9v",
    "pbkdf2:md5:1000$salt$00",
    "$argon2id$v=19$m=1,t=1,p=1$c2FsdA$aGFzaA",
    base64.b64encode(b"\x02" + b"x" * 48).decode(),
    "x" * 4097,
])
def test_plaintext_unsupported_and_malformed_hashes_fail_closed(encoded):
    assert not passwords.verify_password(PASSWORD, encoded)


def test_excessive_pbkdf2_cost_is_rejected_before_running_the_kdf(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Untrusted excessive cost reached the KDF")

    monkeypatch.setattr(passwords.hashlib, "pbkdf2_hmac", forbidden)
    raw = b"\x01" + struct.pack(">III", 2, 0xFFFFFFFF, 16) + b"s" * 16 + b"h" * 32
    assert not passwords.verify_password(PASSWORD, base64.b64encode(raw).decode())
    assert not passwords.verify_password(PASSWORD, "pbkdf2_sha256$2000001$salt$" + "a" * 43 + "=")


def test_bcrypt_and_argon2_resource_limits_reject_before_kdf(monkeypatch):
    def forbidden(*args, **kwargs):
        pytest.fail("Untrusted excessive cost reached the KDF")

    monkeypatch.setattr(passwords.bcrypt, "checkpw", forbidden)
    monkeypatch.setattr(passwords, "_ARGON2", SimpleNamespace(verify=forbidden))
    assert not passwords.verify_password(PASSWORD, "$2b$31$" + "a" * 53)
    assert not passwords.verify_password("x" * 73, "$2b$04$" + "a" * 53)
    assert not passwords.verify_password(PASSWORD, "$argon2id$v=19$m=999999999,t=1,p=1$c2FsdA$aGFzaA")
    assert not passwords.verify_password("x" * 4097, identity_hash())


class FixtureDatabase:
    def __init__(self):
        self.settings = SimpleNamespace(
            auth_database=None, auth_schema="org", auth_table="AccountCredentials",
            session_ttl_minutes=10, session_idle_minutes=2,
        )
        self.exists = True
        self.opens = 0
        self.conn = object()

    @contextmanager
    def connection(self):
        self.opens += 1
        yield self.conn

    def table_exists(self, table, schema="org", database=None, connection=None):
        assert connection is self.conn
        return self.exists


@pytest.fixture
def auth_environment(monkeypatch):
    db = FixtureDatabase()
    state = SimpleNamespace(
        account={
            "AccountId": "account-test", "PersonId": "person-test", "AspNetUserId": "identity-test",
            "FirstName1": "Imię", "LastName": "Testowe",
        },
        account_active=True,
        roles=[{"Code": "dean-office", "Name": "Dziekanat"}],
        hashes=[{"PasswordHash": django_hash()}],
        columns=["AccountId", "PasswordHash"],
        credential_params=None,
        credential_sql=None,
        decisions={"semesters": {"HasDeny": 0, "HasAllow": 0, "HasRole": 1}},
        menu=[{"Code": "semesters", "Name": "Semestry"}],
        queries=[],
    )

    def fake_query(connection, sql, params=()):
        assert connection is db.conn
        state.queries.append((sql, params))
        if "INFORMATION_SCHEMA.COLUMNS" in sql:
            return [{"ColumnName": name} for name in state.columns]
        if "(a.Login = ? OR a.Email = ?)" in sql:
            return [dict(state.account)] if state.account_active and params[0] == "test-user" else []
        if "SELECT PasswordHash" in sql:
            state.credential_params = params
            state.credential_sql = sql
            return list(state.hashes)
        if "SELECT DISTINCT r.Code" in sql:
            return list(state.roles)
        if "CROSS JOIN (VALUES" in sql:
            return [{"Id": item['Code'], "ParentItemId": None, "Route": '/', "ComponentKey": item['Code'],
                     "SortOrder": 0, "ShowInMenu": item.get('ShowInMenu', True), **item,
                     "ActionCode": action, **(state.decisions.get(item['Code']) or
                     {"HasDeny": 0, "HasAllow": 0, "HasRole": 0})}
                    for item in state.menu for action in ('view','create','update')]
        if "FROM org.SystemItems WHERE" in sql:
            return list(state.menu)
        raise AssertionError(f"Unexpected query: {sql}")

    def fake_one(connection, sql, params=()):
        assert connection is db.conn
        state.queries.append((sql, params))
        if "WHERE a.Id = ?" in sql:
            return dict(state.account) if state.account_active and params[0] == state.account["AccountId"] else None
        if "AS HasDeny" in sql:
            return state.decisions.get(params[-1])
        raise AssertionError(f"Unexpected query: {sql}")

    monkeypatch.setattr(service, "query", fake_query)
    monkeypatch.setattr(service, "one", fake_one)
    return db, state, AuthService(db)


def test_missing_password_source_blocks_login_before_accounts_query(auth_environment):
    db, state, auth = auth_environment
    db.exists = False
    with pytest.raises(AuthConfigurationError, match="Brak skonfigurowanego źródła haseł"):
        auth.login("test-user", PASSWORD)
    assert not state.queries
    assert not auth._sessions


def test_explicit_local_provider_uses_hash_but_requires_database_grants(auth_environment, monkeypatch):
    db, state, auth = auth_environment
    db.settings.auth_provider = "local_file"
    db.exists = False
    monkeypatch.setattr(service, "load_local_credentials", lambda settings: {"account-test": django_hash()})
    token = auth.login("test-user", PASSWORD)
    assert auth.get_user(token).account_id == "account-test"
    assert auth.can(token, "semesters", "create")
    state.decisions.clear()
    assert not auth.can(token, "semesters", "create")
    assert not any("SELECT PasswordHash" in sql for sql, _ in state.queries)


def test_local_hash_cannot_authenticate_without_active_database_account(auth_environment, monkeypatch):
    db, state, auth = auth_environment
    db.settings.auth_provider = "local_file"
    state.account_active = False
    monkeypatch.setattr(service, "load_local_credentials", lambda settings: {"account-test": django_hash()})
    with pytest.raises(AuthenticationError):
        auth.login("test-user", PASSWORD)
    assert not auth._sessions


def test_missing_required_password_columns_fail_closed(auth_environment):
    _, state, auth = auth_environment
    state.columns = ["AccountId"]
    with pytest.raises(AuthConfigurationError, match="kolumn"):
        auth.login("test-user", PASSWORD)


@pytest.mark.parametrize("login, password", [("unknown-user", PASSWORD), ("test-user", "wrong"), ("", PASSWORD)])
def test_failed_login_never_creates_a_session(auth_environment, login, password):
    _, _, auth = auth_environment
    with pytest.raises(AuthenticationError, match="Nieprawidłowy"):
        auth.login(login, password)
    assert not auth._sessions


def test_ambiguous_credentials_fail_closed(auth_environment):
    _, state, auth = auth_environment
    state.hashes *= 2
    with pytest.raises(AuthenticationError):
        auth.login("test-user", PASSWORD)


def test_optional_credential_active_flag_is_used_when_present(auth_environment):
    _, state, auth = auth_environment
    state.columns.append("IsActive")
    auth.login("test-user", PASSWORD)
    assert "AND IsActive = 1" in state.credential_sql


def test_external_identity_uses_existing_aspnet_id_and_explicit_database(auth_environment):
    db, state, auth = auth_environment
    db.settings.auth_database = "identity_db"
    db.settings.auth_schema = "dbo"
    db.settings.auth_table = "AspNetUsers"
    state.columns = ["Id", "PasswordHash"]
    auth.login("test-user", PASSWORD)
    assert state.credential_params == ("identity-test",)
    assert "[identity_db].[dbo].[AspNetUsers]" in state.credential_sql


def test_real_database_name_with_hyphen_is_quoted_for_source_and_metadata(auth_environment):
    db, state, auth = auth_environment
    db.settings.auth_database = "Cuda-BIS"
    auth.login("test-user", PASSWORD)
    assert "[Cuda-BIS].[org].[AccountCredentials]" in state.credential_sql
    assert any("[Cuda-BIS].INFORMATION_SCHEMA.COLUMNS" in sql for sql, _ in state.queries)


def test_identity_without_an_existing_mapping_cannot_log_in(auth_environment):
    db, state, auth = auth_environment
    db.settings.auth_table = "AspNetUsers"
    state.columns = ["Id", "PasswordHash"]
    state.account["AspNetUserId"] = None
    with pytest.raises(AuthenticationError):
        auth.login("test-user", PASSWORD)


def test_session_stores_only_token_digest_and_logout_revokes_access(auth_environment):
    _, _, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    assert len(token) >= 32
    assert list(auth._sessions) == [hashlib.sha256(token.encode()).digest()]
    assert token not in repr(auth._sessions)
    assert auth.get_user(token).display_name == "Imię Testowe"
    auth.logout(token)
    auth.logout(token)
    assert not auth.can(token, "semesters", "view")
    with pytest.raises(AuthenticationError):
        auth.get_user(token)


def test_account_deactivation_revokes_session_and_reactivation_does_not_restore_it(auth_environment):
    _, state, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    state.account_active = False
    with pytest.raises(AuthenticationError):
        auth.get_user(token)
    state.account_active = True
    assert not auth.can(token, "semesters", "view")


def test_moving_account_to_another_person_invalidates_session(auth_environment):
    _, state, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    state.account["PersonId"] = "another-person"
    with pytest.raises(AuthenticationError):
        auth.get_user(token)


def test_roles_are_reloaded_instead_of_stored_in_the_session(auth_environment):
    _, state, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    assert auth.get_user(token).roles == ("Dziekanat",)
    state.roles = [{"Code": "reader", "Name": "Odczyt"}]
    assert auth.get_user(token).roles == ("Odczyt",)


def test_idle_expiry_revokes_session(auth_environment, monkeypatch):
    _, _, auth = auth_environment
    now = [1000.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    token = auth.login("test-user", PASSWORD)
    now[0] += 120
    with pytest.raises(AuthenticationError):
        auth.get_user(token)


def test_active_session_is_still_subject_to_absolute_expiry(auth_environment, monkeypatch):
    _, _, auth = auth_environment
    now = [1000.0]
    monkeypatch.setattr(service.time, "monotonic", lambda: now[0])
    token = auth.login("test-user", PASSWORD)
    for _ in range(5):
        now[0] += 100
        auth.get_user(token)
    now[0] += 100
    with pytest.raises(AuthenticationError):
        auth.get_user(token)


@pytest.mark.parametrize("decision, expected", [
    ({"HasDeny": 1, "HasAllow": 1, "HasRole": 1}, False),
    ({"HasDeny": 0, "HasAllow": 1, "HasRole": 0}, True),
    ({"HasDeny": 0, "HasAllow": 0, "HasRole": 1}, True),
    ({"HasDeny": 0, "HasAllow": 0, "HasRole": 0}, False),
    (None, False),
])
def test_personal_deny_dominates_grants_and_missing_grants_deny(auth_environment, decision, expected):
    _, state, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    state.decisions["semesters"] = decision
    assert auth.can(token, "semesters", "create") is expected


def test_permission_revocation_is_seen_on_next_operation(auth_environment):
    _, state, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    assert auth.can(token, "semesters", "create")
    state.decisions["semesters"] = {"HasDeny": 0, "HasAllow": 0, "HasRole": 0}
    with pytest.raises(AuthorizationError):
        auth.require(token, "semesters", "create")


def test_action_codes_are_exact_and_no_read_edit_aliases_are_granted(auth_environment):
    _, _, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    assert not auth.can(token, "semesters", "read")
    assert not auth.can(token, "semesters", "edit")
    assert not auth.can(token, "semesters", "CREATE")


def test_transaction_authorization_uses_the_callers_connection(auth_environment):
    db, _, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    opens = db.opens
    auth.require(token, "semesters", "create", connection=db.conn)
    assert db.opens == opens


def test_menu_shows_only_items_with_current_view_permission(auth_environment):
    _, state, auth = auth_environment
    token = auth.login("test-user", PASSWORD)
    state.menu += [{"Code": "hidden", "Name": "Ukryty"}]
    state.decisions["hidden"] = {"HasDeny": 1, "HasAllow": 0, "HasRole": 1}
    assert [row["Code"] for row in auth.menu(token)] == ["semesters"]


def test_malformed_tokens_cannot_authorize(auth_environment):
    _, _, auth = auth_environment
    for token in (None, "", "x", "\ud800" * 32, "x" * 129):
        assert not auth.can(token, "semesters", "view")
        auth.logout(token)


def test_ui_presentation_fetches_all_actions_in_one_connection_and_respects_denies(auth_environment):
    db, state, auth = auth_environment
    token = auth.login('test-user', PASSWORD)
    state.menu += [{'Code': 'denied', 'Name': 'Denied'}, {'Code': 'hidden', 'Name': 'Hidden', 'ShowInMenu': False}]
    state.decisions['denied'] = {'HasDeny': 1, 'HasAllow': 1, 'HasRole': 1}
    state.decisions['hidden'] = {'HasDeny': 0, 'HasAllow': 0, 'HasRole': 1}
    opens, queries = db.opens, len(state.queries)
    user, menu, permissions = auth.presentation(token)
    assert db.opens == opens + 1
    assert len(state.queries) == queries + 3  # account, role names, all ACL/menu
    assert user.person_id == state.account['PersonId']
    assert [item['Code'] for item in menu] == ['semesters']
    assert permissions[('semesters', 'create')]
    assert permissions[('hidden', 'update')]
    assert not permissions[('denied', 'view')]
    state.decisions['semesters'] = {'HasDeny': 0, 'HasAllow': 0, 'HasRole': 0}
    assert not auth.presentation(token)[2][('semesters', 'create')]


def test_presentation_still_revokes_session_if_account_is_disabled(auth_environment):
    _, state, auth = auth_environment
    token = auth.login('test-user', PASSWORD)
    auth.presentation(token)
    state.account_active = False
    with pytest.raises(AuthenticationError):
        auth.presentation(token)
