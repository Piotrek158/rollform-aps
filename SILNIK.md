# Silnik planowania — opis krok po kroku

> Dokument opisuje **tylko logikę planowania** (po imporcie i przypisaniu maszyn).
> Zakłada dane już załadowane do pamięci i przypisane do maszyn.

---

## Dane wejściowe silnika

| Dane | Źródło |
|------|--------|
| Lista zleceń (DataFrame) | import xlsx → już odfiltrowany po zakładzie |
| Konfiguracja maszyn | `config.json["machines"]` |
| Konfiguracja operatorów | `config.json["operators"]` (shift_group, maszyny) |
| Kalendarz pracy | `config.json["calendar"]` — minuty per dzień |
| Urlopy operatorów | `config.json["operator_vacations"]` |
| Konfiguracja zmian | `config.json["shift_config"]["A_first_shift_week"]` |
| Data startowa | wybór usera w UI |
| Horyzont planowania | N dni do przodu (wybór usera) |
| Okno lookahead | X dni (wybór usera) — ile dni do przodu zbieramy zlecenia na dany dzień |

---

## Architektura zmianowa

### Rotacja zmian
- **Zmiana 1 (ranna):** 6:00–14:00 (480 min)
- **Zmiana 2 (popołudniowa):** 14:00–22:00 (480 min)
- Operatorzy podzieleni na **grupę A** i **grupę B**
- Co tydzień grupy się zamieniają:
  - Tydzień parzysty od `A_first_shift_week`: A = zmiana 1, B = zmiana 2
  - Tydzień nieparzysty: A = zmiana 2, B = zmiana 1
- Parametr `A_first_shift_week = "2026-04-27"` (poniedziałek kiedy A jest na 1. zmianie)

```python
def get_shift(op, day, A_week_start):
    weeks = (day - A_week_start).days // 7
    a_on_first = (weeks % 2 == 0)
    return (1 if a_on_first else 2) if op["shift_group"] == "A" else (2 if a_on_first else 1)
```

### Konfiguracja operatora
```json
{
  "name": "Jan Kowalski",
  "shift_group": "A",
  "maszyny": ["S2-T20P", "S2-T18H"],
  "skills": {"TRAPEZY": true, ...}
}
```

### Urlopy
```json
"operator_vacations": {
  "Jan Kowalski": ["2026-05-12", "2026-05-15"]
}
```

---

## Nowa główna pętla planowania (operator-aware)

```
last_katalog_per_maszyna = {}   ← globalny, przenosi się przez dni i zmiany

dla każdego dnia w horyzoncie:
  cal_min = kalendarz[dzien]
  if cal_min == 0: skip (last_katalog NIE resetuje się)

  dla każdej zmiany (1, 2):
    if zmiana == 2 and cal_min < 960: skip  ← praca tylko na 1 zmianie

    dostepni = [op for op if shift(op,dzien)==zmiana and not urlop(op,dzien)]
    # max maszyn równoczesnych = len(dostepni)

    dla każdego operatora z dostepni:
      cursor = shift_start  ← minuty od północy (360 dla zm.1, 840 dla zm.2)
      prev_maszyna = None

      dla każdej maszyny z op["maszyny"]:
        if prev_maszyna is not None:
          emit przejście(20 min, cursor → cursor+20)  ← pomarańczowy blok
          cursor += 20

        if cursor >= shift_end: break

        window = shift_end - cursor
        cursor, last_kat = plan_window(maszyna, cursor, window, last_katalog_per_maszyna[maszyna])
        last_katalog_per_maszyna[maszyna] = last_kat
        prev_maszyna = maszyna
```

### plan_window (bez zmian w logice — tylko inny limit)
Zamiast `limit_min = min(cal, machine_limit)` używamy `limit_min = window` (czas operatora na tej maszynie).
Reszta algorytmu (katalogi, lookahead, przezbrojenia) identyczna jak poprzednio.

---

## Typy wierszy w Gantt

| kolor_id | Kolor | Opis |
|----------|-------|------|
| RAL kod (np. "8017") | kolor RAL | produkcja |
| `"przezbrojenie"` | różowy #FFB3BA | zmiana katalogu (12 lub 6 min) |
| `"przejscie"` | pomarańczowy #FFAA5A | operator przechodzi między maszynami (20 min) |

---

## Otwarte decyzje (do implementacji w przyszłości)

### Kluczowe kolumny zlecenia używane w silniku

| Kolumna xlsx | Rola w planowaniu |
|---|---|
| `Numer_zlecenia` | klucz główny zlecenia |
| `Ilosc_MBSZT` | MB zlecenia (float, po konwersji `","→"."`) |
| `LD` | termin realizacji (data) |
| `Twr_Katalog` | klucz grupowania — jednostka przezbrojenia |
| `Twr_Profil_opcje` | klasyfikacja Dach / Elewacja (puste = Dach) |
| `Twr_Flizelina` | czy produkt wymaga flizeliny (puste = nie) |
| `maszyna` | przypisana maszyna (wyliczona z Twr_Profil w kroku importu) |

