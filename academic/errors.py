"""Wyjątki z komunikatami bezpiecznymi do wyświetlenia w interfejsie."""


class ApplicationError(Exception):
    """Przewidywalny błąd aplikacji; komunikat nie zawiera poświadczeń."""


class ConfigurationError(ApplicationError):
    pass


class DatabaseError(ApplicationError):
    pass


class ValidationError(ApplicationError):
    pass


class ConflictError(ApplicationError):
    pass
