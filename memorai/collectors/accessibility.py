import json
import logging
import os
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

from memorai.config import config, settings

_FROZEN = getattr(sys, 'frozen', False)
_MEIPASS = Path(getattr(sys, '_MEIPASS', ''))

_POLL_INTERVAL = settings.get('poll_interval')
_AX_SWIFT_SOURCE = Path(__file__).parent.parent / "macos" / "ax_content.swift"
_OCR_SWIFT_SOURCE = Path(__file__).parent.parent / "macos" / "ocr.swift"
_APP_SCRIPT = (
    (_MEIPASS / "macos" / "window_title.applescript")
    if _FROZEN
    else (Path(__file__).parent.parent / "macos" / "window_title.applescript")
)
_MAX_LOG_ROWS = 100_000
_TRIM_EVERY = 1_000
_MIN_CONTENT_LEN = 60
_MIN_DIFF_LEN = 30        # minimum new chars required to write a diff entry
_FULL_REPLACE_RATIO = 0.8 # if diff is >80% of new content, store full text (new page)
_MIN_DURATION_SECS = 4
_SKIP_APPS = {"Finder", "System Preferences", "System Settings", "loginwindow", "Dock", ""}


class AccessibilityCollector:
    def __init__(self) -> None:
        self._ax_binary: Optional[Path] = None
        self._ocr_binary: Optional[Path] = None
        self._last_text: Dict[Tuple[str, str], str] = {}  # (app, window_title) → last full text
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

    def _compile(self, source: Path, output_name: str) -> Optional[Path]:
        if _FROZEN:
            bundled = _MEIPASS / "macos" / output_name
            return bundled if bundled.exists() else None
        binary = config.data_dir / output_name
        if binary.exists() and binary.stat().st_mtime >= source.stat().st_mtime:
            return binary
        if not source.exists():
            return None
        config.data_dir.mkdir(parents=True, exist_ok=True)
        result = subprocess.run(
            ["swiftc", str(source), "-o", str(binary)],
            capture_output=True,
            timeout=60,
        )
        if result.returncode != 0:
            logger.debug("swiftc failed for %s: %s", source.name, result.stderr.decode())
            return None
        return binary

    def _ensure_ax_binary(self) -> Optional[Path]:
        if not self._ax_binary:
            self._ax_binary = self._compile(_AX_SWIFT_SOURCE, "ax_content")
        return self._ax_binary

    def _ensure_ocr_binary(self) -> Optional[Path]:
        if not self._ocr_binary:
            self._ocr_binary = self._compile(_OCR_SWIFT_SOURCE, "ocr")
        return self._ocr_binary

    def _get_app_name_fallback(self) -> Tuple[str, str]:
        try:
            result = subprocess.run(
                ["osascript", _APP_SCRIPT],
                capture_output=True,
                text=True,
                timeout=3,
            )
            if result.returncode != 0:
                return ("", "")
            parts = result.stdout.strip().split("|||", 1)
            return (parts[0].strip(), parts[1].strip() if len(parts) > 1 else "")
        except (subprocess.TimeoutExpired, OSError):
            return ("", "")

    def _get_ax_content(self) -> Optional[Dict]:
        binary = self._ensure_ax_binary()
        if not binary:
            return None
        try:
            result = subprocess.run(
                [str(binary)],
                capture_output=True,
                text=True,
                timeout=5,
            )
            if result.returncode != 0:
                return None
            raw = result.stdout.strip()
            if not raw:
                return None
            return json.loads(raw)
        except (subprocess.TimeoutExpired, OSError, json.JSONDecodeError):
            return None

    @staticmethod
    def _reconstruct_layout(observations: List[Dict]) -> str:
        if not observations:
            return ""

        def x_mid(o: Dict) -> float: return o["x"] + o["w"] / 2
        def y_mid(o: Dict) -> float: return o["y"] + o["h"] / 2

        def gap_splits(midpoints: List[float], min_gap: float) -> List[float]:
            pts = sorted(midpoints)
            splits = []
            for i in range(1, len(pts)):
                if pts[i] - pts[i - 1] > min_gap:
                    splits.append((pts[i - 1] + pts[i]) / 2)
            return splits

        def assign(val: float, splits: List[float]) -> int:
            return sum(1 for s in splits if val > s)

        col_splits = gap_splits([x_mid(o) for o in observations], min_gap=0.02)

        cols: Dict[int, List] = {}
        for obs in observations:
            cols.setdefault(assign(x_mid(obs), col_splits), []).append(obs)

        section_parts = []
        for ci in sorted(cols):
            col_obs = cols[ci]

            row_splits = gap_splits(
                sorted([y_mid(o) for o in col_obs], reverse=True),
                min_gap=0.03,
            )
            row_splits_asc = sorted(row_splits)

            def row_idx(o: Dict) -> int:
                ym = y_mid(o)
                return sum(1 for s in row_splits_asc if ym < s)

            rows: Dict[int, List] = {}
            for obs in col_obs:
                rows.setdefault(row_idx(obs), []).append(obs)

            col_lines = []
            for ri in sorted(rows):
                row_obs = sorted(rows[ri], key=lambda o: -y_mid(o))
                lines: List[str] = []
                current: List[Dict] = []
                for obs in row_obs:
                    if not current or abs(y_mid(current[-1]) - y_mid(obs)) <= 0.015:
                        current.append(obs)
                    else:
                        lines.append(" ".join(
                            o["text"] for o in sorted(current, key=lambda o: o["x"])
                        ))
                        current = [obs]
                if current:
                    lines.append(" ".join(
                        o["text"] for o in sorted(current, key=lambda o: o["x"])
                    ))
                col_lines.extend(lines)
                if ri < max(rows):
                    col_lines.append("---")

            section_parts.append("\n".join(col_lines))

        return "\n\n===\n\n".join(section_parts)[:4000]

    def _take_ocr_screenshot(
        self,
        app_name: str,
        window_title: str,
        frame: Optional[Dict] = None,
    ) -> Optional[Dict]:
        binary = self._ensure_ocr_binary()
        if not binary:
            return None
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            cap_cmd = ["screencapture", "-x"]
            if frame and frame.get("w", 0) > 0 and frame.get("h", 0) > 0:
                pad = 50
                x = max(0, int(frame["x"]) - pad)
                y = max(0, int(frame["y"]) - pad)
                w = int(frame["w"]) + pad * 2
                h = int(frame["h"]) + pad * 2
                cap_cmd += ["-R", f"{x},{y},{w},{h}"]
            cap_cmd.append(tmp_path)
            cap = subprocess.run(
                cap_cmd,
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
            raw = ocr.stdout.strip()
            if not raw:
                return None
            try:
                observations = json.loads(raw)
                text = self._reconstruct_layout(observations)
            except (json.JSONDecodeError, TypeError, KeyError):
                text = raw[:4000]
            if not text:
                return None
            return {"app": app_name, "window_title": window_title, "text": text}
        except (subprocess.TimeoutExpired, OSError):
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _get_screen_content(self) -> Optional[Dict]:
        ax = self._get_ax_content()
        if ax:
            app_name = ax.get("app", "")
            window_title = ax.get("window_title", "")
            source = ax.get("source", "ocr_needed")
            logger.debug("AX result: app=%s source=%s", app_name, source)

            if source != "ocr_needed":
                content = ax.get("content", "").strip()
                if content:
                    return {"app": app_name, "window_title": window_title, "text": content}

            # AX binary ran but content requires OCR; use app/window and frame from AX
            return self._take_ocr_screenshot(app_name, window_title, frame=ax.get("frame"))

        # AX binary unavailable — fall back to applescript for app name + OCR
        app_name, window_title = self._get_app_name_fallback()
        return self._take_ocr_screenshot(app_name, window_title)

    @staticmethod
    def _content_diff(old: str, new: str) -> str:
        """Return lines that appear in `new` but not in `old`."""
        old_lines = set(old.splitlines())
        added = [line for line in new.splitlines() if line.strip() and line not in old_lines]
        return "\n".join(added)

    def record_current_screen(self) -> None:
        data = self._get_screen_content()
        if not data:
            logger.debug("No screen content captured")
            return
        app = data["app"]
        window_title = data["window_title"]
        if app in _SKIP_APPS:
            logger.debug("Skipping capture for noise app: %s", app)
            return
        text = data["text"]
        if len(text.strip()) < _MIN_CONTENT_LEN:
            logger.debug("Skipping capture: content too short (%d chars)", len(text.strip()))
            return

        key = (app, window_title)
        last = self._last_text.get(key, "")

        if last:
            diff = self._content_diff(last, text)
            if len(diff) < _MIN_DIFF_LEN:
                self._last_text[key] = text
                return
            diff_ratio = len(diff) / max(len(text), 1)
            is_diff = diff_ratio <= _FULL_REPLACE_RATIO
            raw_for_embed = diff if is_diff else text
        else:
            raw_for_embed = text
            is_diff = False

        self._last_text[key] = text
        config.data_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "timestamp": datetime.now(timezone.utc).isoformat(),
            "app": app,
            "window_title": window_title,
            "text": raw_for_embed,
            "is_diff": is_diff,
            "full_content_chars": len(text),
        }
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

            if duration < _MIN_DURATION_SECS:
                continue

            text = obj.get("text", "")
            if not text:
                continue

            app = obj.get("app", "")
            window_title = obj.get("window_title", "")

            if window_title:
                raw_content = f"{app} — {window_title}: {text}"
            else:
                raw_content = f"{app}: {text}"

            meta: Dict = {
                "app_name": app,
                "window_title": window_title,
                "duration_seconds": duration,
            }
            if obj.get("is_diff"):
                meta["is_diff"] = True
                meta["full_content_chars"] = obj.get("full_content_chars", len(text))

            events.append({
                "timestamp": ts,
                "source": "accessibility",
                "raw_content": raw_content,
                "metadata": meta,
            })

        return events
