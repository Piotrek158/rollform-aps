import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd
from utils import load_config, save_config, get_skill_categories

st.set_page_config(page_title="Operatorzy", page_icon="👷", layout="wide")
st.title("👷 Konfiguracja operatorów")
st.markdown("---")

config = load_config()
skills = get_skill_categories(config)
operators = config.get("operators", [])

# ── Konfiguracja zmian ────────────────────────────────────────────────────────
st.subheader("Konfiguracja zmian")
shift_cfg = config.setdefault("shift_config", {"A_first_shift_week": "2026-04-27"})

from datetime import date
sc1, sc2 = st.columns([2, 5])
current_ref = shift_cfg.get("A_first_shift_week", "2026-04-27")
try:
    ref_date = date.fromisoformat(current_ref)
except Exception:
    ref_date = date.today()

new_ref = sc1.date_input(
    "Zmiana A na 1. zmianie od (poniedziałek)",
    value=ref_date,
    help="Pierwszy poniedziałek tygodnia, kiedy grupa A pracuje na zmianie rannej (6–14).",
)
if str(new_ref) != current_ref:
    config["shift_config"]["A_first_shift_week"] = str(new_ref)
    save_config(config)
    st.success("Zapisano datę referencyjną zmian.")

sc2.caption(
    "**Zmiana 1 (ranna):** 6:00–14:00  ·  **Zmiana 2 (popołudniowa):** 14:00–22:00  ·  "
    "Grupy rotują co tydzień."
)
st.markdown("---")

# ── Dodaj operatora ───────────────────────────────────────────────────────────
st.subheader("Dodaj operatora")
with st.form("dodaj_operatora", clear_on_submit=True):
    col_name, col_btn = st.columns([3, 1])
    with col_name:
        new_name = st.text_input("Imię i nazwisko", placeholder="np. Jan Kowalski")
    with col_btn:
        st.markdown("<br>", unsafe_allow_html=True)
        submitted = st.form_submit_button("➕ Dodaj", use_container_width=True)

    if submitted:
        if not new_name.strip():
            st.error("Podaj imię i nazwisko operatora.")
        elif any(op["name"].lower() == new_name.strip().lower() for op in operators):
            st.error(f"Operator '{new_name.strip()}' już istnieje.")
        else:
            config["operators"].append({
                "name": new_name.strip(),
                "shift_group": "A",
                "skills": {skill: False for skill in skills},
            })
            save_config(config)
            st.success(f"Dodano operatora: {new_name.strip()}")
            st.rerun()

st.markdown("---")

if not operators:
    st.info("Brak operatorów. Dodaj pierwszego operatora powyżej.")
    st.stop()

# ── Grupa zmianowa per operator ───────────────────────────────────────────────
st.subheader("Grupy zmianowe")
st.caption(
    "Przypisz każdego operatora do grupy A lub B. "
    "Maszyny są przypisywane automatycznie przez silnik planowania — "
    "od najbardziej obciążonej."
)

changed = False
cols = st.columns(min(len(operators), 4))
for i, op in enumerate(config["operators"]):
    new_group = cols[i % len(cols)].selectbox(
        op["name"],
        options=["A", "B"],
        index=0 if op.get("shift_group", "A") == "A" else 1,
        key=f"grp_{op['name']}",
    )
    if new_group != op.get("shift_group"):
        op["shift_group"] = new_group
        changed = True

if changed:
    save_config(config)
    st.success("✅ Zapisano grupy zmianowe.")

st.markdown("---")

# ── Macierz kompetencji ───────────────────────────────────────────────────────
st.subheader("Macierz kompetencji")
st.caption("Zaznacz kategorie produktów, które dany operator potrafi obsługiwać.")

matrix_data = {}
for op in operators:
    matrix_data[op["name"]] = {
        skill: bool(op.get("skills", {}).get(skill, False))
        for skill in skills
    }

df_matrix = pd.DataFrame(matrix_data, index=skills)
df_matrix.index.name = "Kategoria produktów"

col_config = {
    op["name"]: st.column_config.CheckboxColumn(op["name"], width="small")
    for op in operators
}

edited_matrix = st.data_editor(
    df_matrix,
    column_config=col_config,
    use_container_width=True,
    key="macierz_kompetencji",
)

col_save, col_del, col_status = st.columns([1, 1, 3])

with col_save:
    if st.button("💾 Zapisz kompetencje", type="primary", use_container_width=True):
        for op in config["operators"]:
            if op["name"] in edited_matrix.columns:
                op["skills"] = {
                    skill: bool(edited_matrix.loc[skill, op["name"]])
                    for skill in skills
                }
        save_config(config)
        with col_status:
            st.success("✅ Kompetencje zapisane!")

with col_del:
    with st.popover("🗑️ Usuń operatora", use_container_width=True):
        to_delete = st.selectbox(
            "Wybierz operatora",
            options=[op["name"] for op in operators],
            key="del_operator",
        )
        if st.button("Potwierdź usunięcie", type="secondary", use_container_width=True):
            config["operators"] = [
                op for op in config["operators"] if op["name"] != to_delete
            ]
            vac = config.get("operator_vacations", {})
            vac.pop(to_delete, None)
            save_config(config)
            st.success(f"Usunięto: {to_delete}")
            st.rerun()

# ── Urlopy ────────────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Urlopy i nieobecności")
st.caption("Zaznacz dni nieobecności danego operatora. Dni te są pomijane przy planowaniu.")

vacations = config.setdefault("operator_vacations", {})

op_names = [op["name"] for op in operators]
sel_op = st.selectbox("Operator", options=op_names, key="urlop_op")

if sel_op:
    op_vac = vacations.get(sel_op, [])
    from datetime import timedelta

    today = date.today()
    vac_rows = []
    for i in range(60):
        d = today + timedelta(days=i)
        vac_rows.append({
            "Data": d.isoformat(),
            "Dzień": ["Pn","Wt","Śr","Cz","Pt","Sb","Nd"][d.weekday()],
            "Nieobecny": d.isoformat() in op_vac,
        })

    vac_df = pd.DataFrame(vac_rows)
    edited_vac = st.data_editor(
        vac_df,
        column_config={
            "Data":      st.column_config.TextColumn("Data", disabled=True, width="small"),
            "Dzień":     st.column_config.TextColumn("Dzień", disabled=True, width="small"),
            "Nieobecny": st.column_config.CheckboxColumn("Nieobecny", width="small"),
        },
        hide_index=True,
        use_container_width=True,
        height=400,
        key="urlopy_editor",
    )

    if st.button("💾 Zapisz urlopy", type="primary"):
        new_vac = edited_vac[edited_vac["Nieobecny"]]["Data"].tolist()
        # Zachowaj daty spoza okna 60 dni
        outside = [d for d in op_vac if d not in vac_df["Data"].values]
        vacations[sel_op] = sorted(set(outside + new_vac))
        config["operator_vacations"] = vacations
        save_config(config)
        st.success(f"✅ Urlopy zapisane dla {sel_op}.")
        st.rerun()

# ── Podsumowanie ──────────────────────────────────────────────────────────────
st.markdown("---")
st.subheader("Podsumowanie")

col1, col2, col3 = st.columns(3)
col1.metric("Liczba operatorów", len(operators))
with_skills = sum(1 for op in operators if any(op.get("skills", {}).get(s, False) for s in skills))
col2.metric("Z kompetencjami", with_skills)
grA = sum(1 for op in operators if op.get("shift_group", "A") == "A")
grB = sum(1 for op in operators if op.get("shift_group", "A") == "B")
col3.metric("Grupa A / B", f"{grA} / {grB}")
