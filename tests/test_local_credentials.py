import json
from types import SimpleNamespace

import pytest

from academic.auth import local_credentials
from academic.auth.models import AuthConfigurationError

ACCOUNT_ID = "71a0cf92-4c76-42bf-8e09-65a2c18b5c28"


@pytest.fixture
def local_file(tmp_path, monkeypatch):
    monkeypatch.setattr(local_credentials, "ROOT", tmp_path)
    path = tmp_path / "secret" / "test_accounts.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"version": 1, "accounts": {ACCOUNT_ID: "existing-test-hash"}}))
    path.chmod(0o600)
    return path, SimpleNamespace(auth_local_credentials_file="secret/test_accounts.json")


def test_hashes_are_bound_to_database_account_ids(local_file):
    _, settings = local_file
    assert local_credentials.load_local_credentials(settings) == {ACCOUNT_ID: "existing-test-hash"}


def test_credentials_cannot_be_loaded_outside_secret(local_file, tmp_path):
    _, settings = local_file
    settings.auth_local_credentials_file = str(tmp_path / "elsewhere.json")
    with pytest.raises(AuthConfigurationError, match="katalogu secret"):
        local_credentials.load_local_credentials(settings)


def test_world_readable_credentials_are_rejected(local_file):
    path, settings = local_file
    path.chmod(0o644)
    with pytest.raises(AuthConfigurationError, match="600"):
        local_credentials.load_local_credentials(settings)


@pytest.mark.parametrize("data", [[], {"version": 2, "accounts": {}},
                                     {"version": 1, "accounts": {"invalid-id": "hash"}},
                                     {"version": 1, "accounts": {ACCOUNT_ID: None}}])
def test_malformed_local_store_fails_without_echoing_data(local_file, data):
    path, settings = local_file
    path.write_text(json.dumps(data))
    with pytest.raises(AuthConfigurationError) as error:
        local_credentials.load_local_credentials(settings)
    assert ACCOUNT_ID not in str(error.value)