---

## Koncepcja kluczowa: Twr_Katalog jako jednostka grupowania

Zlecenia są planowane **blokami** — wszystkie zlecenia z tym samym `Twr_Katalog`
na danej maszynie tworzą jeden blok produkcyjny.

Zmiana `Twr_Katalog` = przezbrojenie (koszt w minutach).

**Carryover między dniami**: `last_katalog` przenosi się z poprzedniego dnia.
Jeśli dzień N kończy się katalogiem X, a dzień N+1 zaczyna od X → brak przezbrojenia.

---

## Klasyfikacja Dach vs Elewacja

```
typ_zlecenia(zlecenie):
  if zlecenie["Twr_Profil_opcje"] == "" or null:
    return "Dach"
  return zlecenie["Twr_Profil_opcje"]   # "Dach" lub "Elewacja"
```

---

## Reguły przezbrojenia

| Przypadek | Czas (min) |
|-----------|-----------|
| `prev_katalog == curr_katalog` | **0** — brak przezbrojenia |
| Inne katalogi, ale ta sama "baza" (tylko D↔E) | **6** |
| Zupełnie inny katalog | **przezbrojenie maszyny** (domyślnie 12 min) |
| Pierwszy katalog dnia (`prev_katalog is None`) | **przezbrojenie maszyny** |

### Jak wykryć przypadek D↔E (6 min)

`Twr_Katalog` koduje na końcu typ: ostatnia litera `D` (Dach) lub `E` (Elewacja).
Przykłady: `T20PM8017AM50F125D` / `T20PM8017AM50F125E`.

```python
def katalog_base(kat: str) -> str:
    """Zwraca 'bazę' katalogu bez D/E na końcu."""
    if kat and kat[-1] in ("D", "E"):
        return kat[:-1]
    return kat

def katalog_typ(kat: str) -> str:
    if kat and kat[-1] in ("D", "E"):
        return kat[-1]
    return "D"  # domyślnie Dach

def czas_przezbrojenia(prev_kat, curr_kat, maszyna_przezb_min) -> float:
    if prev_kat is None:
        return maszyna_przezb_min
    if prev_kat == curr_kat:
        return 0.0
    if katalog_base(prev_kat) == katalog_base(curr_kat) and \
       katalog_typ(prev_kat) != katalog_typ(curr_kat):
        return 6.0   # D ↔ E, ta sama maszyna + kolor + grubość
    return float(maszyna_przezb_min)
```

---

## Flizelina — logika obecna i przyszła

### TERAZ (bez przystawki)
- `Twr_Flizelina` niepuste → `Twr_Katalog` zawiera `F` w kodzie (np. `T20PM8017AM50F125D`)
- Katalog z F i bez F traktujemy jako **różne grupy** → standardowe przezbrojenie między nimi
- Żadnych dodatkowych obliczeń — `Twr_Katalog` jest już poprawny w danych

### PRZYSZŁOŚĆ — checkbox `ma_przystawke` przy maszynie
Gdy maszyna ma przystawkę flizeliny, samodzielnie dokłada F do produktu.
Oznacza to: wersja bez F i wersja z F są FAKTYCZNIE tym samym produktem → brak przezbrojenia.

```python
# Przyszła logika (nie implementujemy teraz):
def katalog_base_flizelina(kat: str, ma_przystawke: bool) -> str:
    base = katalog_base(kat)  # usuwa D/E
    if ma_przystawke:
        base = base.replace("F", "")  # ignoruj flizelina w porównaniu
    return base
```

**Flagę `ma_przystawke` dodać do konfiguracji maszyny w UI (checkbox) — w kolejnej iteracji.**

---

## Główna pętla planowania

