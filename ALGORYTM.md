# Opis algorytmu planowania produkcji

## Dane wejściowe

- Plik xlsx z zleceniami
- Wybrany zakład: Zabornia lub Sękocin
  - Zakład rozpoznawany z kolumny `Nazwa_zakladu_Grupa`
  - Maszyny zakładu: prefix `S` = Sękocin (21 maszyn), prefix `Z` = Zabornia (30 maszyn)
- Konfiguracja maszyn (z `backend/config.json`, uzupełniana przez usera w UI):
  - Nazwa maszyny (pre-załadowana z maszyna-twr.xlsx)
  - Lista `Twr_Profil` (pre-załadowana — np. S2-T20P obsługuje: TRAPEZ T20 PLUS, POZOSTALE, SUROWCE)
  - Prędkość (MB/min) — uzupełnia user
  - Dzienny limit (minuty) — uzupełnia user
  - Czas przezbrojenia (minuty) — uzupełnia user
- Konfiguracja operatorów (macierz kompetencji wg 14 kategorii `Twr_Profil_Glowny`)
- Data startowa planowania
- Horyzont planowania (liczba dni do przodu — definiowany przez usera)
- Okno lookahead (X dni — definiowane przez usera, ile dni do przodu zbieramy zlecenia na dany dzień)

---

## Krok 1 — Import i czyszczenie danych

1. Wczytaj xlsx
2. Filtruj wiersze po `Nazwa_zakladu_Grupa` == wybrany zakład
3. Konwertuj `Ilosc_MBSZT` z tekstu `"43,2"` → float (zamień przecinek na kropkę)
4. Parsuj `LD` jako datę
5. Pomiń wiersze gdzie `Ilosc_MBSZT` == 0 lub null
6. Kolumna `Zrealizowane` — ignorujemy, planujemy wszystkie zlecenia

**Wyjątek W1:** `Ilosc_MBSZT` nie da się skonwertować → pomiń wiersz, zaloguj błąd i pokaż użytkownikowi

---

## Krok 2 — Przypisanie maszyn do zleceń

Dla każdego zlecenia:
- Sprawdź jego `Twr_Profil`
- Znajdź maszynę, która obsługuje ten `Twr_Profil`
- Przypisz maszynę do zlecenia

**Wyjątek W2:** `Twr_Profil` nie przypisany do żadnej maszyny w wybranym zakładzie → zlecenie trafia do listy "nieprzypisane", nie jest planowane, pokazujemy ostrzeżenie

**Wyjątek W3:** `Twr_Profil` przypisany do więcej niż jednej maszyny w wybranym zakładzie → blokada planowania, błąd konfiguracji, pokazujemy komunikat

**Uwaga:** `Twr_Profil` = "POZOSTALE" i "SUROWCE" są przypisane do wielu maszyn — to normalne (catch-all). Jeśli zlecenie ma taki `Twr_Profil`, traktujemy je jako W3 i pokazujemy ostrzeżenie że wymaga ręcznego przypisania.

---

## Krok 3 — Główna pętla planowania

### Struktura pętli

```
dla każdej maszyny w zakładzie:

  ostatni_katalog = None   ← przenosi się między dniami!

  dla każdego dnia roboczego (od data_startowa, pomiń sob/nd):

    # --- Buduj listę katalogów na ten dzień ---
    okno_dat = [dzien, dzien+1, ..., dzien+X]  ← X = lookahead user'a
    (pomijamy sob/nd w oknie)

    unikalne_katalogi = unikalne Twr_Katalog ze zleceń gdzie:
      - zlecenie należy do tej maszyny
      - LD <= ostatni dzień okna
      - zlecenie jeszcze nie zaplanowane

    # --- Planuj zlecenia pogrupowane po katalogu ---
    minuty_dnia = 0

    dla każdego katalogu z unikalne_katalogi:

      czy_przezbrojenie = (katalog != ostatni_katalog)

      dla każdego zlecenia z tym katalogiem (wg LD rosnąco):
        jeśli zlecenie już zaplanowane → pomiń

        czas = Ilosc_MBSZT / predkosc_maszyny

        if czy_przezbrojenie:
          czas += czas_przezbrojenia
          czy_przezbrojenie = False   ← setup tylko raz na katalog per dzień

        if minuty_dnia + czas <= limit_dzienny:
          przypisz zlecenie do tego dnia
          minuty_dnia += czas
          ostatni_katalog = katalog   ← aktualizuj dla następnego dnia

        else:
          pomiń (następny dzień)
          BREAK wewnętrznej pętli → przejdź do kolejnego katalogu
          (nie ma sensu próbować kolejnych zleceń z tego katalogu jeśli limit osiągnięty)
```

