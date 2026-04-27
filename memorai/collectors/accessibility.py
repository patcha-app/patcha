import json
import logging
import os
import subprocess
import tempfile
import threading
from datetime import datetime, timezone
from difflib import SequenceMatcher
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from memorai.config import config, settings

_POLL_INTERVAL = settings.get('poll_interval')
_SWIFT_SOURCE = Path(__file__).parent.parent / "appscripts" / "ocr.swift"
_APP_SCRIPT = Path(__file__).parent.parent / "appscripts" / "window_title.applescript"
_MAX_LOG_ROWS = 100_000
_TRIM_EVERY = 1_000
_SIMILARITY_THRESHOLD = 0.85


class AccessibilityCollector:
    def __init__(self) -> None:
        self._binary: Optional[Path] = None
        self._last_text = ""
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.log_file: Path = config.data_dir / "screen_log.jsonl"
        self._line_count: int = self._count_existing_lines()
        self._write_count: int = 0

    def _count_existing_lines(self) -> int:
        if not self.log_file.exists():
            return 0
        count = 0
        try:
            with open(self.log_file, "rb") as f:
                for _ in f:
                    count += 1
        except OSError:
            return 0
        return count

    def _ensure_binary(self) -> Optional[Path]:
        if self._binary:
            return self._binary
        binary = config.data_dir / "ocr"
        if binary.exists():
            self._binary = binary
            return binary

        if not _SWIFT_SOURCE.exists():
            return None

        config.data_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["swiftc", str(_SWIFT_SOURCE), "-o", str(binary)],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            return None
        self._binary = binary
        return binary

    def _get_app_name(self) -> str:
        try:
            result = subprocess.run(
                ["osascript", _APP_SCRIPT],
                capture_output=True,
                text=True,
                timeout=3,
            )
            return result.stdout.strip() if result.returncode == 0 else ""
        except (subprocess.TimeoutExpired, OSError):
            return ""

    def _get_ocr_text(self) -> Optional[Dict]:
        binary = self._ensure_binary()
        if not binary:
            return None

        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            cap = subprocess.run(
                ["screencapture", "-x", tmp_path],
                capture_output=True,
                timeout=5,
            )
            if cap.returncode != 0 or not os.path.getsize(tmp_path):
                return None

            ocr = subprocess.run(
                [str(binary), tmp_path],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if ocr.returncode != 0:
                return None

            text = ocr.stdout.strip()
            if not text:
                return None

            return {"app": self._get_app_name(), "text": text}
        except (subprocess.TimeoutExpired, OSError):
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def record_current_screen(self) -> None:
        data = self._get_ocr_text()
        if not data:
            logger.debug("No OCR data captured from screen")
            return
        if data["text"] == self._last_text:
            return
        if self._last_text:
            ratio = SequenceMatcher(None, self._last_text, data["text"]).ratio()
            if ratio >= _SIMILARITY_THRESHOLD:
                return
        self._last_text = data["text"]
        config.data_dir.mkdir(parents=True, exist_ok=True)
        entry = {"timestamp": datetime.now(timezone.utc).isoformat(), **data}
        with open(self.log_file, "a") as f:
            f.write(json.dumps(entry) + "\n")
        self._line_count += 1
        self._write_count += 1
        if self._write_count % _TRIM_EVERY == 0 and self._line_count > _MAX_LOG_ROWS:
            self._trim_log()

    def _trim_log(self) -> None:
        try:
            with open(self.log_file, "rb") as f:
                lines = f.readlines()
            if len(lines) <= _MAX_LOG_ROWS:
                self._line_count = len(lines)
                return
            keep = lines[-_MAX_LOG_ROWS:]
            tmp = self.log_file.with_suffix(".jsonl.tmp")
            with open(tmp, "wb") as f:
                f.writelines(keep)
            tmp.replace(self.log_file)
            self._line_count = _MAX_LOG_ROWS
        except OSError:
            pass

    def _run_loop(self) -> None:
        while not self._stop_event.wait(_POLL_INTERVAL):
            try:
                self.record_current_screen()
            except Exception:
                pass

    def start_recording(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop_event.clear()
        self._thread = threading.Thread(target=self._run_loop, daemon=True)
        self._thread.start()

    def stop_recording(self) -> None:
        self._stop_event.set()

    def collect_screen_text(self, since: datetime) -> List:
        if not self.log_file.exists():
            return []

        entries: List[Tuple] = []
        with open(self.log_file) as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                    ts = datetime.fromisoformat(obj["timestamp"])
                    if ts >= since:
                        entries.append((ts, obj))
                except (json.JSONDecodeError, KeyError, ValueError):
                    continue

        now = datetime.now(timezone.utc)
        events = []
        for i, (ts, obj) in enumerate(entries):
            end_ts = entries[i + 1][0] if i + 1 < len(entries) else now
            duration = int((end_ts - ts).total_seconds())

            text = obj.get("text", "")
            if not text:
                continue

            app = obj.get("app", "")
            events.append({
                "timestamp": ts,
                "source": "accessibility",
                "raw_content": f"{app}: {text}",
                "metadata": {
                    "app_name": app,
                    "duration_seconds": duration,
                },
            })

        return events
