# Plan wdrożenia Plannera Produkcji

## Status ogólny
- [x] **Krok 0** — Przegląd projektu i odczyt makr VBA z base.xlsm
- [x] **Krok 1** — Analiza nowej struktury danych (nowe kolumny vs stare)
- [ ] **Krok 2** — Wyjaśnienie pytań otwartych (priorytety, maszyny, dane wejściowe)
- [x] **Krok 3** — Frontend — struktury stron + konfiguratory maszyn i operatorów
- [ ] **Krok 4** — Przegląd i korekta frontendu z użytkownikiem
- [ ] **Krok 5** — Backend: modele + silnik planowania
- [ ] **Krok 6** — Backend: endpointy API
- [ ] **Krok 7** — Frontend: widok wynikowego planu (strona 4)
- [ ] **Krok 8** — Testy i walidacja

---

## Krok 0 — WYKONANY: Odczyt makr VBA z base.xlsm

### Arkusze w pliku Excel
| Arkusz | Rola |
|--------|------|
| `import` | Główna tabela z danymi zleceń produkcyjnych (źródło danych) |
| `dane` | Roboczy bufor — przefiltrowane zlecenia dla jednej maszyny |
| `Lista` | Panel sterowania — parametry maszyny, daty, limity |
| `Lista2` | Słownik maszyn — lista maszyn z parametrami i dziennymi limitami |

### Kluczowe kolumny w arkuszu `import` (stara struktura)
| Nr kol | Litera | Nazwa | Opis |
|--------|--------|-------|------|
| 6 | F | Termin | Termin realizacji zlecenia (deadline) |
| 8 | H | MB | Ilość w metrach bieżących |
| 9 | I | Kod produktu | Kod wyrobu (np. "C2", "C1") |
| 13 | M | ID | Identyfikator — używany do wykrywania końca danych |
| 38 | AL | zonk | MB zrealizowane na czas |
| 40 | AN | Flaga X | Oznaczenie małych zleceń do uzupełnienia |
| 41 | AO | planmin | Czas przezbrojenia (setup) w minutach |
| 42 | AP | min | Czas produkcji w minutach (MB / prędkość) |
| 43 | AQ | Plan | Zaplanowana data (numer dnia) |
| 44 | AR | PlanX | Priorytet: "a" / "b" / "C" / "X" |
| 46+offset | dyn. | Kolor | Kod koloru/wzoru (offset zależny od maszyny) |
| 51 | AY | PlanKol | Zaplanowany kolor (kopia kolumny kolor) |
| 52 | AZ | Maszyna | Nazwa maszyny przypisanej do zlecenia |

### Panel sterowania — arkusz `Lista` (kluczowe komórki)
| Komórka | Znaczenie |
|---------|-----------|
| E1 | Data graniczna 1 (główny deadline, priorytet "a") |
| E3 | Data graniczna 2 (priorytet "b") |
| F3 | Data graniczna 3 (priorytet "C") |
| G3 | Data graniczna 4 (priorytet "X") |
| E8 | Numer startowego dnia planowania |
| E10 | Nazwa aktualnie planowanej maszyny |
| F10 | Dzienny limit MB dla maszyny |
| G10 | Prędkość maszyny (MB/minutę) |
| H10 | Offset kolumny koloru (0-indexed, dodawany do 46) |
| I2 | Liczba dni do przodu (horyzont planowania) |
| N2 | Czas przezbrojenia między kolorami (minuty) |

### Słownik maszyn — arkusz `Lista2`
| Kol | Znaczenie |
|-----|-----------|
| A | Nazwa maszyny |
| B | Dzienny limit MB (domyślny) |
| C | Prędkość (MB/min) |
| D | Offset kolumny koloru |
| E, F, G... | Dzienny limit MB dla dnia 1, 2, 3... |

---

## Logika algorytmu planowania — szczegółowe rozumowanie

### Makro główne: `PlanujKolejke`
Jest to makro-orkiestrator. Wykonuje:
1. Pobiera listę unikalnych maszyn z kolumny AZ arkusza `import` → arkusz `Lista2`
2. Czyści kolumny planowania w `import` (AQ, AT:AW, AY)
3. Dla każdej maszyny z `Lista2`:
   - Ustawia parametry maszyny w `Lista` (E10, F10, G10, H10)
   - Kopiuje tylko zlecenia tej maszyny do roboczego arkusza `dane`
   - Uruchamia `PlanujSeryjnie` (planuje n dni do przodu)
   - Kopiuje wyniki z `dane` z powrotem do `import`

### Makro: `kopiujdane`
Filtruje `import` → `dane`:
- Kopiuje tylko wiersze, gdzie maszyna (kol. 52) = aktualnie planowana maszyna
- Zapisuje oryginalny numer wiersza w `dane` kol. 2 (potrzebne do `kopiujplan`)

### Makro: `kopiujplan`
Zapisuje wyniki planowania z `dane` z powrotem do odpowiednich wierszy `import`:
- Kolumny: Plan(43), PlanX(44), planmin(41), min(42), zonk(38), PlanKol(51)

