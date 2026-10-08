from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime

from academic.errors import ValidationError


@dataclass(frozen=True)
class Semester:
    id: str
    name: str
    academic_year: str
    start_date: datetime | date
    end_date: datetime | date
    is_actual: bool
    is_active: bool
    timestamp: bytes | str | None

    @property
    def duration_days(self) -> int:
        return (as_date(self.end_date) - as_date(self.start_date)).days


def as_date(value: date | datetime) -> date:
    return value.date() if isinstance(value, datetime) else value


@dataclass(frozen=True)
class SemesterInput:
    name: str
    academic_year: str
    start_date: date
    end_date: date
    is_actual: bool
    is_active: bool

    def validated(self) -> SemesterInput:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValidationError("Podaj nazwę semestru.")
        if not isinstance(self.academic_year, str) or not self.academic_year.strip():
            raise ValidationError("Podaj rok akademicki.")
        if not isinstance(self.start_date, date) or not isinstance(self.end_date, date):
            raise ValidationError("Wybierz daty początku i końca semestru.")
        start_date, end_date = as_date(self.start_date), as_date(self.end_date)
        if end_date < start_date:
            raise ValidationError("Koniec semestru nie może być wcześniejszy niż początek.")
        if type(self.is_actual) is not bool or type(self.is_active) is not bool:
            raise ValidationError("Stan semestru musi mieć wartość tak albo nie.")
        return SemesterInput(
            self.name.strip(), self.academic_year.strip(), start_date,
            end_date, self.is_actual, self.is_active
        )
