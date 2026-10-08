"""Nawigacja modułów implementujących UI.jpg i proces z transkrypcji."""

from dataclasses import dataclass

from academic.catalogs.specs import CATALOGS


@dataclass(frozen=True)
class Module:
    key: str
    title: str
    group: str
    permission_codes: tuple[str, ...]
    kind: str


def modules(semesters_item_code="semesters"):
    return (
        Module("semesters", "Semestr", "", (semesters_item_code,), "semesters"),
        *(Module(key, spec.title, spec.group, (spec.item_code,), "catalog") for key, spec in CATALOGS.items()),
        Module("notes", "Notatki", "Słowniki", ("dictionaries",), "notes"),
        Module("organization", "Organizacja semestru", "Organizacja i harmonogram", ("organization",), "organization"),
        Module("schedule", "Harmonogram i bilans", "Organizacja i harmonogram", ("schedule",), "schedule"),
        Module("schedule_import", "Import planu", "Organizacja i harmonogram", ("schedule_import",), "import"),
        Module("calendar", "Kalendarz zajęć", "Organizacja i harmonogram", ("calendar.dean", "calendar.teacher", "calendar.student"), "calendar"),
    )


def available_modules(menu, semesters_item_code="semesters"):
    codes = {item["Code"] for item in menu}
    return tuple(module for module in modules(semesters_item_code) if codes.intersection(module.permission_codes))
