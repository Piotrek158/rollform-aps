"""
Silnik planowania produkcji — V4 (must_go + zonk + rolling)

Logika:
- must_go = zaległości (LD < D) + krytyczne (D ≤ LD ≤ NWD(D)).
  Mogą wystawać poza shift_end → zonk.
- batch z lookahead = zlecenia z tym samym katalogiem co must_go,
  z LD ≤ D + lookahead (max batch, mniej przezbrojeń).
- rolling = must_go z dnia jutrzejszego (NWD < LD ≤ NWD(NWD+1))
  na tej samej maszynie, jeśli zostały minuty po must_go.
  Nie pozwala na zonk.
"""
from __future__ import annotations

import re
from datetime import date, datetime, timedelta

import pandas as pd


# ── Stałe ─────────────────────────────────────────────────────────────────────

SHIFT_1 = (6 * 60, 14 * 60)    # minuty od północy
SHIFT_2 = (14 * 60, 22 * 60)
TRANSIT_MIN = 20.0
TRANSIT_COLOR = "przejscie"
SETUP_COLOR   = "przezbrojenie"
CRANE_CONFLICT_COLOR = "konflikt_suwnicy"

# Suwnice dzielone w grupie produktów: jedna fizyczna suwnica per zakład per
# kategoria główna. Setup 12-min wymaga suwnicy (zmiana coilu); 6-min D↔E to
# tylko zmiana koloru — bez suwnicy. Inne grupy nie mają tego ograniczenia.
CRANE_GROUPS = {"TRAPEZY", "BLACHODACHÓWKA", "PANELE"}
CRANE_NEEDED_MIN = 12.0


def _crane_key(machine: dict, machine_cats: set[str]) -> tuple[str, str] | None:
    """Klucz suwnicy `(zaklad, kategoria_głowna)` lub None gdy maszyna nie należy
    do grupy z dzieloną suwnicą. Bierze pierwszą z `machine_cats` należącą do
    `CRANE_GROUPS` (maszyna może mieć też kategorie spoza grup z suwnicą,
    np. TOWARY HANDLOWE — ignorujemy je)."""
    zaklad = machine.get("zaklad", "")
    for c in machine_cats:
        if c in CRANE_GROUPS:
            return (zaklad, c)
    return None


def _crane_first_free(
    crane_cal: dict[tuple[str, str, str], list[tuple[float, float]]],
    key: tuple[str, str],
    day: date,
    t: float,
    dur: float,
) -> float:
    """Najwcześniejszy moment ≥ `t` gdy suwnica nie jest zajęta — koniec
    łańcucha rezerwacji trwających w `t`. Kolejka FIFO: nie szukamy dziury
    o długości `dur`; wystarczy wolna chwila — wchodzimy z przezbrojeniem,
    a ewentualna późniejsza rezerwacja innej maszyny w praktyce przesunie
    się o kilka minut (świadome dopuszczenie nakładek w modelu planu).
    Maks. czekanie ≈ (pozycja w kolejce) × czas przezbrojenia."""
    busy = sorted(crane_cal.get((key[0], key[1], day.isoformat()), []))
    cand = float(t)
    for bs, be in busy:
        if be <= cand:
            continue
        if bs <= cand:
            cand = float(be)
        else:
            break  # suwnica wolna w `cand` — wchodzimy, nawet gdy dziura < dur
    return cand


def _crane_reserve(
    crane_cal: dict[tuple[str, str, str], list[tuple[float, float]]],
    key: tuple[str, str],
    day: date,
    start: float,
    dur: float,
) -> None:
    crane_cal.setdefault((key[0], key[1], day.isoformat()), []).append(
        (float(start), float(start + dur))
    )


# ── Helpers katalog ───────────────────────────────────────────────────────────

def _katalog_base(kat: str) -> str:
    return kat[:-1] if kat and kat[-1] in ("D", "E") else kat

def _katalog_typ(kat: str) -> str:
    return kat[-1] if kat and kat[-1] in ("D", "E") else "D"

def _has_filc(katalog: str) -> bool:
    return "F" in _katalog_base(katalog)

def _oblicz_przezbrojenie(
    prev: str | None, curr: str, std_min: float, ma_przystawke: bool = False
) -> float:
    if prev is None: return std_min
    if prev == curr: return 0.0
    prev_cmp = _katalog_base(prev).replace("F", "") if ma_przystawke else _katalog_base(prev)
    curr_cmp = _katalog_base(curr).replace("F", "") if ma_przystawke else _katalog_base(curr)
    if prev_cmp == curr_cmp:
        return 6.0 if _katalog_typ(prev) != _katalog_typ(curr) else 0.0
    return float(std_min)

def _extract_ral(katalog: str) -> str:
    m = re.search(r"\d{4}", katalog)
    return m.group() if m else ""


# ── Helpers zmiana / operator ─────────────────────────────────────────────────

def _norm_zaklad(s: str) -> str:
    return (s.lower()
            .replace("ę","e").replace("ó","o").replace("ś","s")
            .replace("ą","a").replace("ź","z").replace("ż","z")
            .replace("ń","n").replace("ć","c").replace("ł","l").strip())

def get_operator_shift(op: dict, day: date, A_week_start: date) -> int:
    """Zwraca 1 lub 2 (numer zmiany) dla operatora w danym dniu."""
    weeks = (day - A_week_start).days // 7
    a_on_first = (weeks % 2 == 0)
    return (1 if a_on_first else 2) if op.get("shift_group","A") == "A" else (2 if a_on_first else 1)

def _is_vacation(name: str, day: date, vacations: dict) -> bool:
    return day.isoformat() in vacations.get(name, [])


# ── Kalendarz: następny dzień roboczy ─────────────────────────────────────────

def _next_working_day(day: date, kalendarz: dict[str, int], max_search: int = 30) -> date:
    """Pierwszy dzień > `day` gdzie cal_min > 0. Fallback: day+1 jeśli nie znajdzie."""
    for i in range(1, max_search + 1):
        nd = day + timedelta(days=i)
        if float(kalendarz.get(nd.isoformat(), 0)) > 0:
            return nd
    return day + timedelta(days=1)


# ── Obciążenie maszyn ─────────────────────────────────────────────────────────

def _machine_workload(
    planowalne: pd.DataFrame,
    maszyny_dict: dict,
    planned: set[str],
) -> dict[str, float]:
    """Zwraca {maszyna: szacowane_minuty_pracy} dla wszystkich niezaplanowanych ZP."""
    result = {}
    for nazwa, m in maszyny_dict.items():
        v = float(m.get("predkosc_mb_min", 0) or 0)
        if v <= 0:
            result[nazwa] = 0.0
            continue
        kand = planowalne[
            (planowalne["maszyna"] == nazwa) &
            (~planowalne["Numer_zlecenia"].isin(planned))
        ]
        result[nazwa] = float(kand["Ilosc_MBSZT_num"].sum()) / v if not kand.empty else 0.0
    return result


def _operator_can_handle(op: dict, machine: dict, machine_cats: set[str]) -> bool:
    """Operator obsługuje maszynę jeśli ma włączony skill na DOWOLNĄ
    kategorię główną (`Twr_Profil_Glowny`) tej maszyny.

    `machine_cats` to zbiór kategorii głównych derywowany z danych zleceń
    (mapa profil_szczegółowy → Twr_Profil_Glowny). Jeśli pusty (np. brak
    danych do zbudowania mapy), spada do bezpośredniego porównania ze
    `twr_profil` maszyny — bezpieczny fallback dla legacy configów.
    """
    skills = op.get("skills", {}) or {}
    if machine_cats:
        return any(skills.get(c, False) for c in machine_cats)
    return any(skills.get(tp, False) for tp in machine.get("twr_profil", []) or [])


