import ctypes
import ctypes.util
import hashlib
import json
import logging
import os
import statistics
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from patcha.config import config, settings
from patcha.collectors.filters import is_banking_domain, is_incognito_window

logger = logging.getLogger(__name__)


def _has_screen_recording_permission() -> bool:
    try:
        cg = ctypes.CDLL(ctypes.util.find_library("CoreGraphics"))
        if not hasattr(cg, "CGPreflightScreenCaptureAccess"):
            return True
        cg.CGPreflightScreenCaptureAccess.restype = ctypes.c_bool
        return bool(cg.CGPreflightScreenCaptureAccess())
    except Exception:
        return True


_FROZEN = getattr(sys, "frozen", False)
_MEIPASS = Path(getattr(sys, "_MEIPASS", ""))

_POLL_INTERVAL = settings.get("ax_poll_interval")
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
_MIN_DIFF_LEN = 30  # minimum new chars required to write a diff entry
_FULL_REPLACE_RATIO = 0.8  # if diff is >80% of new content, store full text (new page)
_MIN_DURATION_SECS = 4
_MAX_WINDOW_KEYS = 1_000  # cap per-window state dicts to bound memory


def _bounded_set(d: dict, key, value, cap: int = _MAX_WINDOW_KEYS) -> None:
    """Set d[key]=value, evicting the oldest entry if a new key exceeds cap."""
    if key not in d and len(d) >= cap:
        d.pop(next(iter(d)))
    d[key] = value


_SKIP_APPS_BASE = {
    "Finder",
    "System Preferences",
    "System Settings",
    "loginwindow",
    "Dock",
    "",
    "1Password",
    "1Password 7 - Password Manager",
    "Bitwarden",
    "LastPass",
    "Dashlane",
    "Keychain Access",
    "KeePassXC",
    "NordPass",
    "Enpass",
    "Keeper Password Manager",
    "RoboForm",
}


def _build_skip_apps() -> set:
    base = set(_SKIP_APPS_BASE)
    excluded = settings.get("excluded_app_names") or ""
    if excluded:
        base |= {n.strip() for n in excluded.split(",") if n.strip()}
    return base


