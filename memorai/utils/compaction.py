import json
import logging
from datetime import date, datetime, timedelta, timezone
from typing import List, Optional

import numpy as np

from memorai.config import config
from memorai.db.models import Category, Event, EventType
from memorai.db.store import VectorStore
from memorai.db.tasks import TaskStore
from memorai.identify import TaskIdentifier
from memorai.process import EventPreprocessor

log = logging.getLogger(__name__)

_STATE_FILE = config.data_dir / "daily_compaction.json"


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"compacted_dates": [], "last_trigger_date": None}


def _save_state(state: dict):
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f)


class DailyCompactor:
    def __init__(self):
        self.vector_store = VectorStore()
        self.task_store = TaskStore()
        preprocessor = EventPreprocessor()
        self.task_identifier = TaskIdentifier(preprocessor, vector_store=None)

    def _reconstruct_events(self, raw_records: list) -> List[Event]:
        events = []
        for record in raw_records:
            vector = record.get("vector")
            if vector is None:
                continue
            payload = record["payload"]
            try:
                event = Event(
                    timestamp=datetime.fromisoformat(payload["timestamp"]),
                    type=EventType(payload["type"]),
                    source=payload.get("source", ""),
                    project=payload.get("project"),
                    raw_content=payload.get("raw_content", ""),
                    metadata=payload.get("metadata") or {},
                    summary=payload.get("summary"),
                    category=Category(payload["category"]) if payload.get("category") else None,
                    embedding=vector,
                )
                events.append(event)
            except Exception as e:
                log.debug("skipping malformed record: %s", e)
        return events

    def _dedup_by_content(self, events: List[Event]) -> List[Event]:
        seen: dict = {}
        kept: List[Event] = []

        for event in events:
            key = None

            if event.type == EventType.TERMINAL:
                try:
                    cmd = json.loads(event.raw_content).get("command", event.raw_content)
                except Exception:
                    cmd = event.raw_content
                key = ("terminal", cmd)

            elif event.type == EventType.BROWSER:
                try:
                    url = json.loads(event.raw_content).get("url", event.raw_content)
                except Exception:
                    url = event.raw_content
                key = ("browser", url)

            elif event.type in (EventType.WINDOW, EventType.SCREEN):
                app = (event.metadata or {}).get("app_name", "")
                title = (event.metadata or {}).get("window_title", "")
                key = (event.type.value, app, title)

            if key is None:
                kept.append(event)
                continue

            if key not in seen:
                seen[key] = len(kept)
                kept.append(event)
            else:
                kept[seen[key]] = event

        return kept

    def _dedup_by_vector(self, events: List[Event]) -> List[Event]:
        threshold = config.working_memory_dedup_threshold
        kept: List[Event] = []
        kept_vecs: List[np.ndarray] = []

        for event in events:
            if event.embedding is None:
                kept.append(event)
                continue

            emb = np.array(event.embedding, dtype=np.float32)
            norm = np.linalg.norm(emb)
            if norm == 0:
                kept.append(event)
                continue
            emb_n = emb / norm

            if kept_vecs:
                matrix = np.stack(kept_vecs)
                sims = matrix @ emb_n
                if float(sims.max()) >= threshold:
                    continue

            kept.append(event)
            kept_vecs.append(emb_n)

        return kept

    def _dedup_events(self, events: List[Event]) -> List[Event]:
        before = len(events)
        events = sorted(events, key=lambda e: e.timestamp)
        events = self._dedup_by_content(events)
        after_content = len(events)
        log.info("content dedup: %d -> %d events", before, after_content)

        if len(events) > 50:
            events = self._dedup_by_vector(events)
            log.info("vector dedup: %d -> %d events", after_content, len(events))

        return events

    def compact_day(self, target_date: date, dry_run: bool = False, force: bool = False) -> dict:
        today = datetime.now(timezone.utc).date()
        if target_date >= today:
            return {"skipped": True, "reason": "cannot_compact_today_or_future"}

        state = _load_state()
        date_str = target_date.isoformat()

        if date_str in state["compacted_dates"] and not force:
            log.info("daily compaction: %s already compacted, skipping", date_str)
            return {"skipped": True, "reason": "already_compacted", "date": date_str}

        raw_records = self.vector_store.get_events_by_date(target_date)
        log.info("daily compaction: fetched %d raw events for %s", len(raw_records), date_str)

        if not raw_records:
            if not dry_run:
                if date_str not in state["compacted_dates"]:
                    state["compacted_dates"].append(date_str)
                _save_state(state)
            return {"date": date_str, "event_count": 0, "deduped_count": 0, "task_count": 0, "dry_run": dry_run}

        events = self._reconstruct_events(raw_records)
        events = self._dedup_events(events)
        log.info("daily compaction: %d events after dedup for %s", len(events), date_str)

        tasks = self.task_identifier.identify_tasks_from_activities(
            events,
            min_activities_per_task=config.daily_compaction_min_activities,
        )
        log.info("daily compaction: identified %d tasks for %s", len(tasks), date_str)

        if not dry_run:
            for task in tasks:
                self.task_store.store_task(task)
            point_ids = [r["id"] for r in raw_records]
            self.vector_store.mark_events_compacted(point_ids)
            self.vector_store.delete_events_by_date(target_date)
            if date_str not in state["compacted_dates"]:
                state["compacted_dates"].append(date_str)
            _save_state(state)

        return {
            "date": date_str,
            "event_count": len(raw_records),
            "deduped_count": len(events),
            "task_count": len(tasks),
            "dry_run": dry_run,
        }

    def maybe_compact_previous_days(self):
        state = _load_state()
        today = datetime.now(timezone.utc).date()
        today_str = today.isoformat()

        if state.get("last_trigger_date") == today_str:
            log.debug("daily compaction already ran today, skipping")
            return

        for days_ago in range(1, 8):
            target = today - timedelta(days=days_ago)
            target_str = target.isoformat()
            if target_str in state["compacted_dates"]:
                continue
            try:
                result = self.compact_day(target)
                log.info("daily compaction result for %s: %s", target_str, result)
            except Exception as e:
                log.error("daily compaction failed for %s: %s", target_str, e)

        state = _load_state()
        state["last_trigger_date"] = today_str
        _save_state(state)
