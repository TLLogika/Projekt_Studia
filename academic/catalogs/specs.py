"""Jawny wykaz pól z UI.jpg oraz tabel potrzebnych do ich uzupełniania.

Id, Timestamp, OldId oraz kolumny okresu temporalnego nigdy nie są edytowane.
Nie definiujemy kolumn, których nie ma w udostępnionej bazie.
"""

from dataclasses import dataclass


@dataclass(frozen=True)
class Field:
    name: str
    label: str
    kind: str = "text"
    required: bool = False
    reference: str | None = None
    minimum: int | float | None = None
    maximum: int | float | None = None


@dataclass(frozen=True)
class CatalogSpec:
    key: str
    title: str
    singular: str
    table: str
    fields: tuple[Field, ...]
    item_code: str = "dictionaries"
    label_fields: tuple[str, ...] = ("Name", "ShortName")

    @property
    def allowed_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields)

    @property
    def required_fields(self) -> tuple[str, ...]:
        return tuple(field.name for field in self.fields if field.required)

    @property
    def group(self) -> str:
        if self.key in {"groups", "lecture_groups", "lecture_group_types", "lecture_group_elements", "group_members"}:
            return "Zarządzanie grupami"
        if self.key in {"subjects", "subject_types", "subjects_semesters"}:
            return "Przedmioty"
        return "Słowniki"


def text(name, label, required=False):
    return Field(name, label, required=required)


def ref(name, label, table, required=True):
    return Field(name, label, "reference", required, table)


def number(name, label, minimum=0, maximum=None, required=True):
    return Field(name, label, "integer", required, minimum=minimum, maximum=maximum)


ACTIVE = Field("IsActive", "Aktywny", "boolean", True)
NAME = text("Name", "Nazwa", True)
SHORT = text("ShortName", "Skrót nazwy", True)
TYPE_FIELDS = (NAME, SHORT, ACTIVE)


def spec(key, title, singular, table, fields, item_code="dictionaries", label_fields=("Name", "ShortName")):
    return CatalogSpec(key, title, singular, table, tuple(fields), item_code, label_fields)


