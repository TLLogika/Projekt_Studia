"""Jawnie włączane hashe lokalnych kont testowych, powiązane z org.Accounts."""

import json
from pathlib import Path
from uuid import UUID

from academic.config import ROOT
from academic.auth.models import AuthConfigurationError


def credentials_path(settings) -> Path:
    path = Path(settings.auth_local_credentials_file)
    if not path.is_absolute():
        path = ROOT / path
    path = path.resolve()
    if not path.is_relative_to((ROOT / "secret").resolve()):
        raise AuthConfigurationError("Plik haseł testowych musi znajdować się w katalogu secret.")
    return path


def load_local_credentials(settings) -> dict[str, str]:
    try:
        path = credentials_path(settings)
        if path.stat().st_size > 1_048_576:
            raise ValueError
        if path.stat().st_mode & 0o077:
            raise AuthConfigurationError("Ustaw uprawnienia 600 dla pliku haseł testowych.")
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("accounts"), dict):
            raise ValueError
        accounts = {}
        for account_id, hashed_password in data["accounts"].items():
            if not isinstance(hashed_password, str) or not 1 <= len(hashed_password) <= 4096:
                raise ValueError
            accounts[str(UUID(account_id))] = hashed_password
        return accounts
    except (OSError, ValueError, TypeError, AttributeError):
        raise AuthConfigurationError("Brak poprawnego pliku haseł kont testowych w secret.") from None
