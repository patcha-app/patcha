import json
import logging
import shutil
import sqlite3
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from patcha.db.models import Event, EventType, BrowserActivity
from patcha.config import config
from patcha.utils.guard import CollectorGuard
from patcha.collectors.filters import is_banking_domain


class BrowserCollector:
    def __init__(self):
        self.history_paths = config.browser_history_paths
        self.logger = logging.getLogger(__name__)
        self._chrome_guard = CollectorGuard("chrome_history")
        self._safari_guard = CollectorGuard("safari_history")
        self._arc_guard = CollectorGuard("arc_history")

    def _enhance_youtube_title(self, title: str, url: str) -> str:
        if "youtube.com/watch" not in url and "youtu.be/" not in url:
            return title

        video_id_match = re.search(r"(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]+)", url)
        if not video_id_match:
            return title

        video_id = video_id_match.group(1)
        if not title or title.strip() == "" or title == "Untitled" or video_id in title:
            if title and title != "Untitled" and video_id not in title:
                return f"YouTube: {title}"
            else:
                return f"YouTube Video (ID: {video_id})"
        if not title.lower().startswith(("youtube", "yt:")):
            return f"YouTube: {title}"

        return title

    def collect_chrome_history(self, since: Optional[datetime] = None) -> List[Event]:
        if not self._chrome_guard.ok:
            return []
        chrome_path = Path(self.history_paths["chrome"]).expanduser()
        if not chrome_path.exists():
            return []

        events = []
        try:
            temp_path = chrome_path.with_suffix(".tmp")
            shutil.copy2(chrome_path, temp_path)

            conn = sqlite3.connect(temp_path)
            cursor = conn.cursor()

            query = """
            SELECT title, url, last_visit_time, visit_count
            FROM urls
            WHERE last_visit_time > ?
            ORDER BY last_visit_time DESC
            LIMIT 1000
            """

            since_chrome = (
                int(
                    (
                        since
                        or datetime.now(timezone.utc).replace(
                            hour=0, minute=0, second=0
                        )
                    ).timestamp()
                    * 1000000
                )
                + 11644473600000000
            )
            cursor.execute(query, (since_chrome,))

            for title, url, last_visit_time, visit_count in cursor.fetchall():
                timestamp = datetime.fromtimestamp(
                    (last_visit_time - 11644473600000000) / 1000000, tz=timezone.utc
                )

                domain = (
                    url.split("//")[-1].split("/")[0]
                    if "//" in url
                    else url.split("/")[0]
                )
                if is_banking_domain(domain):
                    continue
                enhanced_title = self._enhance_youtube_title(title or "Untitled", url)

                browser_activity = BrowserActivity(
                    title=enhanced_title, url=url, timestamp=timestamp, domain=domain
                )

                event = Event(
                    timestamp=timestamp,
                    type=EventType.BROWSER,
                    source="chrome",
                    raw_content=json.dumps(browser_activity.model_dump(), default=str),
                    metadata={
                        "domain": domain,
                        "visit_count": visit_count,
                        "browser": "chrome",
                    },
                )
                events.append(event)

            conn.close()
            temp_path.unlink()
            self._chrome_guard.success()

        except Exception as e:
            self._chrome_guard.fail(e)

        return events

    def collect_safari_history(self, since: Optional[datetime] = None) -> List[Event]:
        if not self._safari_guard.ok:
            return []
        safari_path = Path(self.history_paths["safari"]).expanduser()
        if not safari_path.exists():
            return []

        events = []
        try:
            temp_path = safari_path.with_suffix(".tmp")
            shutil.copy2(safari_path, temp_path)

            conn = sqlite3.connect(temp_path)
            cursor = conn.cursor()

            query = """
            SELECT title, url, visit_time
            FROM history_visits hv
            JOIN history_items hi ON hv.history_item = hi.id
            WHERE visit_time > ?
            ORDER BY visit_time DESC
            LIMIT 1000
            """

            since_safari = (
                since or datetime.now(timezone.utc).replace(hour=0, minute=0, second=0)
            ).timestamp()
            cursor.execute(query, (since_safari,))

            for title, url, visit_time in cursor.fetchall():
                timestamp = datetime.fromtimestamp(visit_time, tz=timezone.utc)
                domain = (
                    url.split("//")[-1].split("/")[0]
                    if "//" in url
                    else url.split("/")[0]
                )
                if is_banking_domain(domain):
                    continue
                enhanced_title = self._enhance_youtube_title(title or "Untitled", url)

                browser_activity = BrowserActivity(
                    title=enhanced_title, url=url, timestamp=timestamp, domain=domain
                )

                event = Event(
                    timestamp=timestamp,
                    type=EventType.BROWSER,
                    source="safari",
                    raw_content=json.dumps(browser_activity.model_dump(), default=str),
                    metadata={"domain": domain, "browser": "safari"},
                )
                events.append(event)

            conn.close()
            temp_path.unlink()
            self._safari_guard.success()

        except PermissionError as e:
            self._safari_guard.fail(
                e,
                msg=f"{e} — grant Full Disk Access in System Settings → Privacy & Security",
            )
        except Exception as e:
            self._safari_guard.fail(e)

        return events

    def collect_arc_history(self, since: Optional[datetime] = None) -> List[Event]:
        if not self._arc_guard.ok:
            return []
        arc_path = Path(self.history_paths["arc"]).expanduser()
        if not arc_path.exists():
            return []

        events = []
        try:
            temp_path = arc_path.with_suffix(".tmp")
            shutil.copy2(arc_path, temp_path)

            conn = sqlite3.connect(temp_path)
            cursor = conn.cursor()

            query = """
            SELECT title, url, last_visit_time, visit_count
            FROM urls
            WHERE last_visit_time > ?
            ORDER BY last_visit_time DESC
            LIMIT 1000
            """

            since_chrome = (
                int(
                    (
                        since
                        or datetime.now(timezone.utc).replace(
                            hour=0, minute=0, second=0
                        )
                    ).timestamp()
                    * 1000000
                )
                + 11644473600000000
            )
            cursor.execute(query, (since_chrome,))

            for title, url, last_visit_time, visit_count in cursor.fetchall():
                timestamp = datetime.fromtimestamp(
                    (last_visit_time - 11644473600000000) / 1000000, tz=timezone.utc
                )

                domain = (
                    url.split("//")[-1].split("/")[0]
                    if "//" in url
                    else url.split("/")[0]
                )
                if is_banking_domain(domain):
                    continue
                enhanced_title = self._enhance_youtube_title(title or "Untitled", url)

                browser_activity = BrowserActivity(
                    title=enhanced_title, url=url, timestamp=timestamp, domain=domain
                )

                event = Event(
                    timestamp=timestamp,
                    type=EventType.BROWSER,
                    source="arc",
                    raw_content=json.dumps(browser_activity.model_dump(), default=str),
                    metadata={
                        "domain": domain,
                        "visit_count": visit_count,
                        "browser": "arc",
                    },
                )
                events.append(event)

            conn.close()
            temp_path.unlink()
            self._arc_guard.success()

        except Exception as e:
            self._arc_guard.fail(e)

        return events

    def collect_all(self, since: Optional[datetime] = None) -> List[Event]:
        events = []
        events.extend(self.collect_chrome_history(since))
        events.extend(self.collect_arc_history(since))
        events.extend(self.collect_safari_history(since))
        return sorted(events, key=lambda x: x.timestamp, reverse=True)
