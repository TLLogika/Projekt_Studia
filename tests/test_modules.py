"""Role-specific navigation must not expose another role's calendar."""

from academic.modules import available_modules


def visible(*codes):
    return {item.key for item in available_modules([{"Code": code} for code in codes])}


def test_semester_only_account_keeps_other_modules_hidden():
    assert visible("semesters") == {"semesters"}


def test_personal_calendar_permissions_do_not_expose_administrative_modules():
    assert visible("calendar.student") == {"calendar"}
    assert visible("calendar.teacher") == {"calendar"}
    assert visible("calendar.dean") == {"calendar"}
    assert visible() == set()


def test_dictionary_permission_exposes_dependency_catalogs_and_is_revocable():
    keys = visible("dictionaries", "groups", "subjects")
    assert {"programs", "studies", "teachers", "daily_schedules", "groups", "subjects"} <= keys
    assert "schedule" not in keys
    assert not visible("unknown")