def _build_machine_categories(
    df: pd.DataFrame,
    maszyny_dict: dict[str, dict],
) -> dict[str, set[str]]:
    """Mapa {nazwa_maszyny: {kategorie_glowne}} z danych zleceń.

    Profil szczegółowy (np. 'KINGAS ECO PLUS [35]') jest mapowany na kategorię
    główną (np. 'BLACHODACHÓWKA') przez kolumnę Twr_Profil_Glowny w df.
    Maszyny używają twr_profil = lista profili szczegółowych.
    """
    profile_to_kat: dict[str, str] = {}
    if "Twr_Profil" in df.columns and "Twr_Profil_Glowny" in df.columns:
        sub = df.dropna(subset=["Twr_Profil", "Twr_Profil_Glowny"])
        if not sub.empty:
            profile_to_kat = (
                sub.groupby("Twr_Profil")["Twr_Profil_Glowny"]
                .first()
                .to_dict()
            )
    out: dict[str, set[str]] = {}
    for nazwa, m in maszyny_dict.items():
        cats = set()
        for tp in m.get("twr_profil", []) or []:
            kat = profile_to_kat.get(tp)
            if kat:
                cats.add(str(kat))
        out[nazwa] = cats
    return out


def _auto_assign(
    available_ops: list[dict],
    machines_by_load: list[str],
    maszyny_dict: dict[str, dict],
    machine_categories: dict[str, set[str]],
) -> dict[str, list[str]]:
    """
    Przypisuje maszyny do operatorów respektując kompetencje (kategoria główna
    maszyny musi być włączona w skillach operatora). Wśród uprawnionych wybiera
    najmniej obciążonego (load balance), remisy rozstrzyga oryginalną kolejnością.

    Maszyna, do której żaden dostępny operator nie ma kompetencji, jest pomijana.
    """
    out: dict[str, list[str]] = {op["name"]: [] for op in available_ops}
    op_idx = {op["name"]: i for i, op in enumerate(available_ops)}
    for masz in machines_by_load:
        machine = maszyny_dict.get(masz)
        if machine is None:
            continue
        cats = machine_categories.get(masz, set())
        eligible = [op for op in available_ops if _operator_can_handle(op, machine, cats)]
        if not eligible:
            continue
        chosen = min(eligible, key=lambda o: (len(out[o["name"]]), op_idx[o["name"]]))
        out[chosen["name"]].append(masz)
    return out


# ── Przypisanie maszyn do zleceń ──────────────────────────────────────────────

def przypisz_maszyny(
    df: pd.DataFrame,
    maszyny: list[dict],
    zaklad: str,
) -> tuple[pd.DataFrame, list[str], dict[str, list[str]]]:
    profil_to_maszyna: dict[str, str] = {}
    konflikty: dict[str, list[str]] = {}
    zaklad_n = _norm_zaklad(zaklad)

    for m in maszyny:
        if _norm_zaklad(m.get("zaklad","")) != zaklad_n:
            continue
        nazwa = m.get("nazwa", m.get("id",""))
        for p in m.get("twr_profil", []):
            if p in konflikty:
                konflikty[p].append(nazwa)
            elif p in profil_to_maszyna:
                konflikty[p] = [profil_to_maszyna.pop(p), nazwa]
            else:
                profil_to_maszyna[p] = nazwa

    df_out = df.copy()
    df_out["maszyna"] = df_out["Twr_Profil"].map(profil_to_maszyna)
    nieprzypisane = df_out[df_out["maszyna"].isna()]["Twr_Profil"].dropna().unique().tolist()
    dane_profile  = set(df["Twr_Profil"].dropna().unique())
    konflikty_ist = {p: ms for p, ms in konflikty.items() if p in dane_profile}
    return df_out, nieprzypisane, konflikty_ist


# ── Okno czasowe: plan_window ─────────────────────────────────────────────────

