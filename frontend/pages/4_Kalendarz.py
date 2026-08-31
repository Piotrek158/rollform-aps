import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from datetime import date, timedelta
from utils import load_config, save_config, get_or_init_calendar

st.set_page_config(page_title="Kalendarz pracy", page_icon="📆", layout="wide")
st.title("📆 Kalendarz pracy")
st.markdown("---")

DAYS_PL = ["Pn", "Wt", "Śr", "Cz", "Pt", "Sb", "Nd"]
SHIFT_LABELS = {0: "🔴 Wolny", 480: "🟡 1 zmiana", 960: "🟢 2 zmiany", 1440: "🔵 3 zmiany"}

def shift_label(minutes):
    return SHIFT_LABELS.get(minutes, f"⚙️ {minutes} min")

config = load_config()
cal = get_or_init_calendar(config, days=60)
save_config(config)  # Utrwala wygenerowane domyślne dni

# --- Szybkie przyciski ---
st.subheader("Szybkie ustawienia")
col1, col2, col3, col4, col5 = st.columns(5)

def set_weekdays(minutes):
    today = date.today()
    for i in range(60):
        d = today + timedelta(days=i)
        if d.weekday() < 5:
            cal[d.isoformat()] = minutes
    config["calendar"] = cal
    save_config(config)
    st.rerun()

with col1:
    if st.button("🟢 Dni robocze → 2 zmiany (960)", use_container_width=True):
        set_weekdays(960)
with col2:
    if st.button("🟡 Dni robocze → 1 zmiana (480)", use_container_width=True):
        set_weekdays(480)
with col3:
    if st.button("🔵 Dni robocze → 3 zmiany (1440)", use_container_width=True):
        set_weekdays(1440)
with col4:
    if st.button("🔴 Dni robocze → Wolny (0)", use_container_width=True):
        set_weekdays(0)
with col5:
    if st.button("🔄 Resetuj weekendy → 0", use_container_width=True):
        today = date.today()
        for i in range(60):
            d = today + timedelta(days=i)
            if d.weekday() >= 5:
                cal[d.isoformat()] = 0
        config["calendar"] = cal
        save_config(config)
        st.rerun()

st.markdown("---")

# --- Tabela kalendarza ---
st.subheader("Edycja dni")
st.caption("Zmień minuty dla dowolnego dnia i kliknij Zapisz. Standardowe wartości: 0 = wolny, 480 = 1 zmiana, 960 = 2 zmiany, 1440 = 3 zmiany.")

today = date.today()
rows = []
for i in range(60):
    d = today + timedelta(days=i)
    key = d.isoformat()
    minutes = cal.get(key, 0)
    is_weekend = d.weekday() >= 5
    rows.append({
        "Data": key,
        "Dzień": DAYS_PL[d.weekday()],
        "Minuty": minutes,
        "Zmiana": shift_label(minutes),
        "_weekend": is_weekend,
    })

df = pd.DataFrame(rows)

edited = st.data_editor(
    df[["Data", "Dzień", "Minuty", "Zmiana"]],
    column_config={
        "Data":   st.column_config.TextColumn("Data", disabled=True, width="small"),
        "Dzień":  st.column_config.TextColumn("Dzień", disabled=True, width="small"),
        "Minuty": st.column_config.NumberColumn(
            "Minuty", min_value=0, max_value=1440, step=60, width="small",
            help="0=wolny | 480=1 zmiana | 960=2 zmiany | 1440=3 zmiany"
        ),
        "Zmiana": st.column_config.TextColumn("Zmiana", disabled=True, width="medium"),
    },
    hide_index=True,
    use_container_width=True,
    height=600,
    key="kalendarz_editor",
)

col_save, col_info = st.columns([1, 4])
with col_save:
    if st.button("💾 Zapisz kalendarz", type="primary", use_container_width=True):
        for _, row in edited.iterrows():
            config["calendar"][row["Data"]] = int(row["Minuty"]) if pd.notna(row["Minuty"]) else 0
        save_config(config)
        with col_info:
            st.success("✅ Kalendarz zapisany!")
        st.rerun()

# --- Podsumowanie ---
st.markdown("---")
st.subheader("Podsumowanie (60 dni)")

total_days = len(rows)
working_days = sum(1 for r in rows if r["Minuty"] > 0)
total_minutes = sum(r["Minuty"] for r in rows)
weekend_work = sum(1 for r in rows if r["_weekend"] and r["Minuty"] > 0)

c1, c2, c3, c4 = st.columns(4)
c1.metric("Dni roboczych (>0 min)", working_days)
c2.metric("Łącznie godzin", f"{total_minutes // 60} h")
c3.metric("Łącznie minut", f"{total_minutes:,}")
c4.metric("Weekendy pracujące", weekend_work)

# Mini wykres
chart_df = pd.DataFrame({"Minuty": [r["Minuty"] for r in rows]}, index=[r["Data"] for r in rows])
st.bar_chart(chart_df, height=200)
