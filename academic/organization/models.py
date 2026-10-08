"""Wynik kontroli organizacji, wyliczany z aktualnych rekordów bazy."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class ReadinessReport:
    semester_id: str
    issues: tuple[str, ...]
    subjects_count: int
    groups_count: int
    organizations_count: int

    @property
    def ready(self) -> bool:
        return not self.issues