def _plan_window(
    kand_masz: pd.DataFrame,
    machine: dict,
    day: date,
    day_dt: datetime,           # midnight tego dnia
    cursor_min: float,          # start (minuty od północy)
    window_end_min: float,      # koniec zmiany / dnia
    nwd: date,                  # next working day — must_go bound
    nwd_next: date,             # next NWD po nwd — rolling bound
    lookahead_end: date,        # data graniczna do brania zleceń (max batch)
    last_kat: str | None,
    planned: set[str],
    gantt_rows: list[dict],
    partial_mb_used: dict[str, float],  # {zp_num: mb_już_zaplanowane} — dla wew częściowych
    operator_name: str | None = None,   # operator obsługujący maszynę (None w trybie fallback)
    crane_calendar: dict | None = None, # globalny rejestr zajętości suwnic (mut.)
    crane_key: tuple[str, str] | None = None,  # klucz suwnicy maszyny lub None
    ma_przystawke: bool = False,
    allow_overflow: bool = False,  # True tylko w ostatniej zmianie dnia → wystawanie = zonk
) -> tuple[float, str | None, float]:
    """
    Planuje 2-fazowo:
    - must_go: katalogi gdzie min(LD) ≤ nwd. Jeśli allow_overflow=True,
      mogą wystawać poza window_end (= zonk). Inaczej: zatrzymują się na window_end
      i niezaplanowane przechodzą do następnej zmiany.
    - rolling: katalogi gdzie nwd < min(LD) ≤ nwd_next. Zawsze zatrzymują się
      na window_end (rolling nie generuje zonka).

    Zwraca (nowy_cursor, last_kat, zonk_min).
    """
    predkosc   = float(machine.get("predkosc_mb_min", 0) or 0)
    std_przezb = float(machine.get("czas_przezbrojenia_min", 12) or 12)
    nazwa      = machine.get("nazwa", machine.get("id",""))

    if predkosc <= 0:
        return cursor_min, last_kat, 0.0

    # Wszystkie niezaplanowane ZP tej maszyny
    kand = kand_masz[~kand_masz["Numer_zlecenia"].isin(planned)]

    if kand.empty:
        return cursor_min, last_kat, 0.0

    # Podział wew/zew. must_go i rolling biorą tylko zew — wew nie może
    # opóźnić innych zamówień, więc dochodzi dopiero w fazie 3 (wew_fill).
    if "Typ_zamowienia" in kand.columns:
        kand_zew = kand[kand["Typ_zamowienia"] != "wew"]
        kand_wew = kand[kand["Typ_zamowienia"] == "wew"]
    else:
        kand_zew = kand
        kand_wew = kand.iloc[0:0]

    # must_go: pilne (LD ≤ nwd) — mogą generować zonk
    # rolling: pozostałe, posortowane po LD (najpilniejsze pierwsze) — bez zonka
    if kand_zew.empty:
        must_go_kat: list[str] = []
        rolling_kat: list[str] = []
    else:
        min_ld = kand_zew.groupby("Twr_Katalog")["LD_date"].min().sort_values()
        must_go_kat = [k for k, ld in min_ld.items() if ld.date() <= nwd]
        rolling_kat  = [k for k, ld in min_ld.items() if ld.date() > nwd]

    cursor    = cursor_min
    zonk_min  = 0.0

    def _emit_setup(katalog: str, przezb: float):
        nonlocal cursor, last_kat, zonk_min
        if przezb <= 0:
            return
        # Rezerwacja suwnicy dla setupów ≥ CRANE_NEEDED_MIN. Jeśli zajęta —
        # maszyna stoi idle (cursor przesuwa się do najbliższego wolnego slotu).
        # Mid-day konflikt (last_kat != None) → wstaw czerwony pasek "konflikt_suwnicy".
        # Pre-shift kolejkowanie (last_kat == None) → ignoruj, to natural cost.
        if (
            crane_calendar is not None and crane_key is not None
            and przezb >= CRANE_NEEDED_MIN
        ):
            new_start = _crane_first_free(crane_calendar, crane_key, day, cursor, przezb)
            if new_start > cursor:
                if last_kat is not None:
                    cs = day_dt + timedelta(minutes=cursor)
                    ce = day_dt + timedelta(minutes=new_start)
                    gantt_rows.append({
                        "Maszyna": nazwa, "Twr_Profil_Glowny": "",
                        "Data": day.isoformat(), "Start": cs, "Koniec": ce,
                        "ZP": "— Konflikt suwnicy —", "Twr_Katalog": "",
                        "RAL": "",
                        "MB": 0.0, "Minuty": round(new_start - cursor, 1),
                        "LD": "—", "Total_kat_min": 0.0, "Total_kat_MB": 0.0,
                        "kolor_id": CRANE_CONFLICT_COLOR,
                        "is_zonk": False, "spoznienie": False, "is_wew": False,
                        "Operator": operator_name or "",
                    })
                cursor = new_start
            _crane_reserve(crane_calendar, crane_key, day, cursor, przezb)
        ps = day_dt + timedelta(minutes=cursor)
        pe = day_dt + timedelta(minutes=cursor + przezb)
        zonk_overlap = max(0.0, (cursor + przezb) - window_end_min) - max(0.0, cursor - window_end_min)
        is_zonk = zonk_overlap > 0
        gantt_rows.append({
            "Maszyna": nazwa, "Twr_Profil_Glowny": "",
            "Data": day.isoformat(), "Start": ps, "Koniec": pe,
            "ZP": "— Przezbrojenie —", "Twr_Katalog": katalog,
            "RAL": _extract_ral(katalog),
            "MB": 0.0, "Minuty": round(przezb, 1),
            "LD": "—", "Total_kat_min": 0.0, "Total_kat_MB": 0.0,
            "kolor_id": SETUP_COLOR, "is_zonk": bool(is_zonk),
            "is_wew": False,
            "Operator": operator_name or "",
        })
        cursor   += przezb
        zonk_min += zonk_overlap
        last_kat  = katalog

    def _emit_zp(
        zp_row, katalog: str, ral: str, kat_total: float, kat_total_mb: float,
        mb_override: float | None = None, is_wew: bool = False, is_partial: bool = False,
    ):
        nonlocal cursor, last_kat, zonk_min
        mb      = float(mb_override) if mb_override is not None else float(zp_row["Ilosc_MBSZT_num"])
        czas_zp = mb / predkosc
        zs = day_dt + timedelta(minutes=cursor)
        ze = day_dt + timedelta(minutes=cursor + czas_zp)
        zonk_overlap = max(0.0, (cursor + czas_zp) - window_end_min) - max(0.0, cursor - window_end_min)
        ld_date = zp_row["LD_date"].date()
        pg = (
            str(zp_row["Twr_Profil_Glowny"])
            if "Twr_Profil_Glowny" in zp_row.index and pd.notna(zp_row["Twr_Profil_Glowny"])
            else ""
        )
        zp_label = str(zp_row["Numer_zlecenia"]) + (" (część)" if is_partial else "")
        gantt_rows.append({
            "Maszyna": nazwa, "Twr_Profil_Glowny": pg,
            "Data": day.isoformat(), "Start": zs, "Koniec": ze,
            "ZP": zp_label,
            "Twr_Katalog": katalog, "RAL": ral,
            "MB": round(mb, 3), "Minuty": round(czas_zp, 2),
            "LD": ld_date.isoformat(),
            "Total_kat_min": kat_total,
            "Total_kat_MB": round(float(kat_total_mb), 1),
            "kolor_id": (ral + "_F") if _has_filc(katalog) else ral,
            "is_zonk": bool(zonk_overlap > 0),
            "spoznienie": day > ld_date,
            "is_wew": bool(is_wew),
            "Operator": operator_name or "",
        })
        cursor   += czas_zp
        zonk_min += zonk_overlap
        last_kat  = katalog
        if not is_partial:
            planned.add(str(zp_row["Numer_zlecenia"]))

    # ── Faza 1: must_go (tylko zew) ───────────────────────────────────────────
    # allow_overflow=True (ostatnia zmiana dnia) → wystawanie poza window_end = zonk.
    # allow_overflow=False (np. zm.1 z planowaną zm.2) → zatrzymujemy się na window_end,
    # niezaplanowane must_go zostają w `planowalne` i obsłuży je zm.2.
    # Wybór katalogu jest dynamiczny: minimalizujemy opóźnienie suwnicy
    # (kandydat nie kolidujący z innymi maszynami w grupie ma priorytet),
    # remisy rozstrzyga LD.
    def _katalog_score(k: str) -> tuple[float, pd.Timestamp]:
        przezb = _oblicz_przezbrojenie(last_kat, k, std_przezb, ma_przystawke)
        delay = 0.0
        if (
            crane_calendar is not None and crane_key is not None
            and przezb >= CRANE_NEEDED_MIN
        ):
            t_free = _crane_first_free(crane_calendar, crane_key, day, cursor, przezb)
            delay = max(0.0, t_free - cursor)
        ld = min_ld[k] if k in min_ld.index else pd.Timestamp.max
        return (delay, ld)

    remaining_must = list(must_go_kat)
    overflow_triggered = False
    while remaining_must:
        if not allow_overflow and cursor >= window_end_min:
            break
        katalog = min(remaining_must, key=_katalog_score)
        remaining_must.remove(katalog)
        przezb = _oblicz_przezbrojenie(last_kat, katalog, std_przezb, ma_przystawke)

        # Sprawdź czy setup (z ewentualnym idle gap suwnicy) zmieści się w oknie
        effective_setup_start = cursor
        if (
            crane_calendar is not None and crane_key is not None
            and przezb >= CRANE_NEEDED_MIN
        ):
            effective_setup_start = _crane_first_free(crane_calendar, crane_key, day, cursor, przezb)
        if not allow_overflow and effective_setup_start + przezb > window_end_min:
            continue  # ten katalog nie wejdzie — spróbuj kolejny

        _emit_setup(katalog, przezb)

        ral          = _extract_ral(katalog)
        blok         = kand_zew[(kand_zew["Twr_Katalog"] == katalog) &
                          (~kand_zew["Numer_zlecenia"].isin(planned))].sort_values("LD_date")
        kat_total    = round(blok["Ilosc_MBSZT_num"].sum() / predkosc, 1)
        kat_total_mb = float(blok["Ilosc_MBSZT_num"].sum())

        for _, zp in blok.iterrows():
            if str(zp["Numer_zlecenia"]) in planned:
                continue
            czas_zp = float(zp["Ilosc_MBSZT_num"]) / predkosc
            if not allow_overflow and cursor + czas_zp > window_end_min:
                overflow_triggered = True
                break
            _emit_zp(zp, katalog, ral, kat_total, kat_total_mb)
        if overflow_triggered:
            break

    # ── Faza 2: rolling (tylko zew) — leci do końca zmiany, bez zonka ────────
    # Również reorder po (delay, LD) żeby wybierać katalogi które nie kolidują
    # z aktualnie zajętą suwnicą.
    if cursor < window_end_min and zonk_min == 0.0:
        remaining_roll = list(rolling_kat)
        while remaining_roll and cursor < window_end_min:
            katalog = min(remaining_roll, key=_katalog_score)
            remaining_roll.remove(katalog)
            przezb   = _oblicz_przezbrojenie(last_kat, katalog, std_przezb, ma_przystawke)

            # Idle gap suwnicy (jeśli setup wymaga)
            effective_setup_start = cursor
            if (
                crane_calendar is not None and crane_key is not None
                and przezb >= CRANE_NEEDED_MIN
            ):
                effective_setup_start = _crane_first_free(
                    crane_calendar, crane_key, day, cursor, przezb
                )
            dostepne = window_end_min - effective_setup_start
            if przezb > dostepne:
                continue  # ten katalog się nie zmieści — spróbuj kolejny

            blok = kand_zew[
                (kand_zew["Twr_Katalog"] == katalog) &
                (~kand_zew["Numer_zlecenia"].isin(planned))
            ].sort_values("LD_date")
            if blok.empty:
                continue
            ral          = _extract_ral(katalog)
            kat_total    = round(blok["Ilosc_MBSZT_num"].sum() / predkosc, 1)
            kat_total_mb = float(blok["Ilosc_MBSZT_num"].sum())

            first_zp_min = float(blok.iloc[0]["Ilosc_MBSZT_num"]) / predkosc
            if przezb + first_zp_min > dostepne:
                continue

            _emit_setup(katalog, przezb)

            for _, zp in blok.iterrows():
                if str(zp["Numer_zlecenia"]) in planned:
                    continue
                czas_zp = float(zp["Ilosc_MBSZT_num"]) / predkosc
                if cursor + czas_zp > window_end_min:
                    break
                _emit_zp(zp, katalog, ral, kat_total, kat_total_mb)

    # ── Faza 3: wew_fill — wew o tym samym katalogu co last_kat, bez setupu ──
    # Dopina się tylko do "otwartego" katalogu (zero przezbrojenia), żeby nigdy
    # nie opóźnić zew. Ostatnie ZP może być pocięte na partial — bierzemy tylko
    # tyle MB ile się mieści w pozostałym oknie. Reszta wraca w kolejnej zmianie
    # (znowu trafi tylko gdy katalog będzie znów otwarty).
    if (
        last_kat is not None
        and cursor < window_end_min
        and zonk_min == 0.0
        and not kand_wew.empty
    ):
        wew_blok = kand_wew[
            (kand_wew["Twr_Katalog"] == last_kat) &
            (~kand_wew["Numer_zlecenia"].isin(planned))
        ].sort_values("LD_date")
        if not wew_blok.empty:
            ral_w = _extract_ral(last_kat)
            wew_kat_total_mb = float(sum(
                max(0.0, float(r["Ilosc_MBSZT_num"]) - float(partial_mb_used.get(str(r["Numer_zlecenia"]), 0.0)))
                for _, r in wew_blok.iterrows()
            ))
            for _, zp in wew_blok.iterrows():
                if cursor >= window_end_min:
                    break
                zp_num   = str(zp["Numer_zlecenia"])
                mb_full  = float(zp["Ilosc_MBSZT_num"])
                mb_done  = float(partial_mb_used.get(zp_num, 0.0))
                mb_left  = mb_full - mb_done
                if mb_left <= 0.001:
                    planned.add(zp_num)
                    continue
                czas_full = mb_left / predkosc
                dostepne  = window_end_min - cursor
                if czas_full <= dostepne + 1e-6:
                    _emit_zp(zp, last_kat, ral_w, round(czas_full, 1), wew_kat_total_mb,
                             mb_override=mb_left, is_wew=True, is_partial=False)
                    if mb_done > 0:
                        partial_mb_used.pop(zp_num, None)
                else:
                    mb_partial = dostepne * predkosc
                    if mb_partial < 0.1:
                        break
                    _emit_zp(zp, last_kat, ral_w, round(dostepne, 1), wew_kat_total_mb,
                             mb_override=mb_partial, is_wew=True, is_partial=True)
                    partial_mb_used[zp_num] = mb_done + mb_partial
                    break

    return cursor, last_kat, zonk_min


