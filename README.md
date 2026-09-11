# RollForm APS

Silnik planowania i harmonogramowania produkcji (APS — Advanced Planning & Scheduling) dla linii profilowania blachy. Aplikacja przekształca eksport zleceń produkcyjnych z ERP w harmonogram zmianowy z rozdzielczością minutową, uwzględniając ograniczenia maszyn, operatorów, kalendarza pracy oraz współdzielonych zasobów dźwigowych.

Projekt zastępuje wcześniejsze, jednoprzebiegowe makro VBA modelem wielofazowym: heurystyka konstrukcyjna sterowana terminami, batching katalogowy z oknem lookahead, symulacja zajętości suwnic oraz post-procesor optymalizacyjny oparty na lokalnym przeszukiwaniu 2-opt z losowymi restartami.

---

## Spis treści

1. [Architektura](#architektura)
2. [Model danych](#model-danych)
3. [Silnik planowania](#silnik-planowania)
4. [Uruchomienie i odtworzenie planu](#uruchomienie-i-odtworzenie-planu)
5. [Konfiguracja](#konfiguracja)
6. [Struktura repozytorium](#struktura-repozytorium)
7. [Ograniczenia i kierunki rozwoju](#ograniczenia-i-kierunki-rozwoju)

---

## Architektura

```
┌────────────────────────────────────────────────────────────────────┐
│  Streamlit (frontend/)                                             │
│  1 Import → 2 Maszyny → 3 Operatorzy → 4 Kalendarz → 5 Planner     │
│        │                                              │            │
│        │ walidacja + normalizacja xlsx                │ Gantt      │
│        ▼                                              │ Widok zmian│
│  session_state (DataFrame zleceń)                     │ KPI / SMED │
│        │                                              │ Excel      │
└────────┼──────────────────────────────────────────────┼────────────┘
         │ przypisz_maszyny() → planuj() → optimize_crane_idle()
         ▼
┌────────────────────────────────────────────────────────────────────┐
│  backend/planning/engine.py  (silnik V4, czysty pandas, bez I/O)   │
│  ├─ mapowanie Twr_Profil → maszyna, Twr_Profil_Glowny → kategoria  │
│  ├─ auto-alokacja operatorów (kompetencje, rotacja A/B, urlopy)    │
│  ├─ harmonogram zmianowy: must_go → batch(lookahead) → rolling     │
│  ├─ model przezbrojeń 0 / 6 / 12 min z carry-over stanu maszyny    │
│  ├─ kalendarz rezerwacji suwnic (FIFO, per zakład × kategoria)     │
│  └─ post-procesor 2-opt + random restarts (min. czasu czekania)    │
└────────────────────────────────────────────────────────────────────┘
         │
         ▼
  backend/config.json  (maszyny, operatorzy, kalendarz, zmiany, urlopy)
  backend/plans/*.json (wersjonowane plany — snapshot każdego przebiegu)

  backend/main.py  (FastAPI — opcjonalna warstwa integracyjna: proxy do
                    OperatorPanelAPI z obsługą CORS i korporacyjnych CA)
```

Frontend importuje silnik bezpośrednio jako moduł Pythona. Backend FastAPI nie jest wymagany do planowania — służy wyłącznie integracji z systemami zewnętrznymi.

---

## Model danych

### Wejście: eksport zleceń (xlsx)

Jeden wiersz = jedno zlecenie produkcyjne (ZP). Kolumny wymagane przez walidator importu:

| Kolumna | Rola w silniku |
|---|---|
| `Numer_zlecenia` | klucz główny ZP |
| `Nazwa_zakladu_Grupa` | filtr zakładu (Zabornia / Sękocin) |
| `Twr_Profil` | profil szczegółowy → determinuje maszynę |
| `Twr_Profil_Glowny` | kategoria główna → kompetencje operatorów, grupa suwnicy |
| `Twr_Katalog` | klucz batchowania; koduje kolor RAL, grubość, flizelinę (`F`) i typ Dach/Elewacja (`D`/`E`) |
| `Ilosc_MBSZT` | ilość w metrach bieżących (format z przecinkiem dziesiętnym) |
| `LD` | termin realizacji (deadline) |
| `Zrodlo_zam` | opcjonalnie: `wewnetrzne` / `zewnetrzne` — zlecenia wewnętrzne mają odrębną politykę planowania |

Pozostałe kolumny eksportu ERP są przenoszone, ale nie wpływają na wynik.

### Wyjście: plan (JSON + Excel)

Każdy wiersz planu to segment na osi czasu jednej maszyny: produkcja, przezbrojenie, przejście operatora lub oczekiwanie na suwnicę. Kolumny: `Maszyna, Twr_Profil_Glowny, Data, Start, Koniec, ZP, Twr_Katalog, RAL, MB, Minuty, LD, Total_kat_min, Total_kat_MB, kolor_id, is_zonk, spoznienie, is_wew, Operator`.

Plany są zapisywane niemutowalnie w `backend/plans/` z sygnaturą czasową, co pozwala porównywać kolejne scenariusze (np. wpływ zmiany lookahead na liczbę przezbrojeń).

---

## Silnik planowania

Silnik pracuje w pętli `dzień → zmiana → operator → maszyna`, z globalnym stanem `last_kat[maszyna]` przenoszonym przez dni wolne (brak resetu przezbrojenia po weekendzie).

### Faza 0 — przypisanie zasobów

- **Maszyny**: `Twr_Profil` → maszyna na podstawie macierzy z konfiguracji. Profil bez maszyny trafia do listy nieprzypisanych; profil przypisany do wielu maszyn w zakładzie blokuje planowanie jako błąd konfiguracji.
- **Kategorie**: most `Twr_Profil → Twr_Profil_Glowny` budowany dynamicznie z danych zleceń, dzięki czemu kompetencje operatorów definiowane są na poziomie kategorii, a nie kilkudziesięciu modeli.
- **Operatorzy**: alokacja round-robin od najbardziej obciążonej maszyny, z filtrem kompetencji, rotacją zmian A/B (tydzień parzysty/nieparzysty względem daty referencyjnej) oraz urlopami. Brak operatorów → fallback do planowania czysto maszynowego.

### Faza 1 — must_go

Zaległości (`LD < D`) oraz zlecenia krytyczne (`D ≤ LD ≤ NWD(D)`, gdzie NWD = następny dzień roboczy) są planowane bezwarunkowo. Mogą przekroczyć koniec zmiany — takie przypadki są oznaczane jako `is_zonk` i raportowane osobno.

### Faza 2 — batch z lookahead

Do każdego katalogu z must_go dołączane są zlecenia tego samego katalogu z `LD ≤ D + lookahead`. Większe okno → dłuższe serie → mniej przezbrojeń, kosztem wcześniejszego zajęcia mocy. Parametr sterowany z UI; w testach na danych produkcyjnych zmiana lookahead 0 → 30 dni redukowała liczbę przezbrojeń o ok. 16 %.

### Faza 3 — rolling

Jeśli po must_go zostają minuty, dociągane są zlecenia z jutrzejszego must_go tej samej maszyny, bez prawa do zonka. Zlecenia wewnętrzne (`wew`) wchodzą dopiero jako wypełniacz pod koniec zmiany, wyłącznie w otwartym katalogu (zerowy koszt przezbrojenia), z możliwością podziału ostatniego ZP na partie między zmianami.

### Model przezbrojeń

| Przejście między katalogami | Koszt |
|---|---|
| identyczny katalog | 0 min |
| ta sama baza, zmiana D ↔ E | 6 min (zmiana koloru, bez suwnicy) |
| katalog z/bez flizeliny przy maszynie z przystawką | 0 min |
| inny katalog | czas przezbrojenia maszyny (domyślnie 12 min, wymaga suwnicy) |

Przejście operatora między maszynami kosztuje stały narzut 20 min i jest pomijane, jeśli po dojściu nie zmieści się już przezbrojenie.

### Suwnice jako zasób współdzielony

Dla kategorii `TRAPEZY`, `BLACHODACHÓWKA`, `PANELE` istnieje jedna fizyczna suwnica per (zakład, kategoria). Każde przezbrojenie ≥ 12 min rezerwuje suwnicę w kalendarzu rezerwacji; kolizja w trakcie zmiany wstawia do planu segment `konflikt_suwnicy` z czasem oczekiwania maszyny. Kolejka działa w semantyce FIFO: maszyna wchodzi w pierwszą wolną chwilę, a nie w pierwszą ciągłą dziurę o długości setupu — to celowa decyzja, która na danych produkcyjnych zredukowała sumaryczny czas konfliktów z 318 do 59 min.

### Faza 4 — post-procesor optymalizacyjny

`optimize_crane_idle` przeszukuje przestrzeń permutacji kolejności katalogów per (grupa suwnicy, dzień): pełny pair-wise 2-opt na każdej maszynie plus losowe restarty z adaptacyjną liczbą prób. Funkcja celu: minimalizacja `Σ konflikt_suwnicy_min` przy zachowaniu ograniczenia terminowego (żadne ZP nie może stać się spóźnione). Zabezpieczenia wydajnościowe: cache kalendarza suwnic per sweep, pominięcie grup bez konfliktów, wczesne wyjście przy zerowym konflikcie, twardy budżet czasowy.

### Wskaźniki

Panel Plannera raportuje per maszyna i dzień: liczbę ZP, MB, czas produkcji, czas setupów, liczbę przezbrojeń, wykorzystanie zmiany (%), MB z flizeliną, a także wskaźniki SMED pozwalające porównywać scenariusze między sobą.

---

## Uruchomienie i odtworzenie planu

### Wymagania

- Python 3.12 (testowane na 3.12.10)
- Windows / Linux / macOS

### Instalacja

```bash
git clone https://github.com/Piotrek158/rollform-aps.git
cd rollform-aps
python -m venv .venv
# Windows:
.venv\Scripts\activate
# Linux / macOS:
source .venv/bin/activate
pip install -r requirements.txt
```

### Start aplikacji

```bash
streamlit run frontend/app.py
```

Przeglądarka otworzy się na `http://localhost:8501`.

### Odtworzenie przykładowego planu

W repozytorium znajduje się wsad testowy `samples/zlecenia_przykladowe.xlsx` (151 zleceń, kategoria TRAPEZY, dwa zakłady, terminy 25.04–05.05.2026). Konfiguracja maszyn, operatorów i kalendarza w `backend/config.json` jest już spójna z tym wsadem.

1. Strona **1 Import**: wybierz zakład **Zabornia** (107 ZP) lub **Sękocin** (44 ZP), wczytaj `samples/zlecenia_przykladowe.xlsx`. Sprawdź podgląd: liczba ZP, rozkład terminów, podział wew/zew.
2. Strona **2 Maszyny**: zweryfikuj prędkości (MB/min), czasy przezbrojeń i flagę przystawki filcu — wartości są już załadowane.
3. Strona **3 Operatorzy**: macierz kompetencji, grupy zmianowe A/B, urlopy. Możesz zostawić bez zmian albo wyczyścić listę operatorów, żeby zobaczyć fallback maszynowy.
4. Strona **4 Kalendarz**: minuty pracy per dzień (960 = dwie zmiany, 480 = jedna, 0 = wolne). Kalendarz pokrywa okres 24.04–30.10.2026.
5. Strona **5 Planner**: ustaw
   - **Data startowa**: `2026-04-27`
   - **Horyzont**: `14` dni
   - **Lookahead**: `3` dni

   i kliknij **Zaplanuj**. Plan zostanie policzony, zoptymalizowany i zapisany do `backend/plans/`.
6. Wynik: filtry daty i kategorii, osobny Gantt per `Twr_Profil_Glowny` (kolory RAL, różowe przezbrojenia, pomarańczowe przejścia operatorów, czerwone konflikty suwnicy, złota ramka = flizelina), pionowy widok zmian, podsumowanie dnia, eksport do Excela.

Eksperyment do porównania: powtórz krok 5 z lookahead `0` i `30` i porównaj kolumnę **Przezbr.** w podsumowaniu oraz liczbę konfliktów suwnicy.

> Uwaga: Streamlit nie przeładowuje importowanych modułów. Po edycji `backend/planning/engine.py` zrestartuj proces Streamlita, inaczej plany liczą się starym kodem.

### Backend integracyjny (opcjonalnie)

```bash
cp .env.example .env      # uzupełnij OPERATOR_PANEL_BASE i OPERATOR_PANEL_KEY
uvicorn backend.main:app --reload
```

Endpoint `GET /api/proxy/last-programming-orders?machineName=…&top=…` pośredniczy w wywołaniach do OperatorPanelAPI (obejście CORS, wstrzyknięcie korporacyjnych certyfikatów CA przez `truststore`). Bez ustawionych zmiennych środowiskowych endpoint zwraca 503; reszta aplikacji działa normalnie.

---

## Konfiguracja

`backend/config.json` (edytowany przez UI; zapis atomowy z backupem `.bak`):

| Sekcja | Zawartość |
|---|---|
| `machines` | id, zakład, prędkość MB/min, limit dzienny, czas przezbrojenia, lista `twr_profil`, `ma_przystawke_filcu` |
| `operators` | imię, `shift_group` (A/B), przypisane maszyny, macierz `skills` po kategoriach |
| `skill_categories` | lista kategorii głównych używanych w macierzy kompetencji |
| `calendar` | `{"YYYY-MM-DD": minuty}` — 0 / 480 / 960 |
| `shift_config.A_first_shift_week` | poniedziałek referencyjny rotacji zmian |
| `operator_vacations` | `{"Imię Nazwisko": ["YYYY-MM-DD", …]}` |

Zmiany 1 (6:00–14:00) i 2 (14:00–22:00) oraz narzut przejścia 20 min są stałymi silnika.

---

## Struktura repozytorium

```
backend/
  main.py                 FastAPI — warstwa integracyjna (opcjonalna)
  config.json             konfiguracja zasobów (maszyny, operatorzy, kalendarz)
  planning/engine.py      silnik planowania V4 (~1300 linii)
  plans/                  wersjonowane snapshoty planów (JSON)
frontend/
  app.py                  strona główna Streamlit
  utils.py                I/O konfiguracji i planów (zapis atomowy, wersjonowanie)
  pages/1_Import.py       walidacja i normalizacja eksportu ERP
  pages/2_Maszyny.py      parametry maszyn
  pages/3_Operatorzy.py   kompetencje, zmiany, urlopy
  pages/4_Kalendarz.py    kalendarz pracy
  pages/5_Planner.py      planowanie, Gantt, widok zmian, KPI, eksport
  components/             własny komponent HTML do zmiany kolejności katalogów
samples/
  zlecenia_przykladowe.xlsx   wsad testowy do odtworzenia planu
ALGORYTM.md               specyfikacja algorytmu (założenia, wyjątki W1–W6)
SILNIK.md                 opis silnika krok po kroku (pseudokod)
journal.md                dziennik zmian
```

Pozostałe pliki xlsx w katalogu głównym to robocze eksporty z kolejnych iteracji (dane wejściowe, plany wynikowe, słowniki prędkości i przypisań profil → maszyna). `base.xlsm` to archiwalny arkusz VBA, na podstawie którego odtworzono logikę referencyjną.

---

## Ograniczenia i kierunki rozwoju

- Wizualizacja spóźnień (`spoznienie`) — kolumna jest w silniku i planie, w UI jeszcze nieeksponowana.
- Tryb kolorowania Gantta wg pilności (dni do LD) z konfigurowalnymi progami.
- Kalendarz per maszyna (obecnie globalny per zakład).
- Priorytety zleceń poza terminem LD.
- Kolejność katalogów zmieniana ręcznie: komponent działa, warstwa wizualna do przeprojektowania.
