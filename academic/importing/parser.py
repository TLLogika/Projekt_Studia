"""Interpret only user-mapped fields; no inferred columns or fabricated rows."""

from datetime import date, datetime, time, timedelta
import re
from uuid import UUID

from academic.errors import ValidationError
from .models import ImportIssue, ImportMapping, ParsedRow, SourceTable

REFERENCE_FIELDS = frozenset({
    "organization_id", "subject_id", "subject_name", "lecture_group_id", "lecture_group_name",
    "group_id", "group_name", "teacher_id", "teacher_name", "calendar_date_id", "slot_id",
})
ALL_FIELDS = REFERENCE_FIELDS | {"date", "start", "end", "time_range", "room"}
DATE_FORMATS = ("%Y-%m-%d", "%d.%m.%Y", "%d/%m/%Y")


def _text(value) -> str:
    return "" if value is None else str(value).strip()


def parse_date(value, date_format: str) -> date:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    try:
        return datetime.strptime(_text(value), date_format).date()
    except ValueError:
        raise ValidationError("Data nie odpowiada wybranemu formatowi.") from None


def parse_time(value) -> time:
    if isinstance(value, datetime):
        value = value.time()
    if isinstance(value, time):
        if value.second or value.microsecond or value.tzinfo is not None:
            raise ValidationError("Godzinę podaj z dokładnością do minuty, bez strefy czasowej.")
        return value
    if isinstance(value, (float, int)) and not isinstance(value, bool) and 0 <= value < 1:
        minutes = value * 1440
        if abs(minutes - round(minutes)) > 0.00001:
            raise ValidationError("Godzina Excel musi mieć dokładność do minuty.")
        total = round(minutes)
        if total >= 1440:
            raise ValidationError("Godzina musi mieścić się w jednej dobie.")
        return time(total // 60, total % 60)
    text = _text(value)
    if not re.fullmatch(r"\d{1,2}:\d{2}", text):
        raise ValidationError("Godzinę podaj w formacie HH:MM.")
    try:
        return datetime.strptime(text, "%H:%M").time()
    except ValueError:
        raise ValidationError("Nieprawidłowa godzina.") from None


def validate_mapping(table: SourceTable, mapping: ImportMapping):
    if mapping.date_format not in DATE_FORMATS:
        raise ValidationError("Wybierz obsługiwany format daty.")
    if set(mapping.columns) - ALL_FIELDS:
        raise ValidationError("Mapowanie zawiera nieobsługiwane pole.")
    selected = list(mapping.columns.values())
    if any(column not in table.columns for column in selected):
        raise ValidationError("Mapowanie wskazuje kolumnę spoza wybranego pliku.")
    if len(set(selected)) != len(selected):
        raise ValidationError("Jednej kolumny źródłowej nie można przypisać do kilku pól.")
    fields = set(mapping.columns)
    if "organization_id" not in fields:
        if not fields & {"subject_id", "subject_name"}:
            raise ValidationError("Przypisz kolumnę organizacji albo przedmiotu.")
        if not fields & {"lecture_group_id", "lecture_group_name", "group_id", "group_name"}:
            raise ValidationError("Przypisz kolumnę grupy lub grupy wykładowej.")
    if not fields & {"date", "calendar_date_id"}:
        raise ValidationError("Przypisz kolumnę daty lub ID dnia kalendarza.")
    if "slot_id" not in fields and "time_range" not in fields and not {"start", "end"} <= fields:
        raise ValidationError("Przypisz slot, zakres godzin albo obie godziny początku i końca.")
    if "time_range" in fields and fields & {"start", "end", "slot_id"}:
        raise ValidationError("Wybierz jeden sposób określenia godziny: zakres, start/koniec lub ID slotu.")
    if "slot_id" in fields and fields & {"start", "end"}:
        raise ValidationError("Wybierz ID slotu albo godziny początku i końca.")
    if bool("start" in fields) != bool("end" in fields):
        raise ValidationError("Początek i koniec zajęć muszą być przypisane razem.")


def parse_rows(table: SourceTable, mapping: ImportMapping) -> tuple[tuple[ParsedRow, ...], tuple[ImportIssue, ...]]:
    validate_mapping(table, mapping)
    parsed, issues = [], []
    for source in table.rows:
        try:
            values = {field: source.values[column] for field, column in mapping.columns.items()}
            refs = {field: _text(values.get(field)) for field in REFERENCE_FIELDS if field in values}
            for field, value in refs.items():
                if field.endswith("_id") and value:
                    try:
                        refs[field] = str(UUID(value))
                    except ValueError:
                        raise ValidationError(f"Pole {mapping.columns[field]} nie zawiera poprawnego ID.") from None
            if not refs.get("organization_id"):
                if not (refs.get("subject_id") or refs.get("subject_name")):
                    raise ValidationError("W wierszu brakuje ID organizacji albo identyfikacji przedmiotu.")
                if not any(refs.get(field) for field in ("lecture_group_id", "lecture_group_name", "group_id", "group_name")):
                    raise ValidationError("W wierszu brakuje identyfikacji grupy lub grupy wykładowej.")
            day = parse_date(values["date"], mapping.date_format) if "date" in values else None
            start = end = None
            if "time_range" in values:
                bounds = re.split(r"\s*[–—-]\s*", _text(values["time_range"]))
                if len(bounds) != 2:
                    raise ValidationError("Zakres godzin musi mieć postać HH:MM–HH:MM.")
                start, end = map(parse_time, bounds)
            elif "start" in values:
                start, end = parse_time(values["start"]), parse_time(values["end"])
            if start is not None and end <= start:
                raise ValidationError("Koniec zajęć musi przypadać po początku w tej samej dobie.")
            if not refs.get("calendar_date_id") and day is None:
                raise ValidationError("Brak daty zajęć.")
            if not refs.get("slot_id") and start is None:
                raise ValidationError("Brak godziny zajęć.")
            parsed.append(ParsedRow(source.number, refs, day, start, end, _text(values.get("room"))))
        except ValidationError as error:
            issues.append(ImportIssue(source.number, str(error)))
    return tuple(parsed), tuple(issues)


def split_slots(start: time, end: time, slots: list[dict]) -> tuple[dict, ...]:
    """An exact, unique contiguous path through existing slots. No generated IDs."""
    candidates = []
    for slot in slots:
        first, last = parse_time(slot["StartHour"]), parse_time(slot["EndHour"])
        if last <= first:
            raise ValidationError("W bazie istnieje slot o niepoprawnym zakresie godzin.")
        if first >= start and last <= end:
            candidates.append((first, last, slot))
    # The graph is acyclic because every slot ends after it starts. Keeping
    # at most two paths per boundary detects ambiguity without exponential
    # backtracking or recursion for malformed imported slot definitions.
    by_start = {}
    for first, last, slot in candidates:
        by_start.setdefault(first, []).append((last, slot))
    paths_at = {end: [()]}
    for first in sorted(by_start, reverse=True):
        options = []
        for last, slot in by_start[first]:
            for tail in paths_at.get(last, []):
                options.append((slot,) + tail)
                if len(options) == 2:
                    break
            if len(options) == 2:
                break
        paths_at[first] = options
    paths = paths_at.get(start, [])
    if not paths:
        raise ValidationError("Zakresu godzin nie można pokryć istniejącymi slotami tego typu planu dnia.")
    if len(paths) != 1:
        raise ValidationError("Zakres pasuje do kilku zestawów slotów. Wskaż jednoznaczne sloty ID.")
    base = datetime.combine(date(2000, 1, 1), start)
    for slot in paths[0]:
        duration = datetime.combine(base.date(), parse_time(slot["EndHour"])) - datetime.combine(base.date(), parse_time(slot["StartHour"]))
        if duration != timedelta(minutes=45):
            raise ValidationError("Import pojedynczych godzin wymaga istniejących slotów po 45 minut.")
    return paths[0]
