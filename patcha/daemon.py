import time
import signal
import sys
import os
import json
import logging
from datetime import datetime, timezone

from patcha.collectors.git import GitCollector
from patcha.collectors.browser import BrowserCollector
from patcha.collectors.terminal import TerminalCollector
from patcha.collectors.window import WindowCollector
from patcha.collectors.accessibility import AccessibilityCollector
from patcha.compaction import DailyCompactor
from patcha.process import EventPreprocessor
from patcha.db.store import VectorStore
from patcha.db.models import Event, EventType
from patcha.config import config, settings as _settings
from patcha.utils.guard import CollectorGuard


class ActivityDaemon:
    def __init__(self, poll_interval: int = 60, batch_size: int = 50):
        self.poll_interval = _settings.get("poll_interval") or poll_interval
        self.batch_size = batch_size
        self.running = False
        self.start_time = None
        self.last_collection_time = None

        config.data_dir.mkdir(parents=True, exist_ok=True)
        self.logger = logging.getLogger(__name__)

        self.git_collector = GitCollector() if _settings.get("enable_git") else None
        self.browser_collector = (
            BrowserCollector() if _settings.get("enable_browser") else None
        )
        self.terminal_collector = (
            TerminalCollector() if _settings.get("enable_terminal") else None
        )
        self.window_collector = (
            WindowCollector() if _settings.get("enable_window") else None
        )
        self.accessibility_collector = (
            AccessibilityCollector() if _settings.get("enable_accessibility") else None
        )
        self.preprocessor = EventPreprocessor()
        self.vector_store = VectorStore()
        self.daily_compactor = DailyCompactor()
        self._guards: dict = {}

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    def _signal_handler(self, signum, frame):
        self.logger.info(f"Received signal {signum}, shutting down...")
        self.stop()
        sys.exit(0)

    def start(self):
        self.running = True
        self.start_time = datetime.now(timezone.utc)
        self.last_collection_time = self.start_time

        self.logger.info(
            "Starting Activity Daemon (pid=%d, poll interval: %ds)",
            os.getpid(),
            self.poll_interval,
        )
        self.logger.info("Will only collect events after: %s", self.start_time)

        active_collectors = [
            name
            for name, obj in [
                ("git", self.git_collector),
                ("browser", self.browser_collector),
                ("terminal", self.terminal_collector),
                ("window", self.window_collector),
                ("accessibility", self.accessibility_collector),
            ]
            if obj is not None
        ]
        self.logger.info(
            "Active collectors: %s", ", ".join(active_collectors) or "none"
        )

        if not config.openai_api_key:
            self.logger.error(
                "OPENAI_API_KEY not set. Please configure your .env file."
            )
            return

        config.data_dir.mkdir(exist_ok=True)

        pid_file = config.data_dir / "daemon.pid"
        daemon_info = {
            "pid": os.getpid(),
            "start_time": self.start_time.isoformat(),
            "last_collection_time": self.last_collection_time.isoformat(),
        }
        with open(pid_file, "w") as f:
            json.dump(daemon_info, f)

        if self.accessibility_collector:
            self.accessibility_collector.start_recording()
        try:
            while self.running:
                self.collect_and_process()
                if self.running:
                    time.sleep(self.poll_interval)
        finally:
            if self.accessibility_collector:
                self.accessibility_collector.stop_recording()
            if pid_file.exists():
                pid_file.unlink()
            self.logger.info("Daemon stopped")

    def stop(self):
        self.running = False
        self.logger.info("Stopping daemon...")

    def collect_and_process(self):
        try:
            since = self.last_collection_time
            current_time = datetime.now(timezone.utc)

            self.logger.info(
                f"Collecting activities since {since.strftime('%Y-%m-%d %H:%M:%S UTC')}"
            )

            all_events = []

            if self.window_collector:
                try:
                    self.window_collector.record_current_window()
                except Exception as e:
                    self.logger.debug(f"Window snapshot skipped: {e}")

            if self.git_collector:
                try:
                    self.git_collector.record_staging_snapshot()
                except Exception as e:
                    self.logger.debug(f"Git staging snapshot skipped: {e}")

            sources = []
            if self.git_collector:
                sources.append(("git", self.git_collector.collect_commits))
                sources.append(
                    ("git_staged", self.git_collector.collect_staging_events)
                )
            if self.browser_collector:
                sources.append(("browser", self.browser_collector.collect_all))
            if self.terminal_collector:
                sources.append(("terminal", self.terminal_collector.collect_all))
            if self.window_collector:
                sources.append(("window", self.window_collector.collect_windows))
            if self.accessibility_collector:
                sources.append(
                    ("accessibility", self.accessibility_collector.collect_screen_text)
                )

            for source_name, collector_func in sources:
                guard = self._guards.setdefault(
                    source_name, CollectorGuard(source_name)
                )
                if not guard.ok:
                    continue
                try:
                    events = collector_func(since)
                    if source_name == "git":
                        stash_events = self.git_collector.collect_stashes(since)
                        events.extend(stash_events)
                        self.logger.debug(
                            f"Collected {len(stash_events)} stash events from git"
                        )

                    if source_name == "accessibility":
                        events = [
                            Event(
                                timestamp=e["timestamp"],
                                type=EventType.SCREEN,
                                source=e.get("source", "accessibility"),
                                raw_content=e.get("raw_content", ""),
                                metadata=e.get("metadata", {}),
                            )
                            if isinstance(e, dict)
                            else e
                            for e in events
                        ]

                    all_events.extend(events)
                    guard.success()
                    self.logger.info(
                        f"Collected {len(events)} events from {source_name}"
                    )

                except Exception as e:
                    guard.fail(e)
                    continue

            if not all_events:
                self.logger.debug("No new events to process")
                self.last_collection_time = current_time
                return

            self.logger.info(
                f"Processing {len(all_events)} events in batches of {self.batch_size}"
            )

            total_stored = 0
            for i in range(0, len(all_events), self.batch_size):
                batch = all_events[i : i + self.batch_size]

                processed_events = []
                for event in batch:
                    try:
                        processed_events.extend(self.preprocessor.process_event(event))
                    except Exception as e:
                        self.logger.warning(f"Error processing event: {e}")
                        continue

                if processed_events:
                    try:
                        stored_count = self.vector_store.store_events(processed_events)
                        total_stored += stored_count
                        self.logger.debug(f"Stored batch of {stored_count} events")
                    except Exception as e:
                        self.logger.error(f"Error storing batch: {e}")
                        continue

            self.logger.info(
                f"Successfully processed and stored {total_stored}/{len(all_events)} events"
            )

            try:
                self.daily_compactor.maybe_compact_previous_days()
            except Exception as e:
                self.logger.error(f"Daily compaction error: {e}")

            self.last_collection_time = current_time

            pid_file = config.data_dir / "daemon.pid"
            if pid_file.exists():
                try:
                    with open(pid_file, "r") as f:
                        daemon_info = json.load(f)
                    daemon_info["last_collection_time"] = current_time.isoformat()
                    with open(pid_file, "w") as f:
                        json.dump(daemon_info, f)
                except Exception as e:
                    self.logger.warning(f"Error updating PID file: {e}")

        except Exception as e:
            self.logger.error(f"Error in collection cycle: {e}")

    def get_status(self):
        pid_file = config.data_dir / "daemon.pid"
        if not pid_file.exists():
            return {"running": False, "pid": None}

        try:
            with open(pid_file, "r") as f:
                content = f.read().strip()

            try:
                daemon_info = json.loads(content)
                pid = daemon_info["pid"]
                start_time = daemon_info.get("start_time")
                last_collection = daemon_info.get("last_collection_time")
            except json.JSONDecodeError:
                pid = int(content)
                start_time = None
                last_collection = None

            import psutil

            if psutil.pid_exists(pid):
                status = {"running": True, "pid": pid}
                if start_time:
                    status["start_time"] = start_time
                if last_collection:
                    status["last_collection"] = last_collection
                return status
            else:
                pid_file.unlink()
                return {"running": False, "pid": None}
        except Exception as e:
            return {"running": False, "pid": None, "error": str(e)}


def run_daemon(poll_interval: int = 60, batch_size: int = 50):
    daemon = ActivityDaemon(poll_interval=poll_interval, batch_size=batch_size)
    daemon.start()
