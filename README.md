# Organizacja semestru

Modularna aplikacja Python/Streamlit oparta na materiałach w `sources` i istniejącej
bazie SQL Server `Cuda-BIS`, schemat `org`. Odczyty i zatwierdzone zapisy wykonują
zapytania do bazy online. Listy i opcje formularzy są buforowane przez maksymalnie
120 sekund; zapisy zawsze trafiają do SQL Server i odświeżają cache.
Aplikacja nie tworzy tabel, migracji ani danych demonstracyjnych.
Powiązanie wymagań z kodem: [docs/requirements.md](docs/requirements.md).

## Uruchomienie

Wymagany Python 3.11+ oraz Microsoft ODBC Driver 18 for SQL Server.

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python -m streamlit run app.py
```

Adres lokalny: `http://localhost:8501`. Po zmianie konfiguracji trzeba zrestartować
proces Streamlit. Dane połączenia są w `secret/.env`; wzór bez sekretów znajduje
się w `.env.example`. Obsługiwane są `KEY=value` i dostarczony format
`klucz: wartość`, w tym `server_name` z końcowym przecinkiem. Zmienne środowiskowe
mają pierwszeństwo. Połączenie jest szyfrowane; ten serwer ma certyfikat
self-signed. Przy zaufanym certyfikacie ustaw `DB_TRUST_SERVER_CERTIFICATE=false`.
Katalogi `secret`/`Secret`, pliki `.env`, lokalne hashe i sekrety Streamlit są
wykluczone w `.gitignore`.

## Logowanie i dostęp

Do testowania wszystkich nowych ekranów utworzono na wyraźne życzenie użytkownika
osobne konto **`dziekanat.moduly.test`** z rolą `full_module_test_operator`.
Hasło jest w `secret/full-module-test-account.txt`. Konto ma podgląd, tworzenie
i edycję modułów dziekanatu, import planu i kalendarz dziekanatu. Skrypt:
`scripts/create_full_module_test_account.py`. Nie zmienia starego konta ani
struktury bazy i nie tworzy danych zajęć.

Zatwierdzone wcześniej konto testowe to `dziekanat.test`. Hasło znajduje się
w lokalnym `secret/test-account.txt`. Konto, osoba, rola i uprawnienia pochodzą
z istniejących tabel SQL Server. Zatwierdzony dostawca `AUTH_PROVIDER=local_file`
weryfikuje hash Argon2 z `secret/test_accounts.json`, powiązany z prawdziwym
`Accounts.Id`. Oba pliki mają uprawnienia 600. Nie są tabelami bazy.

Pierwotna zgoda obejmowała tylko `view`, `create`, `update` semestrów.
Przygotowany `scripts/enable_full_module_test_access.py` rozszerza tę rolę na
pozostałe moduły dziekanatu; wymaga oddzielnie zatwierdzonego rozszerzenia dostępu.
Nie zmienia hasła, schematu ani danych dydaktycznych. Nie uruchamia się przy
starcie aplikacji. Bez dodatkowych uprawnień nowe ekrany są ukryte dla konta testowego.

Docelowe konta mogą używać istniejącej tabeli `AccountCredentials` lub
`AspNetUsers`, wskazanej przez `AUTH_DATABASE`, `AUTH_SCHEMA`, `AUTH_TABLE`.
W udostępnionej bazie tych tabel obecnie nie ma; ich wskazanie nie tworzy tabeli.
Administrator musi zapewnić rzeczywiste konta i źródło haseł. Role, terminy ich
ważności i indywidualne odmowy są sprawdzane przy każdym odczycie i zapisie.
Odmowa ma pierwszeństwo. Sesje mają limit ważności i bezczynności; restart
serwera wymaga ponownego logowania. `ValidTo` jest granicą wyłączną.

Kody menu/uprawnień: `semesters` (konfigurowalny `SEMESTERS_ITEM_CODE`),
`groups`, `lecture_groups`, `lecture_group_types`, `subjects`, `subject_types`,
`subjects_semesters`, `dictionaries`, `organization`, `schedule`, `schedule_import`.
Zakresy kalendarza: `calendar.dean`, `calendar.teacher`, `calendar.student`, akcja
`view`. Zakresy osobiste wiążą dane z osobą zalogowaną. Nie wystarczy nazwa roli.

## Ręczne sprawdzenie procesu

1. Zaloguj się, wybierz „Semestr”, kliknij „Dodaj semestr”, wpisz własną nazwę,
   rok i daty, ustaw „Aktywny”, zatwierdź „Utwórz semestr”. Rekord pojawi się na liście.
2. Przy dostępie do pozostałych modułów uzupełnij rzeczywiste programy, studia,
   przedmioty, wykładowców, grupy, kalendarze zjazdów i godziny zajęć. Formularze
   relacji pokazują istniejące rekordy. Przygotowanie nie jest kreatorem.
3. W „Przedmioty w semestrze” wybierz program, studia, numer semestru i ewentualną
   specjalizację. Sprawdź podgląd i wygeneruj realizacje. Brak liczby godzin
   w przedmiocie trzeba uzupełnić; generator nie zgaduje wartości.
4. Powiąż grupy z grupami wykładowymi. W „Organizacja semestru” połącz realizację
   przedmiotu, grupę wykładową i wykładowcę. Kontrola kompletności pokazuje braki
   przed zakończeniem organizacji; członkostwa studentów nie są jej warunkiem.
5. W „Harmonogram i bilans” przypisz datę, istniejący slot 45 minut i salę.
   Możesz też dodać dodatkowego wykładowcę. Sprawdzane są kolizje wykładowców,
   grup i sal oraz limit godzin dla realizacji przedmiotu i grupy wykładowej.
