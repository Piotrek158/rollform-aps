import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from utils import load_config, save_config, get_machines

st.set_page_config(page_title="Konfiguracja maszyn", page_icon="⚙️", layout="wide")
st.title("⚙️ Konfiguracja maszyn")
st.markdown("---")

config = load_config()

# --- Wybór zakładu ---
zaklad_options = ["Zabornia", "Sękocin"]
default_zaklad = st.session_state.get("zaklad", "Zabornia")
default_idx = zaklad_options.index(default_zaklad) if default_zaklad in zaklad_options else 0

zaklad_display = st.selectbox("Zakład", zaklad_options, index=default_idx)
zaklad_key = "Sekocin" if zaklad_display == "Sękocin" else "Zabornia"

machines = get_machines(config, zaklad_key)

st.caption(f"Maszyn w zakładzie: **{len(machines)}**")
st.markdown("---")

# --- Tabela edycji parametrów ---
st.subheader("Parametry maszyn")
st.caption("Uzupełnij prędkość, limit dzienny i czas przezbrojenia (SMED) dla każdej maszyny.")

df_edit = pd.DataFrame([
    {
        "Maszyna": m["nazwa"],
        "Prędkość (MB/min)": m.get("predkosc_mb_min"),
        "Limit dzienny (min)": m.get("limit_dzienny_min"),
        "Czas przezbrojenia (min)": m.get("czas_przezbrojenia_min"),
        "Przystawka Filc": bool(m.get("ma_przystawke_filcu", False)),
    }
    for m in machines
])

edited = st.data_editor(
    df_edit,
    column_config={
        "Maszyna": st.column_config.TextColumn("Maszyna", disabled=True, width="medium"),
        "Prędkość (MB/min)": st.column_config.NumberColumn(
            "Prędkość (MB/min)", min_value=0.0, step=0.1, format="%.2f"
        ),
        "Limit dzienny (min)": st.column_config.NumberColumn(
            "Limit dzienny (min)", min_value=0, step=10, format="%d"
        ),
        "Czas przezbrojenia (min)": st.column_config.NumberColumn(
            "Przezbrojenie SMED (min)", min_value=0, step=1, format="%d",
            help="Czas przezbrojenia przy zmianie katalogu (zmiana coilu, wymaga suwnicy). "
                 "To jest czas, który redukuje się metodą SMED.",
        ),
        "Przystawka Filc": st.column_config.CheckboxColumn(
            "Przystawka Filc",
            help="Zaznacz jeśli maszyna ma przystawkę do filcu — zmiana z/na katalog z F nie wymaga przezbrojenia.",
            width="small",
        ),
    },
    hide_index=True,
    use_container_width=True,
    key=f"editor_maszyny_{zaklad_key}",
)

# --- Zapis ---
col_save, col_status = st.columns([1, 3])
with col_save:
    if st.button("💾 Zapisz", type="primary", use_container_width=True):
        for m in config["machines"]:
            if m["zaklad"] != zaklad_key:
                continue
            rows = edited[edited["Maszyna"] == m["nazwa"]]
            if rows.empty:
                continue
            row = rows.iloc[0]
            m["predkosc_mb_min"] = row["Prędkość (MB/min)"] if pd.notna(row["Prędkość (MB/min)"]) else None
            m["limit_dzienny_min"] = row["Limit dzienny (min)"] if pd.notna(row["Limit dzienny (min)"]) else None
            m["czas_przezbrojenia_min"] = row["Czas przezbrojenia (min)"] if pd.notna(row["Czas przezbrojenia (min)"]) else None
            m["ma_przystawke_filcu"] = bool(row["Przystawka Filc"]) if pd.notna(row["Przystawka Filc"]) else False
        save_config(config)
        with col_status:
            st.success("✅ Konfiguracja zapisana!")

# --- Podgląd Twr_Profil ---
st.markdown("---")
st.subheader("Twr_Profil maszyny")
st.caption("Przypisania Twr_Profil są pre-konfigurowane i tylko do podglądu.")

selected = st.selectbox(
    "Wybierz maszynę",
    options=[m["nazwa"] for m in machines],
    key="maszyna_podglad",
)

if selected:
    m_data = next((m for m in machines if m["nazwa"] == selected), None)
    if m_data:
        profils = m_data.get("twr_profil", [])
        cols = st.columns(4)
        for i, profil in enumerate(sorted(profils)):
            cols[i % 4].markdown(f"• {profil}")

# --- Podsumowanie konfiguracji ---
st.markdown("---")
st.subheader("Stan konfiguracji")

skonfigurowane = [m for m in machines if m.get("predkosc_mb_min") and m.get("limit_dzienny_min") and m.get("czas_przezbrojenia_min")]
nieskonfigurowane = [m for m in machines if not (m.get("predkosc_mb_min") and m.get("limit_dzienny_min") and m.get("czas_przezbrojenia_min"))]

col1, col2 = st.columns(2)
col1.metric("✅ Skonfigurowane", len(skonfigurowane))
col2.metric("⚠️ Do uzupełnienia", len(nieskonfigurowane))

if nieskonfigurowane:
    with st.expander(f"Maszyny bez pełnej konfiguracji ({len(nieskonfigurowane)})"):
        for m in nieskonfigurowane:
            brakuje = []
            if not m.get("predkosc_mb_min"):
                brakuje.append("prędkość")
            if not m.get("limit_dzienny_min"):
                brakuje.append("limit dzienny")
            if not m.get("czas_przezbrojenia_min"):
                brakuje.append("czas przezbrojenia")
            st.write(f"**{m['nazwa']}** — brakuje: {', '.join(brakuje)}")
