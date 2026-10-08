"""Każdy zapis harmonogramu reprezentuje jedną godzinę zajęć."""

from dataclasses import dataclass
from datetime import datetime, time
from typing import Mapping
from uuid import UUID

from academic.errors import ValidationError


def valid_id(value, label: str, *, optional: bool = False) -> str | None:
    if optional and value in (None, ""):
        return None
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError, AttributeError):
        raise ValidationError(f"{label}: wybierz istniejący rekord.") from None


def clock_minutes(value) -> int:
    """Porównuj zakresy godzin; identyfikatory slotów nie określają kolizji."""
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        return value.hour * 60 + value.minute
    try:
        hour, minute, *seconds = str(value).strip().split(":")
        if seconds and float(seconds[0]) != 0:
            raise ValueError
        hour, minute = int(hour), int(minute)
        if not (0 <= hour <= 23 and 0 <= minute <= 59):
            raise ValueError
        return hour * 60 + minute
    except (ValueError, TypeError):
        raise ValidationError("Nieprawidłowa godzina w istniejącym planie dziennym.") from None


def intervals_overlap(start, end, other_start, other_end) -> bool:
    left, right = clock_minutes(start), clock_minutes(end)
    other_left, other_right = clock_minutes(other_start), clock_minutes(other_end)
    if right <= left or other_right <= other_left:
        raise ValidationError("Koniec godziny zajęć musi być późniejszy niż początek.")
    return left < other_right and other_left < right


@dataclass(frozen=True, slots=True)
class ScheduleInput:
    semester_organization_id: str
    semester_calendar_date_id: str
    daily_schedule_id: str
    teacher_id: str | None = None
    lecture_room_name: str = ""
    is_active: bool = True
    is_canceled: bool = False
    is_completed: bool = False

    @classmethod
    def from_mapping(cls, values: Mapping) -> "ScheduleInput":
        return cls(
            values.get("SemesterOrganizationId"), values.get("SemesterCalendarDateId"),
            values.get("DailyScheduleId"), values.get("TeacherId"),
            values.get("LectureRoomName") or "", values.get("IsActive", True),
            values.get("IsCanceled", False),
            values.get("IsComplited", values.get("IsCompleted", False)),
        )

    def validated(self) -> "ScheduleInput":
        for label, value in (("Aktywność", self.is_active), ("Odwołanie", self.is_canceled),
                             ("Wykonanie", self.is_completed)):
            if type(value) is not bool:
                raise ValidationError(f"{label}: wymagana wartość logiczna.")
        if self.is_canceled and self.is_completed:
            raise ValidationError("Wykonana godzina nie może być jednocześnie odwołana.")
        if not isinstance(self.lecture_room_name, str) or "\x00" in self.lecture_room_name:
            raise ValidationError("Sala: nieprawidłowa nazwa.")
        return ScheduleInput(
            valid_id(self.semester_organization_id, "Organizacja semestru"),
            valid_id(self.semester_calendar_date_id, "Data / zjazd"),
            valid_id(self.daily_schedule_id, "Godzina"),
            valid_id(self.teacher_id, "Dodatkowy wykładowca", optional=True),
            self.lecture_room_name.strip(), self.is_active, self.is_canceled, self.is_completed,
        )


def calculate_balance(required: int, rows: list[dict]) -> dict[str, int]:
    """Odwołana godzina pozostaje w bazie, ale wraca do nieprzydzielonych."""
    active = [row for row in rows if bool(row.get("IsActive", True))]
    canceled = sum(bool(row.get("IsCanceled")) for row in active)
    planned = sum(not bool(row.get("IsCanceled")) for row in active)
    completed = sum(not bool(row.get("IsCanceled")) and
                    bool(row.get("IsComplited", row.get("IsCompleted", False))) for row in active)
    return {
        "required": int(required), "planned": planned, "completed": completed,
        "canceled": canceled, "to_complete": planned - completed,
        "unassigned": max(int(required) - planned, 0),
        "excess": max(planned - int(required), 0),
    }
