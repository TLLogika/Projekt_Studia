# Powiązanie implementacji z materiałami sources

Źródła: `sources/Technical_asumptions_to_project`, `sources/Transkrypcja_zalozen.txt`,
`sources/Database.md`, `sources/UI.jpg`. Wszystkie operacje trwałe używają istniejącego
SQL Server. Dane testów automatycznych pozostają wyłącznie w pamięci testów.

## Założenia techniczne i UI

| Wymaganie | Implementacja / weryfikacja |
| --- | --- |
| Python, Streamlit, moduły | `app.py`, `academic/modules.py`, osobne usługi i ekrany |
| Baza online, połączenie przez .env | `config.py`, `db.py`; metadane rzeczywistego SQL Server |
| Secret poza repozytorium | `.gitignore`; hashe i dane konta w `secret`, uprawnienia 600 |
| Niezmienna struktura bazy | brak DDL, migracji, lokalnej zastępczej bazy; istniejące klucze i domyślne wartości |
| Różni użytkownicy/widoki | `auth/service.py`, autoryzacja każdego zapisu, menu z `SystemItems`, zakresy kalendarza |
| Ciemny UI, panel boczny, listy rozwijane, dodawanie/edycja | `ui/styles.py`, `app.py`, `ui/semesters.py`, `ui/catalogs.py` |
| Wszystkie formularze JPG | Semestry, Groups, LectureGroups/Types, Subjects/Types, SubjectsSemesters, SemesterOrganizations, SemesterOrganizationSchedules |
| Timestamp automatyczny | tylko do odczytu, wykrywanie równoczesnej edycji na ROWVERSION |
| Czas trwania automatyczny | różnica dat semestru, także przy powiązanych grupach wykładowych/realizacjach |
| Dodatkowy wykładowca i sala | istniejące `TeacherId`, `LectureRoomName`; obaj wykładowcy sprawdzani przy kolizjach |

## Transkrypcja — pełny proces

| Punkt | Zachowanie | Kod / test |
| --- | --- | --- |
| 1 | Semestr musi istnieć i być aktywny do organizacji/planowania | `records.require_active_semester`, usługi organizacji i planu |
| 2 | Niezależne przygotowanie; kontrola przy finalizacji | osobne moduły; `OrganizationService.readiness/finalize` |
| 3–4 | Program, studia, numer semestru, specjalizacja w Subjects | katalogi i rzeczywiste relacje FK |
| 5 | Generator realizacji według tych czterech kryteriów | `CatalogService.preview_subjects/generate_subjects`, `test_catalogs.py` |
| 6 | Nowy program oznacza nowe rekordy Subjects | osobne utworzenie; ochrona zmiany programu istniejącego przedmiotu |
| 7–8 | SubjectsSemesters i wymagana liczba godzin | formularz, generator, limit i bilans `NumberOfPlanningHours` |
| 9 | Grupy przypisane do semestru, studiów, numeru i specjalizacji | Groups, LGE, walidacja organizacji |
| 10 | Organizacja: realizacja + grupa wykładowa + wykładowca | kompletność wymaganych grup i przedmiotów |
| 11–13 | Import prawdziwego planu, podział zakresów na pojedyncze godziny | `importing/reader`, `parser`, `resolver`, `service`; istniejące sloty 45 minut |
| 12 | Rzeczywiste przedmioty; dane wymagają źródłowego planu | brak fikcyjnego arkusza i wymyślonych rekordów; planu nie dostarczono |
| 14 | Osobny rekord każdej godziny | `ScheduleService.create_many`, transakcyjny import |
| 15 | Kalendarz dziekanatu, grupy, wykładowcy, studenta | `views/calendar.py`, `ui/calendar.py`; osobiste ograniczenia przez ID osoby i członkostwa |
| 15 | Członkostwa studentów nie są warunkiem finalizacji | `ReadinessReport`, testy organizacji |
| 16 | Odwołanie i odróbka jednej godziny | `cancel`, `make_up`; ten sam rekord, `IsMoved` |
| 17 | Wymagane, zaplanowane, odwołane, wykonane, do wykonania i nieprzydzielone | `calculate_balance`, `ScheduleService.balance`; `test_workflow.py` |

## Relacje z Database.md

| Domena diagramu | Użycie istniejących tabel |
| --- | --- |
| 7.1–7.2 Tożsamość i ACL | Persons, Accounts, Roles, PersonRoles, SystemItems, RolePermissions, PersonPermissionOverrides; sesje w pamięci procesu, zatwierdzony plik hashy |
| 7.3 Programy i przedmioty | wszystkie tabele tej domeny obsługiwane przez semestry/katalogi/generator |
| 7.4 Grupy i plan | wszystkie tabele tej domeny obsługiwane przez katalogi/organizację/plan/import/kalendarz |
| 7.5 Oceny, zadania, wnioski | tabele demonstracyjne nie istnieją online; brak reguł tego osobnego modułu w JPG/transkrypcji |
| 7.6 Countries/Notes | istniejące słowniki krajów i notatki do rzeczywistych rekordów; bez fizycznego FK notatki |

## Rozbieżności źródeł z niezmienną bazą

- Baza online to SQL Server; końcowy opis `Database.md` wspomina SQLite.
  Połączenie używa faktycznej bazy, bez konwersji.
- `AccountCredentials`, `AuthSessions`, `PersonPreferences`, `StudentGrades`,
  `StudentTasks`, `StudentRequests` nie istnieją online. Zakaz zmiany
  struktury wyklucza ich dodawanie. Lokalne hashe zatwierdzono osobno.
- Tabele typów nie mają pól czasu trwania sugerowanych przez JPG. Nie dodano kolumn.
- Nie ma kolumny trwałej finalizacji organizacji. Przycisk kończenia sprawdza
  aktualną kompletność; wynik nie udaje zapisu nieistniejącego stanu.
- Rzeczywiste `Students.GroupId` jest wymagane; formularz zachowuje tę relację.
  Indywidualny plan wynika jednak z `GroupMembers`, zgodnie z transkrypcją.
- Kolumna ukończenia godziny nazywa się `IsComplited` i tak jest używana w SQL.
- Nie dostarczono arkusza dziekanatu. Import ma jawne mapowanie, podgląd i
  zatwierdzenie; nie zakłada wymyślonego układu pliku ani danych.

## Zakres sprawdzeń

Testy automatyczne sprawdzają ACL, walidacje SQL, generację, kompletność,
kolizje, limit godzin, współbieżność, import atomowy, zakresy osobiste i cały
proces od generacji przez odwołanie do odróbki. Zapytania i pola są porównywane
z rzeczywistymi metadanymi; kontrakty DML sprawdzane przez
`sys.sp_describe_first_result_set` nie wykonują zapisu.

Ręczny test prawdziwego planu wymaga pliku, rzeczywistych danych dydaktycznych
i uprawnień do nowych modułów. Konto testowe pierwotnie zatwierdzono tylko dla
semestrów; rozszerzenie dostępu jest osobnym przygotowanym skryptem.
