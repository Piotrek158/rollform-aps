import os

import truststore

truststore.inject_into_ssl()  # firmowe CA z magazynu certyfikatów Windows

import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware

app = FastAPI(title="Project Backend")

# Strona testowa otwierana z dysku (file://) ma origin "null" — wpuszczamy wszystko,
# backend działa tylko lokalnie.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# Dane dostępowe do OperatorPanelAPI — wyłącznie ze zmiennych środowiskowych,
# nigdy na sztywno w kodzie (repozytorium jest współdzielone).
OPERATOR_PANEL_BASE = os.environ.get("OPERATOR_PANEL_BASE", "")
OPERATOR_PANEL_KEY = os.environ.get("OPERATOR_PANEL_KEY", "")


@app.get("/")
def read_root():
    return {"message": "Welcome to the FastAPI Backend"}


@app.get("/api/data")
def get_data():
    # Przykładowe dane do zwrócenia
    return {"data": [10, 20, 30, 40, 50]}


@app.get("/api/proxy/last-programming-orders")
def last_programming_orders(machineName: str, top: int = 5):
    """Proxy do OperatorPanelAPI — omija CORS przy wywołaniach z przeglądarki."""
    if not OPERATOR_PANEL_BASE or not OPERATOR_PANEL_KEY:
        raise HTTPException(
            status_code=503,
            detail="Brak konfiguracji OPERATOR_PANEL_BASE / OPERATOR_PANEL_KEY (zmienne środowiskowe).",
        )
    try:
        r = httpx.get(
            f"{OPERATOR_PANEL_BASE}/api/v1/Reports/LastProgrammingProductionOrders",
            params={"machineName": machineName, "top": top},
            headers={"verification-key": OPERATOR_PANEL_KEY, "Accept": "application/json"},
            timeout=30.0,
        )
    except httpx.RequestError as e:
        raise HTTPException(status_code=502, detail=f"Błąd połączenia z OperatorPanelAPI: {e}")
    if r.status_code != 200:
        raise HTTPException(status_code=r.status_code, detail=r.text)
    return r.json()