### Makro: `PlanujSeryjnie`
Pętla po dniach (1..n):
- Dla każdego dnia pobiera limit MB z `Lista2` (kolumny E, F, G...)
- Wywołuje `PlanujD` (= `ListaKol` + `Zaplanujjutro`)
- Przesuwa numer dnia planowania o +1

### Makro: `ListaKol`
Tworzy listę unikalnych kodów kolorów dla niezaplanowanych zleceń tej maszyny, które:
- Nie mają jeszcze przypisanego planu (kol. 43 = "")
- Mają termin <= data graniczna 1 (E1)
Wynik trafia do kolumny A arkusza `Lista`.

### Makro: `Zaplanujjutro` — SERCE ALGORYTMU
Planuje zlecenia w 4 priorytetach (każdy to osobna pętla po kolorach, potem wierszach):

**Priorytet "a"** — zlecenia pilne (termin <= DataZal = E1):
- Grupuje po kolorze
- Dla pierwszego zlecenia w nowym kolorze → dodaje czas przezbrojenia (setup)
- Oblicza czas produkcji = MB / prędkość
- Jeśli zaplanowana data >= termin → oznacza jako "a" i zapisuje MB do "zonk"
- Kontynuuje dopóki `min < mblimit` (limit minut na dany dzień)

**Priorytet "b"** — zlecenia z terminem <= DataZal2 (E3), bez setupu

**Priorytet "C"** — produkty z kodem zaczynającym się na "C" (ale nie "C1"), termin <= DataZal3

**Priorytet "X"** — małe zlecenia (MB < MBmaleZP) z flagą "X", termin <= DataZal4

### Warunek stopu
Każda pętla sprawdza `min < mblimit` (łączny czas produkcji < limit dzienny w minutach).

---

## Krok 1 — WYKONANY: Analiza nowej struktury danych (nowe_dane.xlsx)

### Nowa struktura — plik nowe_dane.xlsx
Jeden arkusz `Sheet1`, **29 kolumn**, 151 wierszy danych.

| Nr kol | Litera | Nazwa kolumny | Odpowiednik w starym VBA |
|--------|--------|---------------|--------------------------|
| 1 | A | Nazwa_zakladu_Grupa | BRAK (nowe pole) |
| 2 | B | Numer_zamowienia_IZAM | BRAK (nowe pole) |
| 3 | C | Numer_zlecenia | BRAK (nowe pole) |
| 4 | D | Status_ZP | BRAK (nowe pole) |
| 5 | E | Twr_Profil | ~kol 9 (kod produktu) |
| 6 | F | Twr_Profil_Glowny | BRAK (zawsze "TRAPEZY") |
| 7 | G | Data_Termin_Realizacji | **kol 6 (F) = Termin** |
| 8 | H | Ilosc (sztuki) | — |
| 9 | I | Ilosc_MB | **kol 8 (H) = MB** |
| 10 | J | Ilosc_MBSZT | — |
| 11 | K | LD | — (dodatkowa data) |
| 12 | L | Rodzaj_Skladnika | — |
| 13 | M | Trasa | **kol 52 (AZ) = Maszyna** |
| 14 | N | Twr_Flizelina | — |
| 15 | O | Twr_Katalog | — |
| 16 | P | Twr_Kod | — |
| 17 | Q | Twr_Pelna_Grupa | — |
| 18 | R | Twr_Profil_Glowny (dubl.) | — |
| 19 | S | Twr_Profil_opcje | — |
| 20 | T | Twr_Profil_opcje_kod | — |
| 21 | U | Twr_Profil_specyfikacja | — |
| 22 | V | Twr_Typ | — |
| 23 | W | Twr_TypTowaru | — |
| 24 | X | Typ_Rodzaju_Skladnika | — |
| 25 | Y | ZP_KEY | — |
| 26 | Z | Zrealizowane | — |
| 27 | AA | Zrodlo_zam | — |
| 28 | AB | Zwolnione_do_prod | — |
| 29 | AC | Kolor_material_grubosc | **kol 46+offset = Kolor** (teraz bezpośrednio!) |

### Potwierdzone mapowanie: stare VBA → nowe dane (ZAKTUALIZOWANE)

| Pole logiczne | Stare (VBA kol) | Nowe (xlsx kol) | Uwagi |
|---------------|-----------------|-----------------|-------|
| **MB** | kol 8 (H) | kol 10 (J) `Ilosc_MBSZT` | Tekst z przecinkiem "43,2" → float! |
| **Termin** | kol 6 (F) | kol 11 (K) `LD` | datetime |
| **Grupowanie (dawny Kolor/setup)** | kol 46+offset | kol 15 (O) `Twr_Katalog` | Bezpośredni tekst, bez offsetu |
| **Maszyna** | kol 52 (AZ) | **konfiguracja UI** | Nie z xlsx! Przypisywana przez Twr_Profil |
| **Zakład** | brak | kol 1 (A) `Nazwa_zakladu_Grupa` | Zabornia / Sękocin — planowane osobno |
| Identyfikator zlecenia | — | kol 3 (C) `Numer_zlecenia` | klucz główny |
| Typ profilu (→ maszyna) | — | kol 5 (E) `Twr_Profil` | relacja: Twr_Profil → Maszyna |