### Przykład działania (lookahead = 3 dni)

Planujemy dzień **10.05** (wtorek):
- Zbieramy unikalne `Twr_Katalog` z zleceń z `LD` w dniach 10.05, 11.05, 12.05, 13.05
- Zakładając że dostajemy katalogi: ["8017 RAL 50", "7016 MAT AM 50", "8019 MAT IN 50"]
- Planujemy wszystkie zlecenia z "8017 RAL 50" (pierwsze przezbrojenie jeśli inny niż wczorajszy)
- Potem wszystkie z "7016 MAT AM 50" (przezbrojenie)
- Potem "8019 MAT IN 50" — jeśli starczyło minut
- Koniec dnia: `ostatni_katalog = "8019 MAT IN 50"`

Planujemy dzień **11.05** (środa):
- Zbieramy katalogi z LD w dniach 11.05, 12.05, 13.05, 14.05
- Pierwsze zlecenie z "8019 MAT IN 50" → **brak przezbrojenia** (poprzedni dzień kończył tym samym)
- Itd.

**Weekendy:** 10.05 to środa — ale jeśli dojdziemy do soboty/niedzieli → pomijamy dzień, nie planujemy

---

## Krok 4 — Wynik planowania

Dla każdego zaplanowanego zlecenia:
- `dzien_planu` — data przypisanego dnia produkcji
- `czas_produkcji_min` — Ilosc_MBSZT / prędkość
- `czas_przezbrojenia_min` — czas_przezbrojenia lub 0
- `przekroczone_LD` — czy `dzien_planu > LD` (spóźnienie)

Podsumowanie per maszyna per dzień:
- Suma MB
- Suma minut (produkcja + przezbrojenia)
- Liczba zleceń
- Liczba przezbrojeń

---

## Wyjątki — zestawienie

| ID | Sytuacja | Zachowanie |
|----|----------|------------|
| W1 | Błędny format Ilosc_MBSZT | Pomiń wiersz, pokaż błąd |
| W2 | Twr_Profil bez maszyny | Lista "nieprzypisane", ostrzeżenie |
| W3 | Twr_Profil w 2 maszynach | Blokada planowania, błąd konfiguracji |
| W4 | Zlecenie > limit dzienny | Planuj jako jedyne w dniu + ostrzeżenie |
| W5 | Zlecenie poza horyzontem | Lista "poza horyzontem", ostrzeżenie |
| W6 | LD w przeszłości | Planuj normalnie (wejdzie do okna pierwszego dnia) |

---

## Założenia zatwierdzone

| ID | Założenie | Status |
|----|-----------|--------|
| A | Sortowanie: outer=Twr_Katalog, inner=LD rosnąco | zatwierdzone |
| B | Sobota i niedziela = dni wolne (w przyszłości zmiennie) | zatwierdzone |
| C | Limit dzienny w minutach | zatwierdzone |
| D | Przezbrojenie się przenosi między dniami (brak przezbrojenia jeśli ten sam katalog co ostatni wczoraj) | zatwierdzone |
| E | Zlecenia "Zrealizowane" planujemy normalnie | zatwierdzone |

---

## Otwarte pytania (do odpowiedzi przed implementacją)

*Brak — wszystko potwierdzone. Gotowe do implementacji.*

---

## Carryover katalogu między dniami — potwierdzone

Jeśli limit dnia zostanie osiągnięty w połowie katalogu (np. zaplanowano 3 z 5 zleceń "8017 RAL 50"), następnego dnia pozostałe 2 zlecenia z tego katalogu **nie dostają przezbrojenia** — `ostatni_katalog` przenosi się z poprzedniego dnia. To identyczne zachowanie jak w oryginalnym algorytmie VBA.

---

## Operatorzy — wymaganie UI (do implementacji po maszynach)

### Macierz kompetencji operatorów
- Wiersze = operatorzy (lista dodawana w UI)
- Kolumny = kategorie produktów wg `Twr_Profil_Glowny` (lista do przekazania przez użytkownika: Trapezy, Dachówki, itd.)
- Komórka = checkbox: czy operator umie obsługiwać dany typ produktu

### Co robi macierz
- Operator zaznaczony jako "umie Trapezy" może być przypisany do maszyny obsługującej Trapezy
- Jeśli maszyna obsługuje `Twr_Profil` należące do "Trapezów" a operator nie ma tej kompetencji → nie można go przypisać

### Co NIE jest jeszcze implementowane (kolejna iteracja)
- Dodatkowe ograniczenia powiązane z operatorem (np. dostępność, zmiany)
- Operator jako warunek blokujący planowanie konkretnego dnia
