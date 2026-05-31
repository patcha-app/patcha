import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple

from patcha.config import config
from patcha.db.models import Event, EventType
from patcha.utils.jsonl import trim_jsonl

_MAX_LOG_ROWS = 100_000
_TRIM_EVERY = 1_000


class WindowCollector:
    """Tracks active macOS app and window title via osascript.

    requires accessibility permission:
    System Settings > Privacy & Security > Accessibility → enable terminal/patcha.
    """

    _SCRIPT = Path(__file__).parent.parent / "macos" / "window_title.applescript"

    def __init__(self):
        self.log_file: Path = config.data_dir / "window_log.jsonl"
        self._write_count: int = 0

    def _get_active_window(self) -> Tuple[str, str]:
        try:
            result = subprocess.run(
                ["osascript", self._SCRIPT],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode == 0:
                parts = result.stdout.strip().split("|||")
                app = parts[0].strip()
                title = parts[1].strip() if len(parts) > 1 else ""
                return app, title
        except (subprocess.TimeoutExpired, OSError):
            pass
        return "", ""

    def record_current_window(self) -> None:
        """Snapshot the active window and append to the log file."""
        app, title = self._get_active_window()
        if not app:
            return

        config.data_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "app": app,
            "title": title,
        }
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")

        self._write_count += 1
        if self._write_count % _TRIM_EVERY == 0:
            trim_jsonl(self.log_file, _MAX_LOG_ROWS)

    def collect_windows(self, since: datetime) -> List[Event]:
        """Return completed window-focus sessions logged since `since`.

        A session ends when the active app or window title changes. The
        currently active (still-open) session is excluded since its
        duration is not yet known.
        """
        if not self.log_file.exists():
            return []

        entries: List[Tuple[datetime, str, str]] = []
        with open(self.log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    ts = datetime.fromisoformat(obj["timestamp"])
                    if ts >= since:
                        entries.append((ts, obj["app"], obj.get("title", "")))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        if len(entries) < 2:
            return []

        events: List[Event] = []
        session_start, session_app, session_title = entries[0]

        for ts, app, title in entries[1:]:
            if app != session_app or title != session_title:
                duration = int((ts - session_start).total_seconds())
                if duration >= 30:
                    events.append(
                        self._make_event(
                            session_start, session_app, session_title, duration
                        )
                    )
                session_start, session_app, session_title = ts, app, title

        return events

    def _make_event(
        self, timestamp: datetime, app: str, title: str, duration_seconds: int
    ) -> Event:
        label = title if title else app
        raw = f"{app}: {label} ({duration_seconds}s)"
        return Event(
            timestamp=timestamp,
            type=EventType.WINDOW,
            source="window",
            raw_content=raw,
            metadata={
                "app_name": app,
                "window_title": title,
                "duration_seconds": duration_seconds,
            },
        )