### Kluczowe zmiany architektoniczne

#### 1. Maszyna — największa zmiana: z kolumny xlsx → konfiguracja UI
- Maszyny **nie ma w pliku xlsx** — jest definiowana w aplikacji
- Relacja: **1 maszyna obsługuje wiele Twr_Profil** (np. "Linia 1" → TRAPEZ T18, TRAPEZ T20)
- Przy imporcie xlsx: każde zlecenie dostaje maszynę przez lookup `Twr_Profil → Maszyna`
- Kolumna `Trasa` (M) z xlsx — ignorowana przy planowaniu (to linia transportowa, nie produkcyjna)

#### 2. Grupowanie dla przezbrojenia: kolor+offset → Twr_Katalog
- **Stare:** skomplikowany offset per maszyna (kol 46 + wartość z panelu sterowania)
- **Nowe:** bezpośrednio `Twr_Katalog` (kol O) — ten sam dla wszystkich maszyn
- Logika setupu: pierwsze zlecenie z nowym `Twr_Katalog` w danym dniu → +czas_przezbrojenia
- Przykład wartości: "T20PM8017AM50F125D", "T18HM8019IN50F125D"

#### 3. MB — uwaga na format danych
- `Ilosc_MBSZT` (kol J) = tekst z przecinkiem: `"43,2"` → przy imporcie: `float("43,2".replace(",","."))`

#### 4. Priorytety — nie implementujemy teraz
- Brak a/b/C/X — proste planowanie: kolejność po terminie `LD`, grupowanie po `Twr_Katalog`

#### 5. Zakłady — osobno
- Zabornia i Sękocin mają osobne maszyny i osobne planowanie
- Filtr przy imporcie: `Nazwa_zakladu_Grupa == wybrany_zaklad`

### Uproszczona logika algorytmu (z VBA → Python)
```
dla każdej maszyny w zakładzie:
  zlecenia = [z for z in dane if Twr_Profil[z] należy do maszyny]
  sortuj zlecenia po: Twr_Katalog, potem LD
  suma_minut = 0
  aktualny_katalog = None
  dla każdego dnia (horyzont):
    dla każdego zlecenia (niezaplanowane):
      czas = Ilosc_MBSZT / predkosc_maszyny
      if Twr_Katalog != aktualny_katalog:
        czas += czas_przezbrojenia
        aktualny_katalog = Twr_Katalog
      if suma_minut + czas <= limit_dzienny_minut:
        przypisz dzien planowania
        suma_minut += czas
```

---

## Architektura docelowa (potwierdzona)

```
backend/
  planning/
    engine.py         # algorytm planowania
    models.py         # Order, Machine, Operator, Plan, PlanResult
  routers/
    planning.py       # POST /plan — uruchom planowanie
    machines.py       # CRUD maszyn + przypisanie Twr_Profil
    operators.py      # CRUD operatorów + macierz kompetencji
    import_data.py    # POST /import — upload xlsx

frontend/
  pages/
    1_Import.py       # upload xlsx, wybór zakładu
    2_Maszyny.py      # konfigurator maszyn: nazwa, prędkość, limit, przezbrojenie, Twr_Profil
    3_Operatorzy.py   # konfigurator operatorów + macierz kompetencji (checkboxy)
    4_Planner.py      # widok wynikowego planu
```

### Konfigurator maszyn (UI) — co musi mieć
- Dodaj / edytuj / usuń maszynę
- Dla każdej maszyny:
  - Nazwa
  - Prędkość (MB/min)
  - Dzienny limit (minuty)
  - Czas przezbrojenia (minuty)
  - Lista obsługiwanych `Twr_Profil` (multiselect z wartości z xlsx)
- Dane maszyn przechowywane w `backend/config.json` (pre-załadowane z maszyna-twr.xlsx)
- **Seed danych gotowy** — 51 maszyn: 21 Sękocin (prefix S), 30 Zabornia (prefix Z)
- User uzupełnia tylko: prędkość, limit dzienny, czas przezbrojenia per maszyna

### Konfigurator operatorów (UI) — co musi mieć
- Dodaj / edytuj / usuń operatora (imię/nazwisko lub identyfikator)
- Macierz kompetencji:
  - Wiersze = operatorzy
  - Kolumny = 14 kategorii `Twr_Profil_Glowny` z pliku skille.xlsx:
    AKCESORIA, ARKUSZE, BLACHODACHÓWKA, BTR SYSTEMS, KROP SYSTEM RYNNY UKRYTEJ,
    OGRODZENIA, OKOŁOPRODUKCYJNE, PANELE, PŁASKA NA WYMIAR, POZOSTALE,
    REVO-SIM, SUROWCE, TOWARY HANDLOWE, TRAPEZY
  - Komórka = checkbox (umie / nie umie)
- Przypisanie operatora do maszyny możliwe tylko jeśli ma kompetencje dla produktów tej maszyny
- Dane operatorów przechowywane razem z maszynami (JSON/SQLite)