```python
def planuj(
    zlecenia: pd.DataFrame,
    maszyny: list[dict],
    kalendarz: dict,          # {"2026-04-28": 960, ...}
    data_start: date,
    horyzont: int,            # liczba dni planowania
    lookahead: int,           # liczba dni okna zbierania zleceń
) -> pd.DataFrame:

    wynik = []

    for maszyna in maszyny:
        last_katalog = None   # carryover między dniami

        zlecenia_masz = zlecenia[zlecenia["maszyna"] == maszyna["name"]].copy()
        planned = set()       # Numer_zlecenia już zaplanowanych

        for offset in range(horyzont):
            dzien = data_start + timedelta(days=offset)
            limit_min = kalendarz.get(dzien.isoformat(), 0)

            if limit_min == 0:
                continue      # dzień wolny — pomiń, last_katalog NIE resetuje się

            minuty_dnia = 0.0

            # --- Okno lookahead: zbierz unikalne katalogi ---
            # Daty okna: dzien .. dzien+lookahead (bez dni wolnych nie ma znaczenia — zbieramy po LD)
            data_max_okna = dzien + timedelta(days=lookahead)

            kandydaci = zlecenia_masz[
                (zlecenia_masz["LD"] <= data_max_okna) &
                (~zlecenia_masz["Numer_zlecenia"].isin(planned))
            ]

            # Unikalne katalogi, posortowane po najwcześniejszym LD w grupie
            min_ld_per_kat = (
                kandydaci.groupby("Twr_Katalog")["LD"].min()
                         .sort_values()
            )
            katalogi_dnia = min_ld_per_kat.index.tolist()

            # --- Planuj blok po bloku ---
            for katalog in katalogi_dnia:

                przezb = czas_przezbrojenia(last_katalog, katalog, maszyna["przezbrojenie"])

                # Sprawdź czy przezbrojenie mieści się w limicie
                if minuty_dnia + przezb >= limit_min:
                    break     # limit osiągnięty, koniec dnia

                # Zlecenia z tego katalogu, posortowane po LD rosnąco
                blok = kandydaci[
                    (kandydaci["Twr_Katalog"] == katalog) &
                    (~kandydaci["Numer_zlecenia"].isin(planned))
                ].sort_values("LD")

                przezb_dodany = False

                for _, zp in blok.iterrows():
                    czas_zp = zp["Ilosc_MBSZT"] / maszyna["predkosc"]

                    koszt = czas_zp + (przezb if not przezb_dodany else 0)

                    if minuty_dnia + koszt > limit_min:
                        break   # to zlecenie nie mieści się — przejdź do kolejnego katalogu

                    if not przezb_dodany:
                        minuty_dnia += przezb
                        przezb_dodany = True

                    minuty_dnia += czas_zp
                    last_katalog = katalog
                    planned.add(zp["Numer_zlecenia"])

                    wynik.append({
                        "Numer_zlecenia":   zp["Numer_zlecenia"],
                        "maszyna":          maszyna["name"],
                        "dzien_planu":      dzien.isoformat(),
                        "Twr_Katalog":      katalog,
                        "typ":              typ_zlecenia(zp),     # Dach / Elewacja
                        "MB":               zp["Ilosc_MBSZT"],
                        "prod_min":         round(czas_zp, 2),
                        "przezb_min":       round(przezb if przezb_dodany and minuty_dnia == czas_zp + przezb else 0, 2),
                        "LD":               zp["LD"].isoformat(),
                        "spoznienie":       dzien > zp["LD"],     # True = spóźnione
                    })

    return pd.DataFrame(wynik)
```

---

## Obsługa dni wolnych a carryover

`last_katalog` **NIE resetuje się** przy dniu wolnym.

Przykład:
- Piątek kończy katalog `T20PM8017D`
- Sobota/niedziela = wolne (limit = 0) → pomijamy bez resetu
- Poniedziałek: first katalog = `T20PM8017D` → **0 min przezbrojenia** ✓

---

## Wynik planowania — kolumny DataFrame

| Kolumna | Typ | Opis |
|---------|-----|------|
| `Numer_zlecenia` | str | klucz zlecenia |
| `maszyna` | str | nazwa maszyny |
| `dzien_planu` | str (ISO) | data produkcji |
| `Twr_Katalog` | str | katalog (jednostka grupowania) |
| `typ` | str | Dach / Elewacja |
| `MB` | float | ilość w metrach bieżących |
| `prod_min` | float | czas produkcji (MB / prędkość) |
| `przezb_min` | float | czas przezbrojenia (0 lub 6 lub przezbrojenie maszyny) |
| `LD` | str (ISO) | termin realizacji |
| `spoznienie` | bool | czy dzień planu > LD |

---

## Wyjątki do obsługi

| ID | Sytuacja | Zachowanie |
|----|----------|------------|
| W1 | Zlecenie nie mieści się w pełnym dniu (MB/v > limit całego dnia) | Planuj jako jedyne zlecenie dnia + ostrzeżenie w logu |
| W2 | Twr_Profil bez przypisanej maszyny | Lista "nieprzypisane", ostrzeżenie w UI |
| W3 | Twr_Profil w >1 maszynie w zakładzie | Blokada planowania, błąd konfiguracji |
| W4 | Zlecenie poza horyzontem (LD > data_start + horyzont) | Lista "poza horyzontem", ostrzeżenie |
| W5 | LD w przeszłości | Planuj normalnie (wchodzi do okna pierwszego dnia) |
| W6 | Ilosc_MBSZT = 0 lub null | Pomiń, zaloguj |

---

## Otwarte decyzje (do implementacji w przyszłości)

| Temat | Stan |
|-------|------|
| Checkbox `ma_przystawke` przy maszynie (flizelina) | **NIE** — kolejna iteracja |
| Operatorzy jako warunek blokujący planowanie | **NIE** — kolejna iteracja |
| Kalendarz per maszyna (różne godziny) | **NIE** — na razie globalny |
| Priorytety (a/b/C/X jak w starym VBA) | **NIE** — planowanie tylko po LD |
