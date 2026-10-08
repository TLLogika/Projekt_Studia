## 7. Diagramy ER bazy danych

Pełny model rozbito na domeny. Ta sama tabela może pojawić się na dwóch
diagramach jako punkt łączący domeny. Pokazano klucze i pola najważniejsze dla
zrozumienia relacji; powtarzalne pola techniczne (`IsActive`, `Timestamp`,
`OldId`) są opisane wcześniej.

Oznaczenia Mermaid:

- `||` — dokładnie jeden,
- `o|` — zero lub jeden,
- `o{` — zero lub wiele.

### 7.1. Osoby, konta i uwierzytelnianie

```mermaid
erDiagram
    Persons {
        uuid Id PK
        string FirstName1
        string LastName
        boolean IsActive
    }
    Accounts {
        uuid Id PK
        uuid PersonId FK
        string Login
        string Email
        boolean IsPrimary
    }
    AccountCredentials {
        uuid Id PK
        uuid AccountId FK
        string PasswordHash
    }
    PersonPreferences {
        uuid Id PK
        uuid PersonId FK
        string DateFormat
        string Theme
        string AvatarColor
    }
    AuthSessions {
        uuid Id PK
        uuid PersonId FK
        string TokenHash UK
        datetime ExpiresAt
        datetime RevokedAt
        datetime LastSeenAt
    }
    Students {
        uuid Id PK
        uuid PersonId FK
        string AcademicNumber
    }
    Teachers {
        uuid Id PK
        uuid PersonId FK
        uuid AcademicTitleId FK
        string ShortName
    }
    AcademicTitles {
        uuid Id PK
        string Name
        string ShortName
    }

    Persons ||--o{ Accounts : posiada
    Accounts ||--o{ AccountCredentials : zabezpiecza
    Persons ||--o{ PersonPreferences : ustawia
    Persons ||--o{ AuthSessions : otwiera
    Persons ||--o{ Students : moze_byc
    Persons ||--o{ Teachers : moze_byc
    AcademicTitles o|--o{ Teachers : opisuje
```

### 7.2. Role, menu i uprawnienia

```mermaid
erDiagram
    Persons {
        uuid Id PK
    }
    Roles {
        uuid Id PK
        string Code UK
        string Name
    }
    PersonRoles {
        uuid Id PK
        uuid PersonId FK
        uuid RoleId FK
        datetime ValidFrom
        datetime ValidTo
    }
    SystemItems {
        uuid Id PK
        uuid ParentItemId FK
        string Code UK
        string Name
        string Route
        int SortOrder
        boolean ShowInMenu
    }
    RolePermissions {
        uuid Id PK
        uuid RoleId FK
        uuid SystemItemId FK
        string ActionCode
    }
    PersonPermissionOverrides {
        uuid Id PK
        uuid PersonId FK
        uuid SystemItemId FK
        string ActionCode
        boolean IsAllowed
    }

    Persons ||--o{ PersonRoles : otrzymuje
    Roles ||--o{ PersonRoles : przypisuje
    Roles ||--o{ RolePermissions : ma
    SystemItems ||--o{ RolePermissions : jest_dozwolony
    Persons ||--o{ PersonPermissionOverrides : ma_wyjatek
    SystemItems ||--o{ PersonPermissionOverrides : jest_nadpisany
    SystemItems o|--o{ SystemItems : rodzic_dziecko
```

### 7.3. Kierunki, studia, programy i przedmioty

```mermaid
erDiagram
    FieldsOfStudies {
        uuid Id PK
        string SchoolName
        string DepartmentName
        string FieldOfStudies
    }
    ModesOfStudies {
        uuid Id PK
        string Name
        string ShortName
    }
    StudiesDegrees {
        uuid Id PK
        string Name
        string ShortName
    }
    Studies {
        uuid Id PK
        uuid FieldsOfStudiesId FK
        uuid ModesOfStudiesId FK
        uuid StudiesDegreeId FK
        string Name
        date StartDate
        int NumberOfSemesters
    }
    Specializations {
        uuid Id PK
        uuid StudiesId FK
        string Name
    }
    ProgramsOfStudies {
        uuid Id PK
        string ProgramName
        string KRKNumber
        string KRKName
    }
    SubjectTypes {
        uuid Id PK
        string Name
        string ShortName
    }
    Subjects {
        uuid Id PK
        uuid SubjectTypeId FK
        uuid ProgramsOfStudyId FK
        uuid StudiesId FK
        uuid SpecializationId FK
        string Name
        string Symbol
        decimal EctsPoints
    }
    Semesters {
        uuid Id PK
        string Name
        string AcademicYearName
        datetime StartDate
        datetime EndDate
        boolean IsActual
    }
    SubjectsSemesters {
        uuid Id PK
        uuid SubjectId FK
        uuid SemesterId FK
        int SemesterNumber
        int NumberOfHybridHours
        int NumberOfPlanningHours
    }

    FieldsOfStudies o|--o{ Studies : klasyfikuje
    ModesOfStudies o|--o{ Studies : okresla_tryb
    StudiesDegrees o|--o{ Studies : okresla_stopien
    Studies o|--o{ Specializations : ma
    Studies o|--o{ Subjects : obejmuje
    Specializations o|--o{ Subjects : zawiera
    ProgramsOfStudies o|--o{ Subjects : definiuje
    SubjectTypes o|--o{ Subjects : typuje
    Subjects o|--o{ SubjectsSemesters : jest_realizowany
    Semesters o|--o{ SubjectsSemesters : planuje
```