# ── Główny algorytm ───────────────────────────────────────────────────────────

_GANTT_COLS = [
    "Maszyna","Twr_Profil_Glowny","Data","Start","Koniec",
    "ZP","Twr_Katalog","RAL","MB","Minuty","LD",
    "Total_kat_min","Total_kat_MB","kolor_id","is_zonk","spoznienie","is_wew","Operator",
]

_ZONK_COLS = ["Maszyna", "Data", "zonk_min", "zonk_MB", "zonk_ZP"]


def planuj(
    df: pd.DataFrame,
    maszyny: list[dict],
    kalendarz: dict[str, int],
    operators: list[dict],
    shift_config: dict,
    operator_vacations: dict,
    data_start: date,
    horyzont: int,
    lookahead: int,
) -> tuple[pd.DataFrame, dict]:
    """
    Główna funkcja planowania z auto-przypisaniem operatorów.

    Operatorzy → auto-przypisanie do maszyn round-robin od najbardziej
    obciążonej. Bez operatorów (lub gdy żaden nie skonfigurowany) → fallback
    do planowania czysto maszynowego.

    Zwraca (gantt_df, raport). Raport zawiera m.in. zonki_df.
    """
    A_week_start = date.fromisoformat(
        shift_config.get("A_first_shift_week", str(data_start))
    )

    maszyny_dict: dict[str, dict] = {
        m.get("nazwa", m.get("id","")): m for m in maszyny
    }

    planowalne = df[df["maszyna"].notna() & (df["Ilosc_MBSZT_num"] > 0)].copy()
    if "Typ_zamowienia" not in planowalne.columns:
        planowalne["Typ_zamowienia"] = "zew"
    machine_categories = _build_machine_categories(df, maszyny_dict)
    crane_keys: dict[str, tuple[str, str] | None] = {
        nazwa: _crane_key(m, machine_categories.get(nazwa, set()))
        for nazwa, m in maszyny_dict.items()
    }
    crane_calendar: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
    gantt_rows: list[dict] = []
    planned:    set[str]   = set()
    last_kat:   dict[str, str | None] = {}   # {maszyna: last_katalog}
    partial_mb_used: dict[str, float] = {}   # {zp_num: mb_zaplanowane} — wew częściowe

    use_operators = bool(operators)

    for offset in range(horyzont):
        day    = data_start + timedelta(days=offset)
        cal_min = float(kalendarz.get(day.isoformat(), 0))
        if cal_min == 0:
            continue   # dzień wolny — last_kat NIE resetuje się

        nwd      = _next_working_day(day, kalendarz)
        nwd_next = _next_working_day(nwd, kalendarz)
        lookahead_e = day + timedelta(days=lookahead)

        day_dt = datetime(day.year, day.month, day.day, 0, 0)

        # Określ ostatnią zmianę tego dnia (jedyną gdy cal_min<960, inaczej zm.2)
        last_shift_num = 1 if cal_min < 960 else 2

        for shift_num, (sh_start, sh_end) in [(1, SHIFT_1), (2, SHIFT_2)]:
            if shift_num == 2 and cal_min < 960:
                continue  # tylko 1 zmiana tego dnia
            allow_overflow = (shift_num == last_shift_num)

            if use_operators:
                dostepni = [
                    op for op in operators
                    if get_operator_shift(op, day, A_week_start) == shift_num
                    and not _is_vacation(op["name"], day, operator_vacations)
                ]
                if not dostepni:
                    continue

                # Obciążenie maszyn → sortuj malejąco
                workload = _machine_workload(planowalne, maszyny_dict, planned)
                machines_by_load = [
                    m for m, w in sorted(workload.items(), key=lambda x: -x[1]) if w > 0
                ]
                if not machines_by_load:
                    continue

                assignments = _auto_assign(dostepni, machines_by_load, maszyny_dict, machine_categories)

                for op in dostepni:
                    cursor    = float(sh_start)
                    prev_masz = None
                    visited   = set()

                    def _has_work(m: str) -> bool:
                        return not planowalne[
                            (planowalne["maszyna"] == m) &
                            (~planowalne["Numer_zlecenia"].isin(planned))
                        ].empty

                    def _pick_next_machine() -> str | None:
                        # 1. przypisane (już przefiltrowane wg kompetencji) — nieodwiedzone, z pracą
                        for m in assignments[op["name"]]:
                            if m not in visited and m in maszyny_dict and _has_work(m):
                                return m
                        # 2. dowolna nieodwiedzona z pracą — TYLKO jeśli operator ma kompetencje
                        for m in machines_by_load:
                            if m in visited or m not in maszyny_dict or not _has_work(m):
                                continue
                            if _operator_can_handle(op, maszyny_dict[m], machine_categories.get(m, set())):
                                return m
                        return None

                    while cursor < sh_end:
                        masz_name = _pick_next_machine()
                        if masz_name is None:
                            break
                        visited.add(masz_name)

                        # Przejście między maszynami
                        if prev_masz is not None:
                            arrival = cursor + TRANSIT_MIN
                            if arrival >= sh_end:
                                break
                            # Nie płać 20 min przejścia, jeśli po dojściu nie
                            # zmieści się już nawet przezbrojenie — chyba że
                            # katalog na maszynie jest otwarty (0 min setupu)
                            # albo to ostatnia zmiana dnia (dozwolony zonk).
                            if not allow_overflow:
                                _std_p = float(
                                    maszyny_dict[masz_name].get("czas_przezbrojenia_min", 12) or 12
                                )
                                _kand_n = planowalne[
                                    (planowalne["maszyna"] == masz_name)
                                    & (~planowalne["Numer_zlecenia"].isin(planned))
                                ]
                                _otwarty = (
                                    last_kat.get(masz_name) is not None
                                    and (_kand_n["Twr_Katalog"] == last_kat.get(masz_name)).any()
                                )
                                if not _otwarty and arrival + _std_p >= sh_end:
                                    break
                            ts = day_dt + timedelta(minutes=cursor)
                            te = day_dt + timedelta(minutes=cursor + TRANSIT_MIN)
                            gantt_rows.append({
                                "Maszyna": masz_name, "Twr_Profil_Glowny": "",
                                "Data": day.isoformat(), "Start": ts, "Koniec": te,
                                "ZP": f"— Przejście ({op['name']}) —",
                                "Twr_Katalog": "", "RAL": "",
                                "MB": 0.0, "Minuty": TRANSIT_MIN,
                                "LD": "—", "Total_kat_min": 0.0, "Total_kat_MB": 0.0,
                                "kolor_id": TRANSIT_COLOR, "is_zonk": False,
                                "spoznienie": False, "is_wew": False,
                                "Operator": op["name"],
                            })
                            cursor += TRANSIT_MIN

                        if cursor >= sh_end:
                            break

                        kand_masz  = planowalne[planowalne["maszyna"] == masz_name]
                        _ma_przyst = bool(maszyny_dict[masz_name].get("ma_przystawke_filcu", False))

                        new_cursor, new_kat, _zonk = _plan_window(
                            kand_masz, maszyny_dict[masz_name], day, day_dt,
                            cursor, float(sh_end),
                            nwd=nwd, nwd_next=nwd_next, lookahead_end=lookahead_e,
                            last_kat=last_kat.get(masz_name),
                            planned=planned, gantt_rows=gantt_rows,
                            partial_mb_used=partial_mb_used,
                            operator_name=op["name"],
                            crane_calendar=crane_calendar,
                            crane_key=crane_keys.get(masz_name),
                            ma_przystawke=_ma_przyst,
                            allow_overflow=allow_overflow,
                        )
                        last_kat[masz_name] = new_kat
                        if new_cursor > cursor:
                            cursor    = new_cursor
                            prev_masz = masz_name

            else:
                # ── Fallback: brak operatorów → plan per maszyna ──────────────
                for masz_name, machine in maszyny_dict.items():
                    machine_limit = float(machine.get("limit_dzienny_min", 960) or 960)
                    w_start = float(sh_start)
                    w_end   = min(float(sh_end), w_start + machine_limit)

                    kand_masz   = planowalne[planowalne["maszyna"] == masz_name]
                    _ma_przyst  = bool(machine.get("ma_przystawke_filcu", False))

                    _, new_kat, _zonk = _plan_window(
                        kand_masz, machine, day, day_dt,
                        w_start, w_end,
                        nwd=nwd, nwd_next=nwd_next, lookahead_end=lookahead_e,
                        last_kat=last_kat.get(masz_name),
                        planned=planned, gantt_rows=gantt_rows,
                        partial_mb_used=partial_mb_used,
                        crane_calendar=crane_calendar,
                        crane_key=crane_keys.get(masz_name),
                        ma_przystawke=_ma_przyst,
                        allow_overflow=allow_overflow,
                    )
                    last_kat[masz_name] = new_kat

    # ── Niezaplanowane ────────────────────────────────────────────────────────
    wszystkie   = set(planowalne["Numer_zlecenia"].astype(str))
    niezapl_ids = wszystkie - planned
    niezapl_df  = planowalne[planowalne["Numer_zlecenia"].astype(str).isin(niezapl_ids)].copy()
    niezapl_df["LD"] = niezapl_df["LD_date"].dt.date.astype(str)
    niezapl_log = (
        niezapl_df[["Numer_zlecenia","maszyna","Twr_Katalog","Ilosc_MBSZT_num","LD"]]
        .rename(columns={"Ilosc_MBSZT_num":"MB"})
        .to_dict("records")
    )

    gantt_df = (
        pd.DataFrame(gantt_rows, columns=_GANTT_COLS)
        if gantt_rows else pd.DataFrame(columns=_GANTT_COLS)
    )

    # ── Optymalizacja dziur suwnicowych (2-opt post-processing) ──────────────
    crane_stats = {"iteracji": 0, "swapów": 0, "idle_przed": 0.0, "idle_po": 0.0}
    if not gantt_df.empty:
        gantt_df, crane_stats = optimize_crane_idle(
            gantt_df, maszyny,
            machine_categories=machine_categories,
            df_orders=df,
        )

    # ── Agregacja zonków per (Maszyna, Data) ─────────────────────────────────
    zonki_df = _build_zonki_df(gantt_df)

    raport = {
        "zaplanowane":    len(planned),
        "niezaplanowane": len(niezapl_log),
        "niezaplanowane_zp": niezapl_log,
        "zonki":          zonki_df.to_dict("records"),
        "crane_optimizer": crane_stats,
    }
    return gantt_df, raport


