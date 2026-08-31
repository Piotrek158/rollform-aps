# Journal zmian

---

## 2026-04-25 — Widok zmian (zastąpienie DnD)

### Co zmieniono

W pliku `frontend/pages/5_Planner.py` usunięto sekcję **🔀 Zmień kolejność katalogów** opartą na `streamlit-sortables` (lista tekstowa do przeciągania, która wyglądała brzydko i była mało czytelna).

Zastąpiono ją sekcją **🏭 Widok zmian** — pionowym widokiem harmonogramu dziennego.

### Jak działa nowy widok

Renderowany jako czysty HTML przez `st.markdown(..., unsafe_allow_html=True)`.

**Struktura wizualna:**
- Oś czasu po lewej — godziny 6:00–22:00 co 1h, etykiety "I" i "II" przy środku każdej zmiany
- Każda maszyna = osobna kolumna (165px szerokości), nagłówek z nazwą maszyny
- Kolumny przewijalne poziomo gdy maszyn jest dużo

**Bloki produkcyjne:**
- Tło bloku = kolor RAL (z palety `RAL_HEX`)
- Kolor tekstu dobierany automatycznie: biały na ciemnym tle, ciemny na jasnym (luminancja)
- Filc = złota ramka 2px (`#C9A227`), identycznie jak w Gancie Plotly
- Przezbrojenie = różowe tło `#FFB3BA` z ikoną ⚙️
- Przejście między maszynami = pomarańczowe tło `#FFAA5A` z ikoną ➡
- Etykieta w bloku zależna od wysokości: >45px — nazwa katalogu + MB; >22px — RAL XXXX; mniejsze — brak tekstu
- Tooltip po najechaniu: numer ZP | katalog | MB

**Podział zmian:**
- I zmiana (6:00–14:00): lekkie niebieskie tło `rgba(160,190,255,0.10)`
- II zmiana (14:00–22:00): lekkie pomarańczowe tło `rgba(255,180,140,0.10)`
- Czerwona linia pozioma przy 14:00 (opacity 0.55) jako separator

**Skala:** 0.70px/minutę → łączna wysokość 672px (16h × 60min × 0.70)

### Funkcja pomocnicza

```python
def _is_dark(hex_color: str) -> bool:
    # luminancja BT.601 — decyduje o kolorze tekstu na tle RAL
    r, g, b = ...
    return (0.299*r + 0.587*g + 0.114*b) / 255 < 0.5
```

### Zależności

Usunięto zależność od pakietu `streamlit-sortables`. Nowy widok nie wymaga żadnych dodatkowych pakietów.

---

## Stan aplikacji na 2026-04-25

### Architektura

```
frontend/
  app.py                    # strona główna (nawigacja)
  pages/
    1_Import.py             # upload xlsx, wybór zakładu
    2_Maszyny.py            # konfiguracja maszyn + checkbox Przystawka Filc
    3_Operatorzy.py         # operatorzy, shift A/B, urlopy, macierz kompetencji
    4_Kalendarz.py          # kalendarz pracy per dzień
    5_Planner.py            # planowanie + Gantt + Widok zmian + podsumowanie + Excel
  utils.py                  # load/save config, load/save/overwrite plan

backend/
  main.py                   # FastAPI app
  config.json               # konfiguracja maszyn, operatorów, kalendarza
  plans/                    # zapisane plany JSON
  planning/
    engine.py               # silnik planowania V3
```

### Silnik planowania (`engine.py`) — kluczowe cechy

- Rotacja zmian A/B co tydzień (ref. `A_first_shift_week = "2026-04-27"`)
- Zmiana 1: 6:00–14:00, Zmiana 2: 14:00–22:00
- Auto-przypisanie operatorów do maszyn round-robin wg obciążenia
- Przejścia między maszynami: 20 min (blok "przejscie")
- Przezbrojenia: 0 min (ten sam katalog) / 6 min (D↔E, ta sama baza) / 12 min (inny katalog)
- `last_kat` przenosi się przez dni wolne — brak resetu w weekend
- Filc: `_has_filc(katalog)` — wykrywa "F" w bazie katalogu
- Przystawka Filc: `ma_przystawke_filcu` w config — jeśli True, zmiana z/na katalog F = 0 min
- Fallback maszynowy gdy brak operatorów

### UI Planner (`5_Planner.py`) — sekcje

1. **Nowe planowanie** — data, horyzont, lookahead → `🚀 Zaplanuj` → `save_plan()`
2. **Plan selector** — dropdown z historią planów JSON
3. **Filtry** — data + Twr_Profil_Glowny
4. **Gantt Plotly** — poziomy timeline, kolory RAL, Filc (jasniejszy + złota ramka), hover
5. **Legenda kolorów** — expandable, 5 kolumn
6. **Widok zmian** — pionowy widok dzienny per maszyna (nowy, zastąpił DnD)
7. **Podsumowanie dnia** — tabela: Maszyna, Liczba ZP, MB, Prod.(min), Setup(min), Przezbr., Razem(min), Wykorz.%, MB Filc
8. **Eksport Excel** — szczegóły ZP + podsumowanie, bez przezbrojeń/przejść

### Otwarte tematy

| Temat | Stan |
|-------|------|
| Spóźnienia (zonk) | Kolumna `spoznienie` w silniku i planie, nie wyświetlana w UI — uzgodniono: czerwona ramka w Gancie + kolumna w podsumowaniu |
| Metryki pilności | Koncepcja zaakceptowana: "katalogi na LD jutro" jako `st.metric`, tryb kolorowania RAL/Pilność, progi elastyczne (suwaki) |
| DnD reorder | Zastąpione Widokiem zmian — temat zamknięty |