6. W „Import planu” wczytaj swój rzeczywisty CSV/XLSX. Wybierz arkusz, wiersz
   nagłówka i mapowanie kolumn. Podgląd sprawdza dopasowania i konflikty bez
   zapisu. Zatwierdzenie ponownie sprawdza aktualną bazę i zapisuje cały import
   w jednej transakcji. Zakres 08:00–09:30 wymaga dwóch istniejących slotów 45 minut.
7. Odwołaj jedną godzinę: wzrośnie liczba nieprzydzielonych. Wyznacz odróbkę,
   a godzina wróci do bilansu. Można oznaczyć jej wykonanie.
8. „Kalendarz zajęć” pokazuje dzień, tydzień lub miesiąc w przyznanym zakresie.
   Dziekanat może filtrować semestr i grupę wykładową. Plan studenta wynika
   z `GroupMembers` i `LectureGroupsElements`, z ważnością członkostwa w chwili
   zajęć (`EndDate` wyłączne). Plan wykładowcy obejmuje prowadzącego i dodatkowego
   wykładowcę.

W `sources` nie ma rzeczywistego arkusza planu ani kompletu danych dydaktycznych.
Importu własnego planu nie można zweryfikować bez tego pliku i jego rekordów w bazie.
Aplikacja nie uzupełnia tych braków przykładowymi zajęciami.

## StudiesId i szybkość formularzy

`StudiesId` jest identyfikatorem istniejących studiów. W formularzach grup i
przedmiotów wybierz je po nazwie w polu **Rodzaj studiów**; UUID przypisze aplikacja.
Jeżeli lista jest pusta, w **Słowniki** uzupełnij **Kierunki studiów**, **Tryby
studiów** i **Stopnie studiów**, a następnie **Studia**. Studia wymagają nazwy,
skrótu, kierunku, trybu, stopnia, daty rozpoczęcia i liczby semestrów. Włącz
**Aktywny** i kliknij **Utwórz**. Po zapisie nowa pozycja jest dostępna w powiązanych
formularzach. Istniejący nieaktywny rekord można edytować i włączyć.

Pola formularzy są wysyłane razem przy **Utwórz/Zapisz zmiany**. Wybór studiów,
semestru lub organizacji pozostaje reaktywny tam, gdzie określa dostępne opcje
innych pól. Obliczenia dat pojawiają się po zatwierdzeniu danych formularza.
**Anuluj** odrzuca zmiany. Generator ma osobny przycisk **Pokaż przedmioty**;
wyświetlenie podglądu nie zapisuje realizacji.

Listy, dane kalendarza i opcje wyboru są buforowane przez **120 sekund** osobno
dla sesji użytkownika i zakresu uprawnień. Każdy udany zapis unieważnia cache
wszystkich użytkowników tego procesu aplikacji. Dla zmian wprowadzonych poza
aplikacją użyj **Odśwież dane** w lewym menu albo poczekaj na wygaśnięcie cache.
Uprawnienia menu są odczytywane przy każdym renderowaniu w jednym połączeniu;
każdy zapis ponownie sprawdza aktualne uprawnienia, relacje i wersję w bazie.
Metadane niezmiennego schematu są buforowane do restartu procesu, a odczyt
przesłanego CSV/XLSX przez 10 minut w pamięci, oddzielnie dla sesji.

## Zgodność z bazą

`Id` i `Timestamp` nadaje baza. `Timestamp` to `ROWVERSION`, wyłącznie do odczytu,
używany do wykrywania równoczesnej edycji. Czas trwania semestru jest obliczany
z dat. Nie dodano kolumn czasu trwania do tabel typów, bo nie istnieją w bazie.
Stan wykonania godziny używa rzeczywistej nazwy kolumny `IsComplited`.

Zakończenie organizacji to kontrola aktualnych zależności. Schemat nie ma flagi
jej trwałego zamknięcia, więc aplikacja jej nie dopisuje. Zmiany katalogów i planu
współdzielą blokadę transakcyjną; edycja nie może unieważnić istniejących przydziałów.
Zmiana dat semestru nie może wykluczyć aktywnych dat jego kalendarzy.

Diagram `sources/Database.md` zawiera także `AccountCredentials`, `AuthSessions`,
`PersonPreferences`, `StudentGrades`, `StudentTasks` i `StudentRequests`,
których nie ma w udostępnionym schemacie. Nie utworzono tych tabel. Materiały te
nie dostarczają reguł osobnego modułu ocen, zadań ani wniosków; rozbieżności
źródeł i bazy opisano w macierzy wymagań.

## Moduły i weryfikacja

`academic/auth` obsługuje tożsamość i uprawnienia, `semesters` semestry,
`catalogs` katalogi i generator, `organization` przydziały i kompletność,
`scheduling` godziny i bilans, `importing` transformację plików,
`views/calendar.py` zakresy kalendarza, `notes` notatki do dostępnych rekordów.
Słowniki obejmują również istniejącą tabelę krajów. `academic/ui` zawiera ekrany Streamlit;
`app.py` i `academic/modules.py` łączą je z nawigacją. `db.py` i `records.py`
zapewniają wspólne transakcje i walidację rzeczywistych typów SQL.

```bash
python -m academic.diagnostics
python -m pip install -r requirements-dev.txt
python -m pytest -q
```

Diagnostyka wykonuje tylko odczyty. Kod 2 oznacza brak konfiguracji kont.
Testy automatyczne używają odizolowanych fixtures w pamięci, w tym całego
procesu od generatora do odróbki oraz importu. Nie tworzą rekordów w bazie online.