def _build_zonki_df(gantt_df: pd.DataFrame) -> pd.DataFrame:
    """Agreguje zonki z gantt_df do tabeli per (Maszyna, Data)."""
    if gantt_df.empty or "is_zonk" not in gantt_df.columns:
        return pd.DataFrame(columns=_ZONK_COLS)

    z = gantt_df[gantt_df["is_zonk"] == True].copy()
    if z.empty:
        return pd.DataFrame(columns=_ZONK_COLS)

    # Pomijamy przezbrojenia/przejścia w sumach MB/ZP, ale ich min liczymy do zonk_min
    is_op = z["kolor_id"].isin([SETUP_COLOR, TRANSIT_COLOR])

    grouped = z.groupby(["Maszyna","Data"]).apply(
        lambda g: pd.Series({
            "zonk_min": round(float(g["Minuty"].sum()), 1),
            "zonk_MB":  round(float(g.loc[~g["kolor_id"].isin([SETUP_COLOR, TRANSIT_COLOR]),"MB"].sum()), 1),
            "zonk_ZP":  int((~g["kolor_id"].isin([SETUP_COLOR, TRANSIT_COLOR])).sum()),
        })
    ).reset_index()

    return grouped[_ZONK_COLS]


# ── Reorder katalogów (drag & drop) ──────────────────────────────────────────

