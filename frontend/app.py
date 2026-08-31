import streamlit as st

st.set_page_config(
    page_title="Planner Produkcji",
    page_icon="🏭",
    layout="wide",
)

st.title("🏭 Planner Produkcji")
st.markdown("---")

st.markdown("""
### Nawigacja

Użyj menu po lewej stronie, aby przejść do poszczególnych sekcji:

| Strona | Opis |
|--------|------|
| 📥 **Import** | Wczytaj plik xlsx ze zleceniami, wybierz zakład |
| ⚙️ **Maszyny** | Konfiguruj parametry maszyn (prędkość, limity, przezbrojenie) |
| 👷 **Operatorzy** | Zarządzaj operatorami i ich macierzą kompetencji |
| 📅 **Planner** | Uruchom planowanie i przeglądaj wyniki |
""")

st.info("Zacznij od strony **Import** — wczytaj plik xlsx i wybierz zakład produkcyjny.")
