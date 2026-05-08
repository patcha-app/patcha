import json
import sqlite3
from datetime import datetime, timezone

import pytest

from patcha.db.models import Event, EventType


@pytest.fixture
def ts():
    return datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def sample_event(ts):
    return Event(
        timestamp=ts,
        type=EventType.BROWSER,
        source="chrome",
        raw_content=json.dumps(
            {
                "title": "GitHub",
                "url": "https://github.com",
                "domain": "github.com",
                "duration": 0,
            }
        ),
        metadata={"domain": "github.com", "browser": "chrome"},
    )


@pytest.fixture
def ocr_observations():
    return [
        {"text": "File", "x": 0.05, "y": 0.98, "w": 0.04, "h": 0.01},
        {"text": "Edit", "x": 0.10, "y": 0.98, "w": 0.04, "h": 0.01},
        {"text": "View", "x": 0.15, "y": 0.98, "w": 0.04, "h": 0.01},
        {"text": "def foo():", "x": 0.05, "y": 0.70, "w": 0.20, "h": 0.01},
        {"text": "return 42", "x": 0.07, "y": 0.68, "w": 0.15, "h": 0.01},
    ]


@pytest.fixture
def chrome_db(tmp_path):
    db = tmp_path / "History"
    conn = sqlite3.connect(db)
    conn.execute(
        "CREATE TABLE urls (id INTEGER PRIMARY KEY, title TEXT, url TEXT, "
        "last_visit_time INTEGER, visit_count INTEGER)"
    )
    chrome_ts = (
        int(datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp() * 1_000_000)
        + 11_644_473_600_000_000
    )
    conn.execute(
        "INSERT INTO urls VALUES (1,'GitHub','https://github.com',?,1)", (chrome_ts,)
    )
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def safari_db(tmp_path):
    db = tmp_path / "History.db"
    conn = sqlite3.connect(db)
    conn.execute("CREATE TABLE history_items (id INTEGER PRIMARY KEY, url TEXT)")
    conn.execute(
        "CREATE TABLE history_visits "
        "(id INTEGER PRIMARY KEY, history_item INTEGER, title TEXT, visit_time REAL)"
    )
    ts = datetime(2026, 1, 1, 12, tzinfo=timezone.utc).timestamp()
    conn.execute("INSERT INTO history_items VALUES (1,'https://github.com')")
    conn.execute("INSERT INTO history_visits VALUES (1,1,'GitHub',?)", (ts,))
    conn.commit()
    conn.close()
    return db


@pytest.fixture
def zsh_history_file(tmp_path):
    content = (
        ": 1735732800:0;git status\n"
        ": 1735732860:0;uv run pytest\n"
        ": 1735732920:0;cd patcha\n"
    )
    f = tmp_path / ".zsh_history"
    f.write_text(content)
    return f


@pytest.fixture
def screen_log(tmp_path):
    log = tmp_path / "screen_log.jsonl"
    entries = [
        {
            "timestamp": "2026-01-01T12:00:00+00:00",
            "app": "VSCode",
            "window_title": "main.py",
            "text": "def hello(): pass",
            "is_diff": False,
            "full_content_chars": 17,
        },
        {
            "timestamp": "2026-01-01T12:00:10+00:00",
            "app": "VSCode",
            "window_title": "main.py",
            "text": "return 42",
            "is_diff": True,
            "full_content_chars": 26,
        },
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries))
    return log
