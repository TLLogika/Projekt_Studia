"""Immutable source and preview records; previews never write to SQL Server."""

from dataclasses import dataclass, field
from datetime import date, time
from typing import Any


@dataclass(frozen=True)
class SourceRow:
    number: int
    values: dict[str, Any]


@dataclass(frozen=True)
class SourceTable:
    columns: tuple[str, ...]
    rows: tuple[SourceRow, ...]


@dataclass(frozen=True)
class ImportContext:
    semester_id: str
    program_id: str | None = None
    studies_id: str | None = None
    semester_number: int | None = None
    specialization_id: str | None = None


@dataclass(frozen=True)
class ImportMapping:
    """Keys are semantic fields, values are user-selected source column names."""

    columns: dict[str, str] = field(default_factory=dict)
    date_format: str = "%Y-%m-%d"


@dataclass(frozen=True)
class ImportIssue:
    row: int
    message: str


@dataclass(frozen=True)
class ParsedRow:
    number: int
    references: dict[str, str]
    day: date | None
    start: time | None
    end: time | None
    room: str


@dataclass(frozen=True)
class ImportPreview:
    entries: tuple[dict[str, Any], ...]
    issues: tuple[ImportIssue, ...]
    source_rows: int
    source_table: SourceTable | None = None
    mapping: ImportMapping | None = None
    context: ImportContext | None = None

    @property
    def ready(self) -> bool:
        return bool(self.entries) and not self.issues