def reorder_katalogi(
    gantt_df: pd.DataFrame,
    maszyna: str,
    dzien: str,
    new_order: list[str],
    machine_config: dict,
    crane_calendar: dict | None = None,
    crane_key: tuple[str, str] | None = None,
) -> pd.DataFrame:
    """
    Przebudowuje wiersze Gantta dla danej maszyny i dnia według nowej kolejności
    katalogów. Wiersze przejścia (transit) są zachowane bez zmian.

    Jeśli `crane_calendar`/`crane_key` są podane, setupy ≥12 min są koordynowane
    z innymi rezerwacjami suwnicy (cursor przesuwany do pierwszego wolnego slotu).
    """
    day    = date.fromisoformat(dzien)
    day_dt = datetime(day.year, day.month, day.day, 0, 0)
    std_przezb   = float(machine_config.get("czas_przezbrojenia_min", 12) or 12)
    predkosc     = float(machine_config.get("predkosc_mb_min", 0) or 0)
    nazwa        = machine_config.get("nazwa", machine_config.get("id", maszyna))
    ma_przystawke = bool(machine_config.get("ma_przystawke_filcu", False))

    mask_this  = (gantt_df["Maszyna"] == maszyna) & (gantt_df["Data"] == dzien)
    rows_this  = gantt_df[mask_this].copy().sort_values("Start")
    rows_other = gantt_df[~mask_this].copy()

    transit_rows = rows_this[rows_this["kolor_id"] == TRANSIT_COLOR]

    if not transit_rows.empty:
        last_te = transit_rows["Koniec"].max()
        cursor_min = (last_te - day_dt).total_seconds() / 60.0
    elif not rows_this.empty:
        first_min = (rows_this["Start"].min() - day_dt).total_seconds() / 60.0
        cursor_min = float(SHIFT_1[0] if first_min < SHIFT_2[0] else SHIFT_2[0])
    else:
        cursor_min = float(SHIFT_1[0])

    shift_end = float(SHIFT_2[1] if cursor_min >= SHIFT_2[0] else SHIFT_1[1])

    prev_prod = rows_other[
        (rows_other["Maszyna"] == maszyna) &
        (rows_other["Data"] < dzien) &
        (~rows_other["kolor_id"].isin([TRANSIT_COLOR, SETUP_COLOR])) &
        (rows_other["Twr_Katalog"].notna()) &
        (rows_other["Twr_Katalog"] != "")
    ].sort_values("Data")
    last_kat: str | None = prev_prod["Twr_Katalog"].iloc[-1] if not prev_prod.empty else None

    prod_rows = rows_this[~rows_this["kolor_id"].isin([TRANSIT_COLOR, SETUP_COLOR])]

    new_rows: list[dict] = []
    for katalog in new_order:
        przezb   = _oblicz_przezbrojenie(last_kat, katalog, std_przezb, ma_przystawke)

        # Koordynacja suwnicy: jeśli setup ≥12min wymaga suwnicy, przesuń kursor
        # do najwcześniejszego wolnego slotu i zarezerwuj. Mid-day idle gap
        # (last_kat != None) wstawia wiersz konfliktu suwnicy.
        if (
            crane_calendar is not None and crane_key is not None
            and przezb >= CRANE_NEEDED_MIN
        ):
            new_start = _crane_first_free(crane_calendar, crane_key, day, cursor_min, przezb)
            if new_start > cursor_min:
                if last_kat is not None:
                    cs = day_dt + timedelta(minutes=cursor_min)
                    ce = day_dt + timedelta(minutes=new_start)
                    new_rows.append({
                        "Maszyna": nazwa, "Twr_Profil_Glowny": "",
                        "Data": dzien, "Start": cs, "Koniec": ce,
                        "ZP": "— Konflikt suwnicy —", "Twr_Katalog": "",
                        "RAL": "",
                        "MB": 0.0, "Minuty": round(new_start - cursor_min, 1),
                        "LD": "—", "Total_kat_min": 0.0, "Total_kat_MB": 0.0,
                        "kolor_id": CRANE_CONFLICT_COLOR,
                        "is_zonk": False, "spoznienie": False, "is_wew": False,
                        "Operator": "",
                    })
                cursor_min = new_start
        dostepne = shift_end - cursor_min

        if przezb > dostepne:
            break

        if przezb > 0:
            if (
                crane_calendar is not None and crane_key is not None
                and przezb >= CRANE_NEEDED_MIN
            ):
                _crane_reserve(crane_calendar, crane_key, day, cursor_min, przezb)
            ps = day_dt + timedelta(minutes=cursor_min)
            pe = day_dt + timedelta(minutes=cursor_min + przezb)
            new_rows.append({
                "Maszyna": nazwa, "Twr_Profil_Glowny": "",
                "Data": dzien, "Start": ps, "Koniec": pe,
                "ZP": "— Przezbrojenie —", "Twr_Katalog": katalog,
                "RAL": _extract_ral(katalog),
                "MB": 0.0, "Minuty": round(przezb, 1),
                "LD": "—", "Total_kat_min": 0.0, "Total_kat_MB": 0.0,
                "kolor_id": SETUP_COLOR, "is_zonk": False,
                "is_wew": False,
                "Operator": "",
            })
            cursor_min += przezb
            last_kat = katalog

        kat_rows = prod_rows[prod_rows["Twr_Katalog"] == katalog].sort_values("Start")
        for _, row in kat_rows.iterrows():
            mb      = float(row["MB"])
            czas_zp = (mb / predkosc) if predkosc > 0 else float(row["Minuty"])
            if czas_zp > shift_end - cursor_min:
                break
            zs = day_dt + timedelta(minutes=cursor_min)
            ze = day_dt + timedelta(minutes=cursor_min + czas_zp)
            new_rows.append({
                "Maszyna": nazwa,
                "Twr_Profil_Glowny": row.get("Twr_Profil_Glowny", ""),
                "Data": dzien, "Start": zs, "Koniec": ze,
                "ZP": row["ZP"], "Twr_Katalog": katalog,
                "RAL": row["RAL"],
                "MB": round(mb, 3), "Minuty": round(czas_zp, 2),
                "LD": row["LD"],
                "Total_kat_min": row.get("Total_kat_min", 0.0),
                "Total_kat_MB": float(row.get("Total_kat_MB", 0.0) or 0.0),
                "kolor_id": row["kolor_id"],
                "is_zonk": bool(row.get("is_zonk") or False),
                "is_wew": bool(row.get("is_wew") or False),
                "Operator": str(row.get("Operator", "") or ""),
            })
            cursor_min += czas_zp
            last_kat = katalog

    new_df     = pd.DataFrame(new_rows, columns=_GANTT_COLS) if new_rows else pd.DataFrame(columns=_GANTT_COLS)
    transit_df = transit_rows.reindex(columns=_GANTT_COLS) if not transit_rows.empty else pd.DataFrame(columns=_GANTT_COLS)

    result = pd.concat(
        [rows_other.reindex(columns=_GANTT_COLS), transit_df, new_df],
        ignore_index=True,
    )
    return result.sort_values(["Data", "Maszyna", "Start"]).reset_index(drop=True)