_SPECS = (
    spec("countries", "Kraje i obywatelstwa", "kraj", "Countries", (
        NAME, text("Citizenship", "Obywatelstwo", True), text("Code", "Kod kraju", True), ACTIVE,
    ), label_fields=("Name", "Code", "Citizenship")),
    spec("groups", "Grupy", "grupę", "Groups", (
        text("Name", "Nazwa grupy", True), number("SemesterNumber", "Numer semestru studiów", 1), ACTIVE,
        ref("StudiesId", "Rodzaj studiów", "Studies"), ref("SemesterId", "Semestr", "Semesters"),
        ref("SpecializationId", "Specjalizacja", "Specializations", False),
    ), "groups"),
    spec("lecture_groups", "Grupy wykładowe", "grupę wykładową", "LectureGroups", (
        text("Name", "Nazwa grupy wykładowej", True), SHORT,
        ref("SemesterId", "Semestr", "Semesters"),
        ref("LectureGroupTypeId", "Typ grupy wykładowej", "LectureGroupTypes"),
        ref("SemesterCalendarId", "Kalendarz semestru", "SemesterCalendars"),
        ref("DailyScheduleTypeId", "Typ planu dziennego", "DailyScheduleTypes"), ACTIVE,
        text("TeamsGroupID", "ID grupy Teams"), text("TeamsGroupName", "Nazwa grupy Teams"),
    ), "lecture_groups"),
    spec("lecture_group_types", "Typy grup wykładowych", "typ grupy wykładowej", "LectureGroupTypes", TYPE_FIELDS, "lecture_group_types"),
    spec("subjects", "Przedmioty", "przedmiot", "Subjects", (
        text("Name", "Nazwa przedmiotu", True), text("ShortName", "Skrót nazwy"),
        number("Semester", "Numer semestru studiów", 1), Field("Exam", "Egzamin", "boolean", True),
        text("Symbol", "Symbol przedmiotu"), text("Subsymbol", "Podkod przedmiotu"),
        Field("EctsPoints", "Punkty ECTS", "decimal", minimum=0, maximum=999.99),
        number("NumberOfHours", "Liczba godzin", 0, 255, False), ACTIVE,
        ref("SubjectTypeId", "Typ przedmiotu", "SubjectTypes"),
        ref("ProgramsOfStudyId", "Program nauczania", "ProgramsOfStudies"),
        ref("StudiesId", "Rodzaj studiów", "Studies"),
        ref("SpecializationId", "Specjalizacja", "Specializations", False),
    ), "subjects"),
    spec("subject_types", "Typy przedmiotów", "typ przedmiotu", "SubjectTypes", TYPE_FIELDS, "subject_types"),
    spec("subjects_semesters", "Przedmioty w semestrze", "przedmiot w semestrze", "SubjectsSemesters", (
        ref("SubjectId", "Przedmiot", "Subjects"), ref("SemesterId", "Semestr", "Semesters"),
        number("NumberOfHybridHours", "Godziny hybrydowe", 0, 255),
        number("NumberOfPlanningHours", "Godziny planowane", 0, 255), ACTIVE,
        number("SemesterNumber", "Numer semestru studiów", 1),
    ), "subjects_semesters", ("SubjectId", "SemesterId")),
    spec("programs", "Programy nauczania", "program nauczania", "ProgramsOfStudies", (
        text("ProgramName", "Nazwa programu"), text("KRKNumber", "Numer KRK", True),
        text("KRKName", "Nazwa KRK", True), Field("StartDate", "Data obowiązywania", "date", True), ACTIVE,
        Field("Description", "Opis", "multiline"),
    ), label_fields=("ProgramName", "KRKNumber", "KRKName")),
    spec("fields_of_studies", "Kierunki studiów", "kierunek studiów", "FieldsOfStudies", (
        text("SchoolName", "Uczelnia", True), text("DepartmentName", "Wydział", True),
        text("FieldOfStudies", "Kierunek studiów", True), ACTIVE,
    ), label_fields=("FieldOfStudies", "DepartmentName")),
    spec("modes_of_studies", "Tryby studiów", "tryb studiów", "ModesOfStudies", TYPE_FIELDS),
    spec("studies_degrees", "Stopnie studiów", "stopień studiów", "StudiesDegrees", TYPE_FIELDS),
    spec("studies", "Studia", "studia", "Studies", (
        NAME, SHORT, ref("FieldsOfStudiesId", "Kierunek studiów", "FieldsOfStudies"),
        ref("ModesOfStudiesId", "Tryb studiów", "ModesOfStudies"), Field("StartDate", "Data rozpoczęcia", "date", True),
        ACTIVE, ref("StudiesDegreeId", "Stopień studiów", "StudiesDegrees"),
        Field("Description", "Opis", "multiline"), number("NumberOfSemesters", "Liczba semestrów", 1),
    )),
    spec("specializations", "Specjalizacje", "specjalizację", "Specializations", (
        NAME, SHORT, ref("StudiesId", "Rodzaj studiów", "Studies"), ACTIVE,
    )),
    spec("persons", "Osoby", "osobę", "Persons", (
        text("FirstName1", "Imię", True), text("FirstName2", "Drugie imię"),
        text("LastNamePrefix", "Przedrostek nazwiska"), text("LastName", "Nazwisko", True),
        Field("AdditionalInfo", "Dodatkowe informacje", "multiline"), ACTIVE,
    ), label_fields=("LastName", "FirstName1", "FirstName2")),
    spec("academic_titles", "Tytuły naukowe", "tytuł naukowy", "AcademicTitles", TYPE_FIELDS),
    spec("teachers", "Wykładowcy", "wykładowcę", "Teachers", (
        ref("PersonId", "Osoba", "Persons"), text("ShortName", "Skrót nazwy"),
        ref("AcademicTitleId", "Tytuł naukowy", "AcademicTitles", False),
    ), label_fields=("PersonId", "ShortName")),
    spec("students", "Studenci", "studenta", "Students", (
        text("AcademicNumber", "Numer albumu", True), ref("PersonId", "Osoba", "Persons"),
        ref("GroupId", "Grupa podstawowa", "Groups"),
    ), label_fields=("AcademicNumber", "PersonId")),
    spec("lecture_group_elements", "Grupy w grupach wykładowych", "przypisanie grupy", "LectureGroupsElements", (
        ref("GroupId", "Grupa", "Groups"), ref("LectureGroupId", "Grupa wykładowa", "LectureGroups"), ACTIVE,
    ), "lecture_groups", ("GroupId", "LectureGroupId")),
    spec("group_members", "Studenci w grupach", "przypisanie studenta", "GroupMembers", (
        ref("StudentId", "Student", "Students"), ref("GroupId", "Grupa", "Groups"),
        Field("StartDate", "Początek członkostwa", "date"), Field("EndDate", "Koniec członkostwa", "date"), ACTIVE,
    ), "groups", ("StudentId", "GroupId")),
    spec("semester_calendars", "Kalendarze semestru", "kalendarz semestru", "SemesterCalendars", (
        NAME, SHORT, ACTIVE, ref("SemesterId", "Semestr", "Semesters"),
    )),
    spec("semester_calendar_dates", "Daty i zjazdy", "datę zjazdu", "SemesterCalendarDates", (
        ref("SemesterCalendarId", "Kalendarz semestru", "SemesterCalendars"),
        Field("Date", "Data zjazdu", "date", True), number("Number", "Numer zjazdu", 1, 255), ACTIVE,
    ), label_fields=("Date", "Number")),
    spec("daily_schedule_types", "Typy planów dziennych", "typ planu dziennego", "DailyScheduleTypes", TYPE_FIELDS),
    spec("daily_schedules", "Godziny zajęć", "godzinę zajęć", "DailySchedules", (
        number("Number", "Numer godziny", 1, 255), Field("StartHour", "Początek godziny (GG:MM)", "time", True),
        Field("EndHour", "Koniec godziny (GG:MM)", "time", True), ACTIVE,
        ref("DailyScheduleTypeId", "Typ planu dziennego", "DailyScheduleTypes"),
    ), label_fields=("Number", "StartHour", "EndHour")),
)

CATALOGS = {item.key: item for item in _SPECS}
TABLE_SPECS = {item.table: item for item in _SPECS}
