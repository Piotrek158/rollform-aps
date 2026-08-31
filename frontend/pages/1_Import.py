import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import streamlit as st
import pandas as pd

st.set_page_config(page_title="Import danych", page_icon="📥", layout="wide")
st.title("📥 Import danych")
st.markdown("---")

# --- Wybór zakładu ---
st.subheader("1. Wybierz zakład")
zaklad = st.selectbox(
    "Zakład produkcyjny",
    options=["Zabornia", "Sękocin"],
    key="zaklad_globalny",
)
st.session_state["zaklad"] = zaklad

# --- Upload pliku ---
st.subheader("2. Wczytaj plik xlsx")
uploaded = st.file_uploader("Wybierz plik Excel ze zleceniami", type=["xlsx"])

if uploaded is not None:
    try:
        df_raw = pd.read_excel(uploaded, dtype=str)

        # Walidacja wymaganych kolumn
        required = {"Nazwa_zakladu_Grupa", "Twr_Profil", "Ilosc_MBSZT", "LD", "Twr_Katalog", "Numer_zlecenia"}
        missing = required - set(df_raw.columns)
        if missing:
            st.error(f"Brakujące kolumny w pliku: {', '.join(missing)}")
            st.stop()

        # Filtruj po zakładzie
        df = df_raw[df_raw["Nazwa_zakladu_Grupa"].str.strip() == zaklad].copy()
        st.success(f"Wczytano {len(df_raw)} wierszy. Po filtrowaniu dla **{zaklad}**: **{len(df)}** zleceń.")

        if len(df) == 0:
            st.warning(f"Brak zleceń dla zakładu {zaklad} w tym pliku.")
            st.stop()

        # Konwersja Ilosc_MBSZT
        bledy_mb = []
        def parse_mb(val):
            try:
                return float(str(val).replace(",", "."))
            except Exception:
                return None

        df["Ilosc_MBSZT_num"] = df["Ilosc_MBSZT"].apply(parse_mb)
        bledy_mb = df[df["Ilosc_MBSZT_num"].isna()]["Numer_zlecenia"].tolist()

        if bledy_mb:
            st.warning(f"⚠️ Nie można odczytać Ilosc_MBSZT dla {len(bledy_mb)} zleceń — zostaną pominięte: {bledy_mb[:5]}{'...' if len(bledy_mb) > 5 else ''}")
            df = df[df["Ilosc_MBSZT_num"].notna()].copy()

        # Konwersja LD
        df["LD_date"] = pd.to_datetime(df["LD"], errors="coerce")
        bledy_ld = df[df["LD_date"].isna()]["Numer_zlecenia"].tolist()
        if bledy_ld:
            st.warning(f"⚠️ Nie można odczytać daty LD dla {len(bledy_ld)} zleceń — zostaną pominięte: {bledy_ld[:5]}{'...' if len(bledy_ld) > 5 else ''}")
            df = df[df["LD_date"].notna()].copy()

        # Typ zamówienia (wew/zew) — czytane z Zrodlo_zam (wewnetrzne/zewnetrzne)
        if "Zrodlo_zam" in df.columns:
            zr = df["Zrodlo_zam"].fillna("").astype(str).str.lower().str.strip()
            df["Typ_zamowienia"] = zr.map({"wewnetrzne": "wew", "zewnetrzne": "zew"})
            invalid_mask = df["Typ_zamowienia"].isna()
            if invalid_mask.any():
                bad_vals = zr[invalid_mask].replace("", "(puste)").unique().tolist()
                st.warning(
                    f"⚠️ Nieznane wartości w `Zrodlo_zam` (potraktowane jako zewnętrzne): "
                    f"{bad_vals[:5]}{'...' if len(bad_vals) > 5 else ''}"
                )
                df.loc[invalid_mask, "Typ_zamowienia"] = "zew"
        else:
            df["Typ_zamowienia"] = "zew"
            st.info("ℹ️ Brak kolumny `Zrodlo_zam` w pliku — wszystkie zlecenia traktowane jako zewnętrzne.")

        # Zapisz do session_state
        st.session_state["dane"] = df
        st.session_state["zaklad"] = zaklad

        # Podsumowanie
        st.markdown("---")
        st.subheader("3. Podgląd danych")

        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Łącznie zleceń", len(df))
        col2.metric("Unikalnych Twr_Profil", df["Twr_Profil"].nunique())
        col3.metric("Unikalnych Twr_Katalog", df["Twr_Katalog"].nunique())
        col4.metric("Łączne MB", f"{df['Ilosc_MBSZT_num'].sum():,.1f}")

        n_wew = int((df["Typ_zamowienia"] == "wew").sum())
        n_zew = int((df["Typ_zamowienia"] == "zew").sum())
        mb_wew = float(df.loc[df["Typ_zamowienia"] == "wew", "Ilosc_MBSZT_num"].sum())
        mb_zew = float(df.loc[df["Typ_zamowienia"] == "zew", "Ilosc_MBSZT_num"].sum())
        cw1, cw2, cw3, cw4 = st.columns(4)
        cw1.metric("Zlecenia zew", n_zew)
        cw2.metric("Zlecenia wew", n_wew)
        cw3.metric("MB zew", f"{mb_zew:,.1f}")
        cw4.metric("MB wew", f"{mb_wew:,.1f}")

        st.markdown("##### Rozkład terminów (LD)")
        ld_counts = df.groupby(df["LD_date"].dt.date).size().reset_index(name="Liczba zleceń")
        ld_counts.columns = ["Data LD", "Liczba zleceń"]
        st.bar_chart(ld_counts.set_index("Data LD"))

        st.markdown("##### Rozkład po Twr_Profil")
        profil_counts = df["Twr_Profil"].value_counts().reset_index()
        profil_counts.columns = ["Twr_Profil", "Liczba"]
        st.dataframe(profil_counts, use_container_width=True, hide_index=True)

        st.markdown("##### Pełne dane")
        display_cols = ["Numer_zlecenia", "Twr_Profil", "Twr_Katalog", "Ilosc_MBSZT", "LD", "Typ_zamowienia"]
        st.dataframe(df[display_cols], use_container_width=True, hide_index=True)

    except Exception as e:
        st.error(f"Błąd podczas wczytywania pliku: {e}")

elif "dane" in st.session_state:
    st.info(f"✅ Dane już wczytane dla zakładu **{st.session_state.get('zaklad', '—')}** — {len(st.session_state['dane'])} zleceń. Możesz wczytać nowy plik lub przejść dalej.")
else:
    st.info("Wczytaj plik xlsx ze zleceniami produkcyjnymi.")