### 7.4. Grupy i organizacja planu

```mermaid
erDiagram
    Studies {
        uuid Id PK
    }
    Specializations {
        uuid Id PK
    }
    Semesters {
        uuid Id PK
    }
    Students {
        uuid Id PK
    }
    Groups {
        uuid Id PK
        uuid StudiesId FK
        uuid SemesterId FK
        uuid SpecializationId FK
        string Name
        int SemesterNumber
    }
    GroupMembers {
        uuid Id PK
        uuid StudentId FK
        uuid GroupId FK
        datetime StartDate
        datetime EndDate
    }
    SemesterCalendars {
        uuid Id PK
        uuid SemesterId FK
        string Name
    }
    SemesterCalendarDates {
        uuid Id PK
        uuid SemesterCalendarId FK
        datetime Date
        int Number
    }
    DailyScheduleTypes {
        uuid Id PK
        string Name
        string ShortName
    }
    DailySchedules {
        uuid Id PK
        uuid DailyScheduleTypeId FK
        int Number
        string StartHour
        string EndHour
    }
    LectureGroupTypes {
        uuid Id PK
        string Name
        string ShortName
    }
    LectureGroups {
        uuid Id PK
        uuid SemesterId FK
        uuid LectureGroupTypeId FK
        uuid SemesterCalendarId FK
        uuid DailyScheduleTypeId FK
        string Name
    }
    LectureGroupsElements {
        uuid Id PK
        uuid GroupId FK
        uuid LectureGroupId FK
    }
    SubjectsSemesters {
        uuid Id PK
    }
    Teachers {
        uuid Id PK
    }
    SemesterOrganizations {
        uuid Id PK
        uuid SubjectsSemesterId FK
        uuid LectureGroupId FK
        uuid TeacherId FK
    }
    SemesterOrganizationSchedules {
        uuid Id PK
        uuid SemesterOrganizationId FK
        uuid SemesterCalendarDateId FK
        uuid DailyScheduleId FK
        uuid TeacherId FK
        string LectureRoomName
        boolean IsCanceled
        boolean IsMoved
    }

    Studies o|--o{ Groups : grupuje
    Semesters o|--o{ Groups : obowiazuje_w
    Specializations o|--o{ Groups : specjalizuje
    Students ||--o{ GroupMembers : nalezy_przez
    Groups ||--o{ GroupMembers : zawiera
    Semesters o|--o{ SemesterCalendars : ma_kalendarz
    SemesterCalendars o|--o{ SemesterCalendarDates : zawiera_dni
    DailyScheduleTypes o|--o{ DailySchedules : definiuje_bloki
    Semesters o|--o{ LectureGroups : ma_grupy_zajec
    LectureGroupTypes o|--o{ LectureGroups : okresla_forme
    SemesterCalendars o|--o{ LectureGroups : przypisuje_kalendarz
    DailyScheduleTypes o|--o{ LectureGroups : przypisuje_typ_dnia
    Groups o|--o{ LectureGroupsElements : laczy
    LectureGroups o|--o{ LectureGroupsElements : laczy
    SubjectsSemesters o|--o{ SemesterOrganizations : organizuje
    LectureGroups o|--o{ SemesterOrganizations : realizuje
    Teachers o|--o{ SemesterOrganizations : prowadzi
    SemesterOrganizations o|--o{ SemesterOrganizationSchedules : ma_terminy
    SemesterCalendarDates o|--o{ SemesterOrganizationSchedules : odbywa_sie
    DailySchedules o|--o{ SemesterOrganizationSchedules : ma_godzine
    Teachers o|--o{ SemesterOrganizationSchedules : zastepuje_lub_prowadzi
```

### 7.5. Dane studenta używane w demonstracji

```mermaid
erDiagram
    Persons {
        uuid Id PK
    }
    Students {
        uuid Id PK
        uuid PersonId FK
    }
    Subjects {
        uuid Id PK
        string Symbol
        string Name
    }
    StudentGrades {
        uuid Id PK
        uuid StudentId FK
        uuid SubjectId FK
        decimal Grade
        date GradedOn
    }
    StudentTasks {
        uuid Id PK
        uuid PersonId FK
        string Title
        string Course
        date DueOn
        string Priority
        string Status
    }
    StudentRequests {
        uuid Id PK
        uuid PersonId FK
        string RequestType
        string Content
        string Status
        date SubmittedOn
    }

    Persons ||--o{ Students : posiada_profil
    Students ||--o{ StudentGrades : otrzymuje
    Subjects ||--o{ StudentGrades : dotyczy
    Persons ||--o{ StudentTasks : tworzy
    Persons ||--o{ StudentRequests : sklada
```

### 7.6. Tabele bez wymuszonej relacji

```mermaid
erDiagram
    Countries {
        uuid Id PK
        string Name
        string Citizenship
        string Code
    }
    Notes {
        uuid Id PK
        string Table
        uuid TableId
        string Comment
        datetime CreatedAt
        string CreatedBy
        boolean Pin
    }
```

`Countries` nie ma w obecnym schemacie fizycznego klucza obcego z `Persons`.
Pola osoby przechowują jedynie kody kraju. `Notes` jest celowo relacją
polimorficzną: para `Table` + `TableId` wskazuje logicznie dowolny rekord, ale
SQLite nie może wymusić takiego klucza obcego.