"""Cache nie może mieszać użytkowników, omijać odmów ani opóźniać własnych zapisów."""

from types import SimpleNamespace

import pytest

from academic.auth.models import AuthorizationError
from academic.errors import ConflictError, ValidationError
from academic.ui.cache import CachedService, _read, invalidate_reads


class Service:
    item_code = 'semesters'

    def __init__(self, db=None):
        self.db = db or object()
        self.reads = []
        self.writes = 0
        self.value = 'initial'
        self.allow_write = True

    def list(self, token):
        self.reads.append(token)
        return [{'token': token, 'value': self.value}]

    def create(self, token, value):
        if not self.allow_write:
            raise AuthorizationError('Revoked live permission')
        self.writes += 1
        self.value = value
        return value


@pytest.fixture(autouse=True)
def clear_cache():
    _read.clear()
    yield
    _read.clear()


def wrap(service, token='session-a', **actions):
    permissions = {('semesters', action): value for action, value in
                   {'view': True, 'create': True, 'update': True, **actions}.items()}
    return CachedService(service, 'semesters', token, permissions)


def test_repeated_reads_use_cache_and_return_independent_copies():
    service = Service()
    first = wrap(service)
    data = first.list('session-a')
    data[0]['value'] = 'local edit'
    second = wrap(service)
    assert second.list('session-a')[0]['value'] == 'initial'
    assert service.reads == ['session-a']
    assert service.writes == 0


def test_sessions_and_databases_do_not_share_cached_rows():
    one, two = Service(), Service()
    assert wrap(one).list('session-a')[0]['token'] == 'session-a'
    assert wrap(one, 'session-b').list('session-b')[0]['token'] == 'session-b'
    two.value = 'other database'
    assert wrap(two).list('session-a')[0]['value'] == 'other database'
    assert len(one.reads) == 2 and len(two.reads) == 1


def test_revoked_view_is_checked_before_returning_warm_cache():
    service = Service()
    wrap(service).list('session-a')
    with pytest.raises(AuthorizationError):
        wrap(service, view=False).list('session-a')
    assert len(service.reads) == 1


def test_writes_are_never_cached_and_refresh_other_users_after_commit():
    service = Service()
    a, b = wrap(service), wrap(service, 'session-b')
    a.list('session-a')
    b.list('session-b')
    a.create('session-a', 'changed')
    assert b.list('session-b')[0]['value'] == 'changed'
    a.create('session-a', 'changed')
    assert service.writes == 2


def test_cached_ui_grant_does_not_bypass_live_write_authorization():
    service = Service()
    ui = wrap(service)
    assert ui.can('session-a', 'create')
    service.allow_write = False
    with pytest.raises(AuthorizationError):
        ui.create('session-a', 'forbidden')
    assert service.writes == 0 and service.value == 'initial'


def test_manual_refresh_reads_external_changes():
    service = Service()
    ui = wrap(service)
    ui.list('session-a')
    service.value = 'external change'
    assert ui.list('session-a')[0]['value'] == 'initial'
    invalidate_reads(service.db)
    assert ui.list('session-a')[0]['value'] == 'external change'


def test_methods_with_the_same_name_in_different_modules_do_not_collide():
    service = Service()
    one = wrap(service)
    notes = Service(service.db)
    notes.item_code = 'dictionaries'
    notes.value = 'notes data'
    two = CachedService(notes, 'notes', 'session-a', {('dictionaries', 'view'): True})
    assert one.list('session-a')[0]['value'] == 'initial'
    assert two.list('session-a')[0]['value'] == 'notes data'


def test_calendar_cache_remains_bound_to_requested_scope_and_session():
    class Calendar:
        db = object()
        calls = 0
        def list(self, token, scope, start=None, end=None):
            self.calls += 1
            return [scope, token]
    calendar = Calendar()
    dean = CachedService(calendar, 'calendar', 'dean', {('calendar.dean','view'): True})
    student = CachedService(calendar, 'calendar', 'student', {('calendar.student','view'): True})
    assert dean.list('dean', 'dean') == ['dean', 'dean']
    assert student.list('student', 'student') == ['student', 'student']
    with pytest.raises(AuthorizationError):
        student.list('student', 'dean')
    assert calendar.calls == 2
    assert student.scopes('student') == ('student',)


def test_import_without_item_code_attribute_uses_its_actual_permission_code():
    service = SimpleNamespace(db=object(), options=lambda token: {'Semesters': []})
    ui = CachedService(service, 'import', 'session-a', {('schedule_import','view'): True})
    assert ui.options('session-a') == {'Semesters': []}
    assert not ui.can('session-a', 'create')


def test_wrong_token_cannot_use_another_sessions_cache():
    service = Service()
    ui = wrap(service)
    ui.list('session-a')
    with pytest.raises(AuthorizationError):
        ui.list('session-b')
    assert len(service.reads) == 1


@pytest.mark.parametrize('error', [ValidationError('invalid'), ConflictError('stale')])
def test_failed_write_never_claims_success_or_modifies_cached_data(error):
    service = Service()
    def fail(*args):
        raise error
    service.create = fail
    ui = wrap(service)
    ui.list('session-a')
    with pytest.raises(type(error)):
        ui.create('session-a', 'bad')
    assert ui.list('session-a')[0]['value'] == 'initial'
    assert service.writes == 0