# ── Optymalizator dziur suwnicowych (post-processing 2-opt) ───────────────────

def _idle_minutes_machine_day(gantt_df: pd.DataFrame, machine: str, day_iso: str) -> float:
    """Suma minut wierszy `konflikt_suwnicy` na maszynie w danym dniu —
    czas gdy maszyna stoi czekając na wolną suwnicę. Pre-shift kolejkowanie
    NIE jest liczone (silnik nie wstawia tam wiersza konfliktu)."""
    return float(gantt_df[
        (gantt_df["Maszyna"] == machine) & (gantt_df["Data"] == day_iso)
        & (gantt_df["kolor_id"] == CRANE_CONFLICT_COLOR)
    ]["Minuty"].sum())


def _build_crane_cal_from_gantt(
    gantt_df: pd.DataFrame,
    maszyny_dict: dict[str, dict],
    machine_categories: dict[str, set[str]],
) -> dict[tuple[str, str, str], list[tuple[float, float]]]:
    """Odtwarza kalendarz suwnic z ganttu — bierze tylko setupy ≥CRANE_NEEDED_MIN."""
    cal: dict[tuple[str, str, str], list[tuple[float, float]]] = {}
    setupy = gantt_df[
        (gantt_df["kolor_id"] == SETUP_COLOR) & (gantt_df["Minuty"] >= CRANE_NEEDED_MIN - 0.001)
    ]
    for _, r in setupy.iterrows():
        m = maszyny_dict.get(str(r["Maszyna"]))
        if not m:
            continue
        key = _crane_key(m, machine_categories.get(str(r["Maszyna"]), set()))
        if key is None:
            continue
        day_iso = str(r["Data"])
        day_dt = pd.to_datetime(day_iso)
        s = (pd.to_datetime(r["Start"]) - day_dt).total_seconds() / 60.0
        e = (pd.to_datetime(r["Koniec"]) - day_dt).total_seconds() / 60.0
        cal.setdefault((key[0], key[1], day_iso), []).append((float(s), float(e)))
    return cal


def _ld_check_machine_day(gantt_df: pd.DataFrame, machine: str, day_iso: str) -> bool:
    """True jeśli żadne ZP produkcyjne na tej (maszyna, dzień) nie ma Start.date() > LD."""
    sub = gantt_df[
        (gantt_df["Maszyna"] == machine) & (gantt_df["Data"] == day_iso)
        & (~gantt_df["kolor_id"].isin([SETUP_COLOR, TRANSIT_COLOR]))
        & gantt_df["LD"].notna() & (gantt_df["LD"] != "—") & (gantt_df["LD"] != "")
    ]
    for _, r in sub.iterrows():
        try:
            ld_d = pd.to_datetime(str(r["LD"])).date()
        except Exception:
            continue
        start_d = pd.to_datetime(r["Start"]).date()
        if start_d > ld_d:
            return False
    return True


def _machine_katalog_order(gantt_df: pd.DataFrame, machine: str, day_iso: str) -> list[str]:
    """Lista katalogów na maszynie w danym dniu, w aktualnej kolejności (z ganttu)."""
    sub = gantt_df[
        (gantt_df["Maszyna"] == machine) & (gantt_df["Data"] == day_iso)
        & (~gantt_df["kolor_id"].isin([TRANSIT_COLOR, CRANE_CONFLICT_COLOR]))
    ].sort_values("Start")
    if sub.empty:
        return []
    out: list[str] = []
    for k in sub["Twr_Katalog"].astype(str).tolist():
        if k and k not in out:
            out.append(k)
    return out


def _try_apply_order(
    base_df: pd.DataFrame,
    m_name: str,
    day: str,
    new_order: list[str],
    machine_cfg: dict,
    crane_key: tuple[str, str] | None,
    maszyny_dict: dict[str, dict],
    machine_categories: dict[str, set[str]],
    base_cand_cal: dict | None = None,
    prod_old_count: int | None = None,
) -> pd.DataFrame | None:
    """Re-symuluje (m_name, day) z wymuszoną kolejnością katalogów. Jeśli
    `base_cand_cal` podane (wzbudzony raz per sweep), używa kopii zamiast
    budować od nowa — krytyczne dla wydajności w 2-opt.
    """
    if base_cand_cal is None:
        base_excl = base_df[~(
            (base_df["Maszyna"] == m_name) & (base_df["Data"] == day)
        )]
        cand_cal = _build_crane_cal_from_gantt(base_excl, maszyny_dict, machine_categories)
    else:
        # Płytka kopia: każda lista pod kluczem nowa, klucze i wartości pierwotne
        cand_cal = {k: list(v) for k, v in base_cand_cal.items()}
    cand_df = reorder_katalogi(
        base_df, m_name, day, new_order,
        machine_cfg, crane_calendar=cand_cal, crane_key=crane_key,
    )
    if prod_old_count is None:
        prod_old_count = int(((base_df["Maszyna"] == m_name)
            & (base_df["Data"] == day)
            & (~base_df["kolor_id"].isin([SETUP_COLOR, TRANSIT_COLOR, CRANE_CONFLICT_COLOR]))
        ).sum())
    prod_new_count = int(((cand_df["Maszyna"] == m_name)
        & (cand_df["Data"] == day)
        & (~cand_df["kolor_id"].isin([SETUP_COLOR, TRANSIT_COLOR, CRANE_CONFLICT_COLOR]))
    ).sum())
    if prod_new_count < prod_old_count:
        return None
    return cand_df


def _conflict_minutes_group(df_: pd.DataFrame, machs: list[str], day: str) -> float:
    """Suma minut wierszy `konflikt_suwnicy` w (grupa, dzień)."""
    return sum(_idle_minutes_machine_day(df_, m, day) for m in machs)


