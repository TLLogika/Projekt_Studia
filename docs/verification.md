# Weryfikacja ukończonej implementacji

- `python -m pytest -q`: **268 testów przeszło**. Testy nie łączą się z bazą
  online; dane fixtures pozostają w pamięci. Obejmują również przepływ
  generator → organizacja → harmonogram → odwołanie → odróbka → wykonanie,
  import z podglądem i zapisem atomowym, zakresy kalendarza oraz wszystkie
  gałęzie nawigacji `app.py` i cofnięcie uprawnień.
- `python -m compileall -q academic app.py scripts`: brak błędów składni.
- Rzeczywisty SQL Server: odczytano metadane kolumn, typów, kluczy i domyślnych
  wartości. **54 kontrakty INSERT/UPDATE** katalogów, semestrów, organizacji,
  harmonogramu i notatek poprawnie skompilowano przez
  `sys.sp_describe_first_result_set`. Teksty DML nie zostały wykonane.
- Zapytania kalendarzy dziekanatu, wykładowcy i studenta oraz filtrów grup
  wykonano jako odczyty rzeczywistej bazy. Przetestowano także blokadę
  transakcyjną i odczyt istniejącego semestru.
- Chromium: prawdziwe logowanie `dziekanat.test`, otwarcie formularza semestru,
  pola początkowo puste, Timestamp tylko do odczytu, poprawne wylogowanie,
  brak wyjątków Streamlit i błędów JavaScript. Nie utworzono semestru w tym teście.
- `secret/.env`, `secret/test_accounts.json`, `secret/test-account.txt`
  mają uprawnienia **600** i są objęte regułami `.gitignore`.

Pierwotne konto `dziekanat.test` nadal ma prawa tylko do semestrów. Rozszerzenie
na nowe ekrany jest przygotowane w `scripts/enable_full_module_test_access.py`
i oczekuje osobnego zatwierdzenia. Nie było wykonane w ramach powyższych testów.

Na późniejsze żądanie użytkownika utworzono osobne `dziekanat.moduly.test`
z dostępem do nowych modułów. Hasło zapisano w ignorowanym
`secret/full-module-test-account.txt`; hashe istniejących kont zachowano.

W materiałach nie dostarczono rzeczywistego CSV/XLSX z planem ani kompletnego
zbioru danych dydaktycznych. Testy potwierdzają logikę na odizolowanych fixtures
i zgodność zapytań z bazą. Nie potwierdzają importu konkretnego, niedostarczonego
arkusza ani pełnego procesu zapisu zajęć online. Rozbieżności diagramu i schematu
są wyszczególnione w [requirements.md](requirements.md).
