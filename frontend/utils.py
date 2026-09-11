import json
import os
import shutil
from datetime import datetime, date
from pathlib import Path
import numpy as np
import pandas as pd


class _Encoder(json.JSONEncoder):
    """Konwertuje typy numpy/pandas na natywne Python przed zapisem do JSON."""
    def default(self, obj):
        if isinstance(obj, np.integer):
            return int(obj)
        if isinstance(obj, np.floating):
            return None if np.isnan(obj) else float(obj)
        if isinstance(obj, np.bool_):
            return bool(obj)
        if isinstance(obj, np.ndarray):
            return obj.tolist()
        if isinstance(obj, pd.Timestamp):
            return obj.isoformat() if not pd.isna(obj) else None
        if isinstance(obj, (datetime, date)):
            return obj.isoformat()
        return super().default(obj)

_BACKEND_DIR = Path(__file__).parent.parent / "backend"
# Lokalna konfiguracja (poza kontrolą wersji) ma pierwszeństwo przed wersją z repo —
# pozwala trzymać prawdziwe dane zasobów bez ich commitowania.
CONFIG_PATH = (
    _BACKEND_DIR / "config.local.json"
    if (_BACKEND_DIR / "config.local.json").exists()
    else _BACKEND_DIR / "config.json"
)
BACKUP_PATH = CONFIG_PATH.with_suffix(".json.bak")


def load_config() -> dict:
    # Spróbuj główny plik, przy błędzie wczytaj backup
    for path in [CONFIG_PATH, BACKUP_PATH]:
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, FileNotFoundError):
            continue
    raise RuntimeError(f"Nie można wczytać konfiguracji z {CONFIG_PATH} ani z backupu.")


def save_config(config: dict) -> None:
    # 1. Zapis do pliku tymczasowego
    tmp_path = CONFIG_PATH.with_suffix(".json.tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2, cls=_Encoder)

    # 2. Weryfikacja że plik tymczasowy jest poprawnym JSON
    with open(tmp_path, "r", encoding="utf-8") as f:
        json.load(f)

    # 3. Backup poprzedniej wersji
    if CONFIG_PATH.exists():
        shutil.copy2(CONFIG_PATH, BACKUP_PATH)

    # 4. Atomowe zastąpienie
    os.replace(tmp_path, CONFIG_PATH)


def get_or_init_calendar(config: dict, days: int = 60) -> dict:
    """Zwraca kalendarz z config. Brakujące dni uzupełnia domyślnymi wartościami."""
    from datetime import date, timedelta
    cal = config.setdefault("calendar", {})
    today = date.today()
    for i in range(days):
        d = today + timedelta(days=i)
        key = d.isoformat()
        if key not in cal:
            cal[key] = 0 if d.weekday() >= 5 else 960  # sob/nd = 0, pon-pt = 960
    return cal


def get_machines(config: dict, zaklad: str) -> list:
    return [m for m in config["machines"] if m["zaklad"] == zaklad]


def get_skill_categories(config: dict) -> list:
    return config.get("skill_categories", [])


# ── Plan versioning ────────────────────────────────────────────────────────────

PLANS_DIR = CONFIG_PATH.parent / "plans"


def list_plans() -> list[dict]:
    """Returns metadata list for all saved plans, newest first."""
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    plans = []
    for f in PLANS_DIR.glob("*.json"):
        try:
            with open(f, "r", encoding="utf-8") as fp:
                meta = json.load(fp)
            plans.append({
                "timestamp": meta.get("timestamp", f.stem),
                "label":     meta.get("label", f.stem),
                "filename":  f.name,
            })
        except Exception:
            continue
    return sorted(plans, key=lambda x: x["timestamp"], reverse=True)


def load_plan(timestamp: str):
    """Load a saved plan DataFrame by its timestamp string."""
    import pandas as pd
    path = PLANS_DIR / f"{timestamp}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    df = pd.DataFrame(data["rows"])
    df["Start"]  = pd.to_datetime(df["Start"])
    df["Koniec"] = pd.to_datetime(df["Koniec"])
    return df


def load_plan_zonki(timestamp: str):
    """Wczytuje listę zonków zapisaną razem z planem (może być pusta)."""
    import pandas as pd
    path = PLANS_DIR / f"{timestamp}.json"
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return pd.DataFrame(data.get("zonki", []))


def overwrite_plan(timestamp: str, df) -> None:
    """Nadpisuje istniejący plan (zachowuje label i timestamp)."""
    path = PLANS_DIR / f"{timestamp}.json"
    with open(path, "r", encoding="utf-8") as f:
        existing = json.load(f)
    out = df.copy()
    for col in ("Start", "Koniec"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S")
    existing["rows"] = out.to_dict(orient="records")
    tmp = PLANS_DIR / f"{timestamp}.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(existing, f, ensure_ascii=False, indent=2, cls=_Encoder)
    os.replace(tmp, path)


def save_plan(df, label: str = "", zonki: list | None = None) -> str:
    """Serialize a plan DataFrame to backend/plans/<timestamp>.json. Returns timestamp."""
    from datetime import datetime as _dt
    PLANS_DIR.mkdir(parents=True, exist_ok=True)
    ts = _dt.now().strftime("%Y-%m-%dT%H-%M-%S")
    out = df.copy()
    for col in ("Start", "Koniec"):
        if col in out.columns:
            out[col] = pd.to_datetime(out[col], errors="coerce").dt.strftime("%Y-%m-%dT%H:%M:%S")
    payload = {
        "timestamp":  ts,
        "created_at": _dt.now().isoformat(),
        "label":      label or ts,
        "rows":       out.to_dict(orient="records"),
        "zonki":      zonki or [],
    }
    path = PLANS_DIR / f"{ts}.json"
    with open(path, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False, indent=2, cls=_Encoder)
    return ts
