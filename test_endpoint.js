// Wywołanie idzie przez lokalny backend FastAPI (proxy w backend/main.py),
// bo OperatorPanelAPI nie zwraca nagłówków CORS — bezpośredni fetch
// z przeglądarki kończy się "Failed to fetch". Backend musi działać:
//   uvicorn backend.main:app --reload
const BASE = "http://localhost:8000";

async function lastProgrammingOrders(machineName, top = 5) {
  const url = new URL(`${BASE}/api/proxy/last-programming-orders`);
  url.searchParams.set("machineName", machineName);
  url.searchParams.set("top", String(top));

  const res = await fetch(url, {
    method: "GET",
    headers: { Accept: "application/json" },
  });

  if (!res.ok) {
    throw new Error(`HTTP ${res.status}: ${await res.text()}`);
  }
  return res.json();
}

// użycie (odkomentuj do testu w konsoli — w test_endpoint.html wywołuje przycisk)
// lastProgrammingOrders("NAZWA_MASZYNY", 5)
//   .then(data => console.log(data))
//   .catch(err => console.error(err));