"""Cache odczytów UI; autoryzacja i wszystkie zapisy pozostają bieżące."""

import hashlib
from threading import RLock

import streamlit as st

from academic.auth.models import AuthorizationError
from academic.catalogs.specs import CATALOGS
from academic.errors import ConflictError
from academic.views.calendar import SCOPES

_lock = RLock()
_generations = {}
READS = {
    'semesters': {'list'},
    'catalog': {'list', 'choices', 'editor_choices', 'generation_choices', 'preview_subjects'},
    'organization': {'list', 'lookups', 'readiness'},
    'schedule': {'list', 'lookups', 'balance'},
    'import': {'options'},
    'calendar': {'semesters', 'lecture_groups', 'list'},
    'notes': {'list', 'targets', 'records'},
}
WRITES = {'create', 'update', 'generate_subjects', 'finalize', 'create_many',
          'cancel', 'complete', 'make_up', 'commit'}


def invalidate_reads(database):
    """Unieważnij odczyty wszystkich użytkowników po zatwierdzonym zapisie."""
    with _lock:
        realm = id(database)
        _generations[realm] = _generations.get(realm, 0) + 1


@st.cache_data(ttl=120, max_entries=512, show_spinner=False)
def _read(realm, session_key, generation, permissions, kind, method, args, kwargs, _service, _token):
    return getattr(_service, method)(_token, *args, **kwargs)


class CachedService:
    """Facade używana przez UI, nigdy przez walidację transakcji backendu."""

    def __init__(self, service, kind, token, permissions):
        self.service, self.kind, self.token = service, kind, token
        self.permissions = permissions
        self.realm = id(service.db)
        self.session_key = hashlib.sha256(token.encode()).hexdigest()
        self.permission_key = tuple(sorted(permissions.items()))

    def _code(self, args, kwargs=None):
        kwargs = kwargs or {}
        if self.kind == 'catalog':
            key = args[0] if args else kwargs.get('entity_key')
            return CATALOGS[key].item_code
        if self.kind == 'semesters':
            return self.service.item_code
        if self.kind == 'import':
            return 'schedule_import'
        return self.service.item_code

    def can(self, token, *args, **kwargs):
        if token != self.token:
            raise AuthorizationError('Nieprawidłowa sesja widoku.')
        code = self._code(args, kwargs)
        position = 1 if self.kind == 'catalog' else 0
        action = args[position] if len(args) > position else kwargs.get('action')
        return self.permissions.get((code, action), False)

    def _guard(self, method, args, kwargs):
        if self.kind == 'calendar':
            scope = args[0] if args else kwargs.get('scope')
            code = SCOPES.get(scope)
        elif self.kind == 'catalog' and method in {'generation_choices', 'preview_subjects'}:
            code = 'subjects_semesters'
        else:
            code = self._code(args, kwargs)
        if not self.permissions.get((code, 'view'), False):
            raise AuthorizationError('Brak uprawnień do tego widoku.')

    def scopes(self, token):
        if token != self.token:
            raise AuthorizationError('Nieprawidłowa sesja widoku.')
        return tuple(scope for scope, code in SCOPES.items() if self.permissions.get((code, 'view'), False))

    def __getattr__(self, name):
        target = getattr(self.service, name)
        if name in READS.get(self.kind, ()):
            def read(token, *args, **kwargs):
                if token != self.token:
                    raise AuthorizationError('Nieprawidłowa sesja widoku.')
                self._guard(name, args, kwargs)
                with _lock:
                    generation = _generations.get(self.realm, 0)
                return _read(self.realm, self.session_key, generation, self.permission_key,
                             self.kind, name, args, kwargs, self.service, token)
            return read
        if name in WRITES:
            def write(*args, **kwargs):
                try:
                    result = target(*args, **kwargs)
                except ConflictError:
                    invalidate_reads(self.service.db)
                    raise
                invalidate_reads(self.service.db)
                return result
            return write
        return target


def cached_service(service, kind, token, permissions):
    # Interfejsy testowe/adaptery bez snapshotu zachowują własne sprawdzanie ACL.
    if permissions is None or not hasattr(service, 'db'):
        return service
    return CachedService(service, kind, token, permissions)