def optimize_crane_idle(
    gantt_df: pd.DataFrame,
    maszyny: list[dict],
    machine_categories: dict[str, set[str]] | None = None,
    df_orders: pd.DataFrame | None = None,
    max_iter: int = 8,
    n_restarts: int = 30,
    time_budget_s: float = 60.0,
    rng_seed: int = 42,
) -> tuple[pd.DataFrame, dict]:
    """Post-processing optymalizator: per (grupa_suwnicy, dzień) — pełny
    pair-wise 2-opt katalogów + N losowych restartów. Cel: minimalizacja
    `Σ konflikt_suwnicy_min`, LD constraint zachowany.

    Skróty wydajnościowe (krytyczne na realnych danych):
    - Skip (grupa, dzień) gdy konflikt już = 0 na starcie
    - Wczesne wyjście gdy konflikt spada do 0
    - Twardy timeout `time_budget_s` (zwraca najlepszy stan jaki zdążył)

    Zwraca (nowy_gantt_df, statystyki).
    """
    import random, time
    rng = random.Random(rng_seed)
    t_start = time.monotonic()

    maszyny_dict = {m.get("nazwa", m.get("id", "")): m for m in maszyny}
    if machine_categories is None:
        machine_categories = (
            _build_machine_categories(df_orders, maszyny_dict)
            if df_orders is not None else {n: set() for n in maszyny_dict}
        )

    groups: dict[tuple[str, str], list[str]] = {}
    crane_keys: dict[str, tuple[str, str] | None] = {}
    for n, m in maszyny_dict.items():
        k = _crane_key(m, machine_categories.get(n, set()))
        crane_keys[n] = k
        if k is not None:
            groups.setdefault(k, []).append(n)

    new_df = gantt_df.copy()
    stats = {
        "iteracji": 0, "swapów": 0, "restartów": 0, "skipów": 0,
        "idle_przed": 0.0, "idle_po": 0.0, "timeout": False,
    }
    if not groups:
        return new_df, stats

    days_all = sorted(new_df["Data"].dropna().unique().tolist())
    stats["idle_przed"] = sum(
        _conflict_minutes_group(new_df, machs, d)
        for machs in groups.values() for d in days_all
    )

    def _budget_left() -> bool:
        return (time.monotonic() - t_start) < time_budget_s

    def _local_2opt(start_df: pd.DataFrame, machs, day) -> tuple[pd.DataFrame, int, float]:
        """Greedy 2-opt pełne pary do zbieżności. Wczesne wyjście przy konflikt=0."""
        cur = start_df
        n_sw = 0
        cur_conflict = _conflict_minutes_group(cur, machs, day)
        for _ in range(max_iter):
            if cur_conflict <= 0.001:
                break
            if not _budget_left():
                stats["timeout"] = True
                break
            improved = False
            for m_name in machs:
                if not _budget_left():
                    stats["timeout"] = True
                    break
                ko = _machine_katalog_order(cur, m_name, day)
                if len(ko) < 2:
                    continue
                # Zbuduj bazę kalendarza suwnicy raz per (maszyna, sweep) — bez
                # rezerwacji tej maszyny w tym dniu. Stała przez całą pętlę (i, j)
                # bo kalendarz innych maszyn nie zmienia się przy swapie X.
                base_excl = cur[~(
                    (cur["Maszyna"] == m_name) & (cur["Data"] == day)
                )]
                base_cand_cal = _build_crane_cal_from_gantt(
                    base_excl, maszyny_dict, machine_categories
                )
                prod_old_count = int(((cur["Maszyna"] == m_name)
                    & (cur["Data"] == day)
                    & (~cur["kolor_id"].isin([SETUP_COLOR, TRANSIT_COLOR, CRANE_CONFLICT_COLOR]))
                ).sum())
                for i in range(len(ko)):
                    if not _budget_left():
                        stats["timeout"] = True
                        break
                    for j in range(i + 1, len(ko)):
                        cand_order = ko.copy()
                        cand_order[i], cand_order[j] = cand_order[j], cand_order[i]
                        cand_df = _try_apply_order(
                            cur, m_name, day, cand_order,
                            maszyny_dict[m_name], crane_keys[m_name],
                            maszyny_dict, machine_categories,
                            base_cand_cal=base_cand_cal,
                            prod_old_count=prod_old_count,
                        )
                        if cand_df is None:
                            continue
                        if not _ld_check_machine_day(cand_df, m_name, day):
                            continue
                        cand_conflict = _conflict_minutes_group(cand_df, machs, day)
                        if cand_conflict + 0.001 < cur_conflict:
                            cur = cand_df
                            cur_conflict = cand_conflict
                            n_sw += 1
                            improved = True
                            ko = _machine_katalog_order(cur, m_name, day)
                            if cur_conflict <= 0.001:
                                break
                            # Po akceptacji swapu — rebuild base_cand_cal
                            base_excl = cur[~(
                                (cur["Maszyna"] == m_name) & (cur["Data"] == day)
                            )]
                            base_cand_cal = _build_crane_cal_from_gantt(
                                base_excl, maszyny_dict, machine_categories
                            )
                            prod_old_count = int(((cur["Maszyna"] == m_name)
                                & (cur["Data"] == day)
                                & (~cur["kolor_id"].isin([SETUP_COLOR, TRANSIT_COLOR, CRANE_CONFLICT_COLOR]))
                            ).sum())
                    if cur_conflict <= 0.001:
                        break
                if improved:
                    break
            stats["iteracji"] += 1
            if not improved:
                break
        return cur, n_sw, cur_conflict

    def _adaptive_restarts(machs_count: int, max_kat: int) -> int:
        """Adaptive liczba restartów wg rozmiaru przestrzeni przeszukiwania."""
        if machs_count <= 3 and max_kat <= 5:
            return max(n_restarts, 100)  # małe grupy → bardzo dużo restartów
        if machs_count <= 5 and max_kat <= 7:
            return n_restarts                # średnie → bazowo (30)
        return max(10, n_restarts // 3)      # duże → mniej (drogie ewaluacje)

    for group_key, machs in groups.items():
        for day in days_all:
            if not _budget_left():
                stats["timeout"] = True
                break
            base_conflict = _conflict_minutes_group(new_df, machs, day)
            if base_conflict <= 0.001:
                stats["skipów"] += 1
                continue  # nie ma co optymalizować

            # Faza 1: lokalny 2-opt z bieżącej pozycji
            best_df, n_sw, best_conflict = _local_2opt(new_df, machs, day)
            stats["swapów"] += n_sw

            # Adaptive restarts wg rozmiaru grupy/dnia
            max_kat = max(
                (len(_machine_katalog_order(new_df, m, day)) for m in machs),
                default=0,
            )
            local_n_restarts = _adaptive_restarts(len(machs), max_kat)

            # Faza 2: random restart (tylko jeśli konflikt jeszcze > 0)
            for r in range(local_n_restarts):
                if best_conflict <= 0.001 or not _budget_left():
                    if not _budget_left():
                        stats["timeout"] = True
                    break
                stats["restartów"] += 1
                seed_df = new_df.copy()
                seed_failed = False
                for m_name in machs:
                    cur_order = _machine_katalog_order(seed_df, m_name, day)
                    if len(cur_order) < 2:
                        continue
                    perm = cur_order.copy()
                    rng.shuffle(perm)
                    sd = _try_apply_order(
                        seed_df, m_name, day, perm,
                        maszyny_dict[m_name], crane_keys[m_name],
                        maszyny_dict, machine_categories,
                    )
                    if sd is None or not _ld_check_machine_day(sd, m_name, day):
                        seed_failed = True
                        break
                    seed_df = sd
                if seed_failed:
                    continue
                cand_df, n_sw_r, cand_conflict = _local_2opt(seed_df, machs, day)
                stats["swapów"] += n_sw_r
                if cand_conflict + 0.001 < best_conflict:
                    best_df = cand_df
                    best_conflict = cand_conflict

            new_df = best_df
        if stats["timeout"]:
            break

    stats["idle_po"] = sum(
        _conflict_minutes_group(new_df, machs, d)
        for machs in groups.values() for d in days_all
    )
    return new_df, stats
