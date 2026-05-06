# Load from settings sqlite or use default
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path

_DB_PATH = Path.home() / "Library" / "Application Support" / "patcha" / "settings.db"


@dataclass
class _Defaults:
    poll_interval: int = 60
    ax_poll_interval: int = 5
    enable_git: bool = True
    enable_browser: bool = True
    enable_terminal: bool = True
    enable_window: bool = True
    enable_accessibility: bool = True


_defaults: dict = asdict(_Defaults())


def _connect() -> sqlite3.Connection:
    _DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(_DB_PATH)
    conn.execute(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)"
    )
    conn.commit()
    return conn


def get(key: str):
    try:
        with _connect() as conn:
            row = conn.execute(
                "SELECT value FROM settings WHERE key = ?", (key,)
            ).fetchone()
            if row:
                return json.loads(row[0])
    except Exception:
        pass
    return _defaults.get(key)


def put(key: str, value) -> None:
    with _connect() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO settings (key, value) VALUES (?, ?)",
            (key, json.dumps(value)),
        )


def all() -> dict:
    merged = dict(_defaults)
    try:
        with _connect() as conn:
            for key, value in conn.execute("SELECT key, value FROM settings"):
                merged[key] = json.loads(value)
    except Exception:
        pass
    return merged