class AccessibilityCollector:
    def __init__(self) -> None:
        self._ax_binary: Optional[Path] = None
        self._ocr_binary: Optional[Path] = None
        self._last_text: Dict[
            Tuple[str, str], str
        ] = {}  # (app, window_title) → last full text
        self._last_active_key: Optional[Tuple[str, str]] = None
        self._thread: Optional[threading.Thread] = None
        self._stop_event = threading.Event()
        self.log_file: Path = config.data_dir / "screen_log.jsonl"
        self._line_count: int = self._count_existing_lines()
        self._write_count: int = 0
        self._screenshot_hashes: Dict[Tuple[str, str], str] = {}

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
            logger.debug(
                "swiftc failed for %s: %s", source.name, result.stderr.decode()
            )
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

    def _get_ax_content(self, frame_only: bool = False) -> Optional[Dict]:
        binary = self._ensure_ax_binary()
        if not binary:
            return None
        args = [str(binary)]
        if frame_only:
            args.append("--frame-only")
        try:
            result = subprocess.run(
                args,
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
    def _dedup_observations(observations: List[Dict], dist: float) -> List[Dict]:
        deduped: List[Dict] = []
        for obs in observations:
            ox, oy = obs["x"] + obs["w"] / 2, obs["y"] + obs["h"] / 2
            duplicate = False
            for kept in deduped:
                if kept["text"] == obs["text"]:
                    kx = kept["x"] + kept["w"] / 2
                    ky = kept["y"] + kept["h"] / 2
                    if abs(kx - ox) < dist and abs(ky - oy) < dist:
                        duplicate = True
                        break
            if not duplicate:
                deduped.append(obs)
        return deduped

    @staticmethod
    def _detect_column_splits(observations: List[Dict], unit: float) -> List[float]:
        """Find x-positions of vertical gutters separating side-by-side panes (columns).

        Uses a token-coverage projection: bin the x-axis and count how many tokens span each bin.
        A real gutter is a contiguous low-coverage run that few tokens cross — so a full-width
        header (e.g. a search bar) crossing it doesn't merge the columns. Returns the gutter
        midpoints (interior only), used to partition tokens into columns read in left-to-right
        order. Zoom-invariant: bins are relative and the threshold is a fraction of peak coverage.
        """
        x0 = min(o["x"] for o in observations)
        x1 = max(o["x"] + o["w"] for o in observations)
        width = x1 - x0
        if width < 4 * unit:  # too narrow to hold multiple columns
            return []

        nbins = 256
        binw = width / nbins
        cover = [0] * nbins
        for o in observations:
            bi = max(0, int((o["x"] - x0) / binw))
            bj = min(nbins - 1, int((o["x"] + o["w"] - x0) / binw))
            for b in range(bi, bj + 1):
                cover[b] += 1

        peak = max(cover)
        if peak < 4:  # too sparse to confidently call it multi-column
            return []
        thresh = max(2.0, peak * 0.15)
        min_gutter = max(
            1, int((1.0 * unit) / binw)
        )  # gutter must be ~1 line-height wide

        splits: List[float] = []
        run_start: Optional[int] = None
        seen_high = False
        for b in range(nbins):
            if cover[b] < thresh:
                if run_start is None:
                    run_start = b
            else:
                # Only record interior gutters (a high-coverage column must precede them).
                if (
                    seen_high
                    and run_start is not None
                    and (b - run_start) >= min_gutter
                ):
                    splits.append(x0 + (run_start + b) / 2 * binw)
                run_start = None
                seen_high = True
        return splits

    @staticmethod
    def _reconstruct_layout(
        observations: List[Dict], select_x: Optional[float] = None
    ) -> str:
        """Rebuild on-screen text from OCR observations (normalized coords, bottom-left origin).

        Side-by-side panes are segmented into columns by x (a vertical-gutter projection). When
        `select_x` is given (the cursor / focused-input x, normalized to the capture's [0,1]), only
        the column the user is in is kept — matching the bounding box to the active pane. Otherwise
        all columns are read column-major (each top-to-bottom, left-to-right) so a chat sidebar,
        message list, and thread panel don't interleave. All thresholds derive from `unit` = median
        text height, so the output is invariant to screen/app zoom. Mirrors Swift `spatialJoin`.
        """
        if not observations:
            return ""

        # Adaptive base unit: median text height (the per-observation `h`), scales with zoom.
        heights = [o["h"] for o in observations if o.get("h") and o["h"] > 0]
        unit = statistics.median(heights) if heights else 0.015

        line_tol = 0.6 * unit  # tokens within this Δy_mid are on the same visual line
        block_gap = 2.2 * unit  # vertical jump above this → blank line between blocks
        dedup_dist = 1.0 * unit
        hgap = 8.0 * unit  # within-line horizontal split (gutter/timestamp vs body)

        observations = AccessibilityCollector._dedup_observations(
            observations, dist=dedup_dist
        )

        def x_mid(o: Dict) -> float:
            return o["x"] + o["w"] / 2

        def y_mid(o: Dict) -> float:
            return o["y"] + o["h"] / 2

        def emit_line(toks: List[Dict]) -> List[str]:
            toks = sorted(toks, key=lambda o: o["x"])
            out: List[str] = []
            cur: List[str] = []
            last_right = None
            for o in toks:
                if last_right is not None and (o["x"] - last_right) > hgap:
                    out.append(" ".join(cur))
                    cur = []
                cur.append(o["text"])
                last_right = o["x"] + o["w"]
            if cur:
                out.append(" ".join(cur))
            return out

        def assemble_column(col_obs: List[Dict]) -> List[str]:
            """Top-to-bottom line reconstruction within a single column."""
            ordered = sorted(col_obs, key=lambda o: (-y_mid(o), x_mid(o)))
            lines: List[str] = []
            current: List[Dict] = []
            line_y: Optional[float] = None  # anchored to the top-most token on the line
            for o in ordered:
                if current and line_y is not None and (line_y - y_mid(o)) > line_tol:
                    lines.extend(emit_line(current))
                    if (line_y - y_mid(o)) > block_gap:
                        lines.append("")  # blank line between distinct blocks/messages
                    current = []
                    line_y = None
                current.append(o)
                line_y = y_mid(o) if line_y is None else max(line_y, y_mid(o))
            if current:
                lines.extend(emit_line(current))
            return lines

        # Segment into columns by x.
        splits = AccessibilityCollector._detect_column_splits(observations, unit)
        columns: List[List[Dict]] = [[] for _ in range(len(splits) + 1)]
        for o in observations:
            idx = sum(1 for s in splits if x_mid(o) > s)
            columns[idx].append(o)

        # If we know where the cursor/input is, keep only that column (the active pane).
        if select_x is not None and splits:
            active = sum(1 for s in splits if select_x > s)
            columns = [columns[active]]

        # Read the kept column(s) column-major (left-to-right).
        lines: List[str] = []
        for col_obs in columns:
            if not col_obs:
                continue
            if lines:
                lines.append("")  # blank line between columns
            lines.extend(assemble_column(col_obs))

        # Collapse runs of blank lines and strip trailing blanks.
        cleaned: List[str] = []
        for ln in lines:
            if ln == "" and (not cleaned or cleaned[-1] == ""):
                continue
            cleaned.append(ln)
        while cleaned and cleaned[-1] == "":
            cleaned.pop()

        return "\n".join(cleaned)[:4000]

    @staticmethod
    def _select_x_in_capture(
        cursor_x: Optional[float],
        focus_x: Optional[float],
        cap_x: Optional[int],
        cap_w: Optional[int],
    ) -> Optional[float]:
        """Map the cursor / focused-input screen-x into the capture's [0,1] range.

        Prefer the cursor; fall back to keyboard focus when the cursor falls outside the captured
        rect. Returns None (→ keep all columns) when neither lands inside or no crop was used.
        """
        if cap_x is None or not cap_w:
            return None
        for sx in (cursor_x, focus_x):
            if sx is None:
                continue
            norm = (sx - cap_x) / cap_w
            if 0.0 <= norm <= 1.0:
                return norm
        return None

    def _take_ocr_screenshot(
        self,
        app_name: str,
        window_title: str,
        frame: Optional[Dict] = None,
        cursor_x: Optional[float] = None,
        focus_x: Optional[float] = None,
        window_id: Optional[int] = None,
    ) -> Optional[Dict]:
        if not _has_screen_recording_permission():
            logger.debug(
                "Screen Recording permission not granted, skipping OCR capture"
            )
            return None
        binary = self._ensure_ocr_binary()
        if not binary:
            return None
        fd, tmp_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            cap_cmd = ["screencapture", "-x"]
            cap_x = cap_w = None  # capture rect left edge / width, for column selection
            if window_id is not None:
                # Capture exactly the focused window (no menu bar, desktop, or other apps).
                # -o drops the drop-shadow so the image bounds match the window frame.
                cap_cmd += ["-o", "-l", str(window_id)]
                if frame and frame.get("w", 0) > 0:
                    cap_x, cap_w = int(frame["x"]), int(frame["w"])
            elif frame and frame.get("w", 0) > 0 and frame.get("h", 0) > 0:
                # Fallback: crop to the window frame by coordinates (no padding).
                x = max(0, int(frame["x"]))
                y = max(0, int(frame["y"]))
                w = int(frame["w"])
                h = int(frame["h"])
                cap_cmd += ["-R", f"{x},{y},{w},{h}"]
                cap_x, cap_w = x, w
            cap_cmd.append(tmp_path)
            cap = subprocess.run(
                cap_cmd,
                capture_output=True,
                timeout=5,
            )
            if cap.returncode != 0 or not os.path.getsize(tmp_path):
                return None
            digest = hashlib.md5(open(tmp_path, "rb").read()).hexdigest()
            _key = (app_name, window_title)
            if digest == self._screenshot_hashes.get(_key):
                logger.debug("Screenshot unchanged for %s, skipping OCR", app_name)
                return None
            _bounded_set(self._screenshot_hashes, _key, digest)
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
            # Normalize the cursor / focused-input x into the capture's [0,1] range so layout
            # reconstruction can keep only the column (pane) the user is actually in. Prefer the
            # cursor; fall back to keyboard focus when the cursor is outside the capture.
            select_x = self._select_x_in_capture(cursor_x, focus_x, cap_x, cap_w)
            logger.debug(
                "OCR pane select_x=%s (cursor_x=%s focus_x=%s cap_x=%s cap_w=%s) for %s",
                select_x,
                cursor_x,
                focus_x,
                cap_x,
                cap_w,
                app_name,
            )
            try:
                observations = json.loads(raw)
                text = self._reconstruct_layout(observations, select_x=select_x)
            except (json.JSONDecodeError, TypeError, KeyError):
                text = raw[:4000]
            if not text:
                return None
            return {
                "app": app_name,
                "window_title": window_title,
                "text": text,
                "raw_text_source": "ocr",
            }
        except (subprocess.TimeoutExpired, OSError):
            return None
        finally:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass

    def _should_skip(self, app_name: str, window_title: str) -> bool:
        """Filter checks applied before OCR so we never capture sensitive windows."""
        if app_name in _build_skip_apps():
            logger.debug("Skipping capture for excluded app: %s", app_name)
            return True
        if is_incognito_window(window_title):
            logger.debug("Skipping capture: incognito window (%s)", window_title)
            return True
        if is_banking_domain(window_title):
            logger.debug("Skipping capture: banking site (%s)", window_title)
            return True
        return False

    def _get_screen_content(self) -> Optional[Dict]:
        # OCR-only capture: Screen Recording permission is mandatory.
        if not _has_screen_recording_permission():
            logger.debug("Screen Recording permission not granted; skipping capture")
            return None

        # ax_content --frame-only supplies app/window metadata, the window frame, and the cursor /
        # focused-input x used to pick the active pane (column).
        ax = self._get_ax_content(frame_only=True)
        if ax:
            app_name = ax.get("app", "")
            window_title = ax.get("window_title", "")
            frame = ax.get("frame")
            cursor_x = ax.get("cursor_x")
            focus_x = ax.get("focus_x")
            window_id = ax.get("window_id")
        else:
            # AX binary unavailable — fall back to applescript for app/window, full-screen OCR.
            app_name, window_title = self._get_app_name_fallback()
            frame = None
            cursor_x = focus_x = window_id = None

        # Filter BEFORE OCR so banking/incognito/excluded windows are never screenshotted.
        if self._should_skip(app_name, window_title):
            return None

        return self._take_ocr_screenshot(
            app_name,
            window_title,
            frame=frame,
            cursor_x=cursor_x,
            focus_x=focus_x,
            window_id=window_id,
        )

    @staticmethod
    def _content_diff(old: str, new: str) -> str:
        """Return lines that appear in `new` but not in `old`."""
        old_lines = set(old.splitlines())
        added = [
            line for line in new.splitlines() if line.strip() and line not in old_lines
        ]
        return "\n".join(added)

    def record_current_screen(self) -> None:
        data = self._get_screen_content()
        if not data:
            logger.debug("No screen content captured")
            return
        app = data["app"]
        window_title = data["window_title"]
        # Skip-app / incognito / banking filtering is applied pre-OCR in _get_screen_content.
        text = data["text"]
        if len(text.strip()) < _MIN_CONTENT_LEN:
            logger.debug(
                "Skipping capture: content too short (%d chars)", len(text.strip())
            )
            return

        key = (app, window_title)
        last = self._last_text.get(key, "")

        if self._last_active_key is None:
            transition = "new"
        elif self._last_active_key[0] != app:
            transition = "switch"
        elif self._last_active_key != key:
            transition = "new"
        else:
            transition = "same"

        if last:
            diff = self._content_diff(last, text)
            if len(diff) < _MIN_DIFF_LEN:
                logger.debug(
                    "Skipping capture: only %d new chars (<%d) for %s",
                    len(diff),
                    _MIN_DIFF_LEN,
                    app,
                )
                _bounded_set(self._last_text, key, text)
                self._last_active_key = key
                return
            diff_ratio = len(diff) / max(len(text), 1)
            raw_text_truncated = diff_ratio <= _FULL_REPLACE_RATIO
            raw_for_embed = diff if raw_text_truncated else text
        else:
            raw_for_embed = text
            raw_text_truncated = False

        _bounded_set(self._last_text, key, text)
        self._last_active_key = key

        now = datetime.now(timezone.utc)
        ts_ms = int(now.timestamp() * 1000)
        short_hash = hashlib.md5(f"{app}:{window_title}:{ts_ms}".encode()).hexdigest()[
            :8
        ]

        config.data_dir.mkdir(parents=True, exist_ok=True)
        entry = {
            "type": "screen",
            "timestamp": now.isoformat(),
            "project": None,
            "app_name": app,
            "window_title": window_title,
            "raw_text": raw_for_embed,
            "raw_text_source": data.get("raw_text_source", "ax"),
            "raw_text_truncated": raw_text_truncated,
            "gist": None,
            "transition": transition,
            "trigger": "timer",
            "compacted": False,
            "source_doc_id": f"screen::{ts_ms}::{short_hash}",
            "_meta": {
                "schema_version": 2,
                "text_embedder": config.embedding_model_name,
            },
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
        thread = self._thread
        if thread and thread.is_alive():
            # Wait for the current capture (screencapture + OCR, ~15s worst case)
            # to finish so we don't leave an orphaned subprocess behind.
            thread.join(timeout=_POLL_INTERVAL + 15)
        self._thread = None

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

            text = obj.get("raw_text") or obj.get("text", "")
            if not text:
                continue

            app = obj.get("app_name") or obj.get("app", "")
            window_title = obj.get("window_title", "")
            raw_text_truncated = obj.get(
                "raw_text_truncated", obj.get("is_diff", False)
            )

            if window_title:
                raw_content = f"{app} — {window_title}: {text}"
            else:
                raw_content = f"{app}: {text}"

            meta: Dict = {
                "app_name": app,
                "window_title": window_title,
                "duration_seconds": duration,
                "raw_text_source": obj.get("raw_text_source", "ax"),
                "transition": obj.get("transition"),
                "trigger": obj.get("trigger", "timer"),
                "gist": obj.get("gist"),
                "compacted": obj.get("compacted", False),
            }
            if raw_text_truncated:
                meta["raw_text_truncated"] = True

            source_doc_id = obj.get("source_doc_id")

            events.append(
                {
                    "timestamp": ts,
                    "source": "accessibility",
                    "raw_content": raw_content,
                    "metadata": meta,
                    "source_doc_id": source_doc_id,
                }
            )

        return events
