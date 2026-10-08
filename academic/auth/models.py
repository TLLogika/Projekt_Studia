"""Authenticated identity and errors safe to present to the user."""

from dataclasses import dataclass

from academic.errors import ApplicationError


class AuthenticationError(ApplicationError):
    """Credentials are invalid or the session is no longer valid."""


class AuthConfigurationError(AuthenticationError):
    """A real database-backed password source has not been configured."""


class AuthorizationError(ApplicationError):
    """The current database grants do not authorize an operation."""


@dataclass(frozen=True, slots=True)
class User:
    person_id: str
    account_id: str
    display_name: str
    roles: tuple[str, ...]
