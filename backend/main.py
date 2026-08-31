from fastapi import FastAPI

app = FastAPI(title="Project Backend")

@app.get("/")
def read_root():
    return {"message": "Welcome to the FastAPI Backend"}

@app.get("/api/data")
def get_data():
    # Przykładowe dane do zwrócenia
    return {"data": [10, 20, 30, 40, 50]}
