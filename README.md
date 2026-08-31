# Projekt FastAPI + Streamlit

Ten projekt to szkielet aplikacji z użyciem FastAPI (jako backend) i Streamlit (jako frontend).

## Struktura projektu

- `backend/main.py` - Główny plik aplikacji FastAPI.
- `frontend/app.py` - Główny plik aplikacji Streamlit.
- `requirements.txt` - Lista zależności Pythona.

## Uruchamianie projektu

Poniższe instrukcje zakładają, że znajdujesz się w głównym folderze projektu (tam gdzie plik `README.md`).

### 1. Backend (FastAPI)

Otwórz terminal i uruchom serwer FastAPI za pomocą Uvicorn:

```bash
uvicorn backend.main:app --reload
```

Backend będzie działać pod adresem: [http://localhost:8000](http://localhost:8000)
Automatyczna dokumentacja API (Swagger UI): [http://localhost:8000/docs](http://localhost:8000/docs)

### 2. Frontend (Streamlit)

Otwórz **drugi, nowy terminal**, upewnij się, że jesteś w folderze głównym i uruchom:

```bash
streamlit run frontend/app.py
```

Frontend uruchomi się automatycznie w Twojej przeglądarce pod adresem: [http://localhost:8501](http://localhost:8501)
