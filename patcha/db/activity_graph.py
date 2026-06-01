"""Structural activity graph — temporal/structural relationships between events.

Complements the semantic KnowledgeGraph (entities/relationships). Stored in the same
SQLite file (knowledge_graph.db) but in its own activity_nodes / activity_edges tables,
built incrementally from event metadata at zero LLM cost.

Captures the structure vector similarity can't express: what app/window an event was on,
which events followed one another, which belong to the same session, what files a commit
touched. See perception.md "Activity graph" (Phase A).
"""

import hashlib
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Dict, List, Optional, Tuple

from patcha.config import config
from patcha.db.models import Event, EventType

log = logging.getLogger(__name__)

# Node labels
NODE_APP = "App"
NODE_WINDOW = "Window"
NODE_SESSION = "Session"
NODE_PROJECT = "Project"
NODE_URL = "URL"
NODE_FILE = "File"
NODE_TASK = "Task"

# Edge types
EDGE_ON_APP = "ON_APP"
EDGE_IN_WINDOW = "IN_WINDOW"
EDGE_BELONGS_TO = "BELONGS_TO"
EDGE_DURING = "DURING"
EDGE_FOLLOWED_BY = "FOLLOWED_BY"
EDGE_SWITCHED_FROM = "SWITCHED_FROM"
EDGE_IN_PROJECT = "IN_PROJECT"
EDGE_MODIFIED = "MODIFIED"
EDGE_VISITED = "VISITED"
EDGE_CONTAINS = "CONTAINS"

# Per-event-type node label
_EVENT_LABELS = {
    EventType.SCREEN: "ScreenEvent",
    EventType.WINDOW: "WindowEvent",
    EventType.TERMINAL: "TerminalEvent",
    EventType.BROWSER: "BrowserEvent",
    EventType.GIT_COMMIT: "GitCommit",
    EventType.GIT_STASH: "GitCommit",
    EventType.GIT_STAGED: "GitCommit",
    EventType.NOTE: "NoteEvent",
}

_SWITCH_TRIGGERS = {"app_switch", "space_switch"}


def _ts_ms(ts: datetime) -> int:
    return int(ts.timestamp() * 1000)


class ActivityGraph:
    """Structural/temporal graph over events, persisted in SQLite."""

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(config.data_dir / "knowledge_graph.db")
        self.db_path = db_path
        self.gap_seconds = getattr(config, "session_gap_seconds", 600)

        # In-memory mirrors for cheap upsert merge / dedup.
        self.nodes: Dict[str, dict] = {}
        self._edge_keys: set = set()  # (src, type, dst)
        # Secondary index so compaction can resolve {type}_{ts} activity ids to
        # the live event node (whose id is its source_doc_id, not recoverable later).
        self._event_by_tskey: Dict[Tuple[str, int], str] = {}

        self._init_database()
        self._load_from_database()

    # --- schema -----------------------------------------------------------

    def _init_database(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_nodes (
                    id TEXT PRIMARY KEY,
                    label TEXT NOT NULL,
                    props TEXT,
                    created_at TEXT,
                    updated_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_edges (
                    id TEXT PRIMARY KEY,
                    src_id TEXT,
                    dst_id TEXT,
                    type TEXT,
                    props TEXT,
                    created_at TEXT
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS activity_session_state (
                    id INTEGER PRIMARY KEY CHECK (id = 1),
                    current_session_id TEXT,
                    last_event_ts TEXT
                )
            """)
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_anodes_label ON activity_nodes(label)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aedges_src ON activity_edges(src_id, type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aedges_dst ON activity_edges(dst_id, type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_aedges_type ON activity_edges(type)"
            )

    def _load_from_database(self):
        with sqlite3.connect(self.db_path) as conn:
            for row in conn.execute(
                "SELECT id, label, props, created_at, updated_at FROM activity_nodes"
            ):
                props = json.loads(row[2]) if row[2] else {}
                self.nodes[row[0]] = {
                    "id": row[0],
                    "label": row[1],
                    "props": props,
                    "created_at": row[3],
                    "updated_at": row[4],
                }
                ts_ms = props.get("ts_ms")
                if ts_ms is not None:
                    self._event_by_tskey[(row[1], int(ts_ms))] = row[0]
            for row in conn.execute(
                "SELECT src_id, type, dst_id FROM activity_edges"
            ):
                self._edge_keys.add((row[0], row[1], row[2]))

    # --- upsert primitives ------------------------------------------------

    def upsert_node(self, label: str, node_id: str, props: dict) -> str:
        now = datetime.now(timezone.utc).isoformat()
        existing = self.nodes.get(node_id)
        if existing:
            merged = {**existing["props"], **{k: v for k, v in props.items() if v is not None}}
            existing["props"] = merged
            existing["updated_at"] = now
            self._persist_node(existing)
        else:
            node = {
                "id": node_id,
                "label": label,
                "props": {k: v for k, v in props.items() if v is not None},
                "created_at": now,
                "updated_at": now,
            }
            self.nodes[node_id] = node
            self._persist_node(node)
            ts_ms = node["props"].get("ts_ms")
            if ts_ms is not None:
                self._event_by_tskey[(label, int(ts_ms))] = node_id
        return node_id

    def upsert_edge(
        self, src_id: str, dst_id: str, edge_type: str, props: Optional[dict] = None
    ) -> None:
        if not src_id or not dst_id or src_id == dst_id:
            return
        key = (src_id, edge_type, dst_id)
        if key in self._edge_keys:
            return
        edge_id = f"{src_id}|{edge_type}|{dst_id}"
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO activity_edges (id, src_id, dst_id, type, props, created_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (
                    edge_id,
                    src_id,
                    dst_id,
                    edge_type,
                    json.dumps(props) if props else None,
                    datetime.now(timezone.utc).isoformat(),
                ),
            )
        self._edge_keys.add(key)

    def _persist_node(self, node: dict):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO activity_nodes (id, label, props, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (
                    node["id"],
                    node["label"],
                    json.dumps(node["props"]),
                    node["created_at"],
                    node["updated_at"],
                ),
            )

    # --- sessions ---------------------------------------------------------

    def current_session(self, now: datetime) -> str:
        """Return the open session id, starting a new session when the gap since the
        last event exceeds session_gap_seconds. Persisted so it survives restarts."""
        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(
                "SELECT current_session_id, last_event_ts FROM activity_session_state WHERE id = 1"
            ).fetchone()

        session_id = row[0] if row else None
        last_ts = datetime.fromisoformat(row[1]) if row and row[1] else None

        new_session = session_id is None
        if last_ts is not None:
            gap = (now - last_ts).total_seconds()
            if gap > self.gap_seconds:
                new_session = True

        if new_session:
            session_id = f"session::{_ts_ms(now)}"
            self.upsert_node(
                NODE_SESSION,
                session_id,
                {"started_at": now.isoformat(), "ended_at": now.isoformat()},
            )
        else:
            node = self.nodes.get(session_id)
            if node:
                ended = node["props"].get("ended_at")
                if not ended or datetime.fromisoformat(ended) < now:
                    node["props"]["ended_at"] = now.isoformat()
                    self._persist_node(node)

        # Never move the gap reference backwards on out-of-order/replayed events.
        latest = max(last_ts, now) if last_ts else now
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO activity_session_state (id, current_session_id, last_event_ts) "
                "VALUES (1, ?, ?)",
                (session_id, latest.isoformat()),
            )
        return session_id

    # --- event ingestion --------------------------------------------------

    def event_node_id(self, event: Event) -> str:
        base = event.source_doc_id
        if base:
            return base.split("::chunk::")[0]
        h = hashlib.sha1(event.raw_content.encode("utf-8", "ignore")).hexdigest()[:8]
        return f"{event.type.value}::{_ts_ms(event.timestamp)}::{h}"

    def upsert_event(
        self,
        event: Event,
        prev_event_id: Optional[str] = None,
        session_id: Optional[str] = None,
    ) -> str:
        """Map one event to its structural nodes and edges. Additive context — never
        raises into the caller; logs and returns the node id on failure."""
        node_id = self.event_node_id(event)
        try:
            self._upsert_event_inner(event, node_id, prev_event_id, session_id)
        except Exception as e:
            log.warning("activity graph upsert failed for %s: %s", node_id, e)
        return node_id

    def _upsert_event_inner(self, event, node_id, prev_event_id, session_id):
        meta = event.metadata or {}
        label = _EVENT_LABELS.get(event.type, "Event")
        props = {
            "type": event.type.value,
            "timestamp": event.timestamp.isoformat(),
            "ts_ms": _ts_ms(event.timestamp),
            "project": event.project,
            "app_name": meta.get("app_name"),
            "window_title": meta.get("window_title"),
            "gist": meta.get("gist"),
            "trigger": meta.get("trigger"),
            "transition": meta.get("transition"),
        }
        self.upsert_node(label, node_id, props)

        if session_id:
            self.upsert_edge(node_id, session_id, EDGE_DURING)

        if event.type in (EventType.SCREEN, EventType.WINDOW):
            self._link_app_window(event, node_id, meta)
        elif event.type in (EventType.GIT_COMMIT, EventType.GIT_STASH, EventType.GIT_STAGED):
            self._link_git(event, node_id)
        elif event.type == EventType.BROWSER:
            self._link_browser(event, node_id, meta)
        elif event.type == EventType.TERMINAL:
            self._link_project(event, node_id)

        if prev_event_id and prev_event_id != node_id:
            self.upsert_edge(prev_event_id, node_id, EDGE_FOLLOWED_BY)
            trigger = meta.get("trigger")
            transition = meta.get("transition")
            if trigger in _SWITCH_TRIGGERS or transition == "switch":
                self.upsert_edge(node_id, prev_event_id, EDGE_SWITCHED_FROM)

    def _link_app_window(self, event, node_id, meta):
        app = meta.get("app_name")
        if app:
            app_id = f"app::{app}"
            self.upsert_node(NODE_APP, app_id, {"name": app})
            self.upsert_edge(node_id, app_id, EDGE_ON_APP)
            title = meta.get("window_title")
            if title:
                win_id = f"window::{app}::{title}"
                self.upsert_node(
                    NODE_WINDOW, win_id, {"title": title, "app_name": app}
                )
                self.upsert_edge(node_id, win_id, EDGE_IN_WINDOW)
                self.upsert_edge(win_id, app_id, EDGE_BELONGS_TO)

    def _link_project(self, event, node_id):
        if event.project:
            proj_id = f"project::{event.project}"
            self.upsert_node(NODE_PROJECT, proj_id, {"name": event.project})
            self.upsert_edge(node_id, proj_id, EDGE_IN_PROJECT)

    def _link_git(self, event, node_id):
        self._link_project(event, node_id)
        try:
            data = json.loads(event.raw_content)
        except (json.JSONDecodeError, TypeError):
            return
        for path in data.get("files_changed", []) or []:
            file_id = f"file::{path}"
            self.upsert_node(
                NODE_FILE, file_id, {"path": path, "project": event.project}
            )
            self.upsert_edge(node_id, file_id, EDGE_MODIFIED)

    def _link_browser(self, event, node_id, meta):
        try:
            data = json.loads(event.raw_content)
        except (json.JSONDecodeError, TypeError):
            return
        url = data.get("url")
        if url:
            url_id = f"url::{url}"
            self.upsert_node(
                NODE_URL, url_id, {"url": url, "domain": meta.get("domain") or data.get("domain")}
            )
            self.upsert_edge(node_id, url_id, EDGE_VISITED)

    # --- task linking (compaction) ----------------------------------------

    def upsert_task(self, task, event_lookup: Dict[str, Event]) -> None:
        """Create a Task node and CONTAINS edges to its member event nodes.

        event_lookup maps the task's synthetic activity ids ({type}_{ts_iso}) to the
        reconstructed Event so we can resolve each to its live event node via the
        (label, ts_ms) secondary index. Additive — never raises into the caller."""
        try:
            task_id = f"task::{task.id}"
            self.upsert_node(
                NODE_TASK,
                task_id,
                {
                    "title": task.title,
                    "summary": task.description,
                    "project": task.project,
                    "started_at": task.start_time.isoformat() if task.start_time else None,
                    "ended_at": task.end_time.isoformat() if task.end_time else None,
                },
            )
            for activity_id in task.activities:
                event = event_lookup.get(activity_id)
                if event is None:
                    continue
                label = _EVENT_LABELS.get(event.type, "Event")
                node_id = self._event_by_tskey.get((label, _ts_ms(event.timestamp)))
                if node_id:
                    self.upsert_edge(task_id, node_id, EDGE_CONTAINS)
        except Exception as e:
            log.warning("activity graph task upsert failed for %s: %s", task.id, e)

    # --- queries ----------------------------------------------------------

    def get_node(self, node_id: str) -> Optional[dict]:
        return self.nodes.get(node_id)

    def _src_of(self, dst_id: str, edge_type: str) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT src_id FROM activity_edges WHERE dst_id = ? AND type = ?",
                    (dst_id, edge_type),
                )
            ]

    def _dst_of(self, src_id: str, edge_type: str) -> List[str]:
        with sqlite3.connect(self.db_path) as conn:
            return [
                r[0]
                for r in conn.execute(
                    "SELECT dst_id FROM activity_edges WHERE src_id = ? AND type = ?",
                    (src_id, edge_type),
                )
            ]

    def before(self, event_id: str, n: int = 1) -> List[dict]:
        """Events that preceded event_id, nearest first (walks FOLLOWED_BY backwards)."""
        out, cur = [], event_id
        for _ in range(n):
            preds = self._src_of(cur, EDGE_FOLLOWED_BY)
            if not preds:
                break
            cur = preds[0]
            node = self.get_node(cur)
            if node:
                out.append(node)
        return out

    def after(self, event_id: str, n: int = 1) -> List[dict]:
        """Events that followed event_id, nearest first."""
        out, cur = [], event_id
        for _ in range(n):
            succ = self._dst_of(cur, EDGE_FOLLOWED_BY)
            if not succ:
                break
            cur = succ[0]
            node = self.get_node(cur)
            if node:
                out.append(node)
        return out

    def session_of(self, event_id: str) -> Optional[dict]:
        """The session an event belongs to, via its DURING edge."""
        for sid in self._dst_of(event_id, EDGE_DURING):
            node = self.get_node(sid)
            if node:
                return node
        return None

    def find_event(self, app: Optional[str] = None, at: Optional[datetime] = None) -> Optional[dict]:
        """Resolve an anchor event node by app name and/or time. With both, picks the
        app's event nearest the given time; with neither, the most recent event."""
        candidates = [n for n in self.nodes.values() if "type" in n["props"]]
        if app:
            app_lc = app.lower()
            candidates = [
                n for n in candidates
                if (n["props"].get("app_name") or "").lower() == app_lc
            ]
        if not candidates:
            return None
        if at is not None:
            target = _ts_ms(at)
            return min(candidates, key=lambda n: abs(n["props"].get("ts_ms", 0) - target))
        return max(candidates, key=lambda n: n["props"].get("ts_ms", 0))

    def events_in_session(self, session_id: str) -> List[dict]:
        return [self.get_node(nid) for nid in self._src_of(session_id, EDGE_DURING)
                if self.get_node(nid)]

    def session_at(self, ts: datetime) -> Optional[dict]:
        for node in self.nodes.values():
            if node["label"] != NODE_SESSION:
                continue
            started = node["props"].get("started_at")
            ended = node["props"].get("ended_at")
            if started and ended:
                if datetime.fromisoformat(started) <= ts <= datetime.fromisoformat(ended):
                    return node
        return None

    def apps_in_session(self, session_id: str) -> List[dict]:
        apps: Dict[str, dict] = {}
        for ev in self.events_in_session(session_id):
            for app_id in self._dst_of(ev["id"], EDGE_ON_APP):
                node = self.get_node(app_id)
                if node:
                    apps[app_id] = node
        return list(apps.values())

    def events_touching(self, node_id: str) -> List[dict]:
        """Events that reference a node (any edge into it), e.g. a File or URL."""
        seen, out = set(), []
        with sqlite3.connect(self.db_path) as conn:
            for (src,) in conn.execute(
                "SELECT src_id FROM activity_edges WHERE dst_id = ?", (node_id,)
            ):
                if src in seen:
                    continue
                seen.add(src)
                node = self.get_node(src)
                # Event nodes carry a "type" prop (EventType); structural nodes don't.
                if node and "type" in node["props"]:
                    out.append(node)
        return out

    def neighbors(self, node_id: str, max_depth: int = 1) -> List[dict]:
        seen = {node_id}
        frontier = [node_id]
        for _ in range(max_depth):
            nxt = []
            with sqlite3.connect(self.db_path) as conn:
                for nid in frontier:
                    for (other,) in conn.execute(
                        "SELECT dst_id FROM activity_edges WHERE src_id = ? "
                        "UNION SELECT src_id FROM activity_edges WHERE dst_id = ?",
                        (nid, nid),
                    ):
                        if other not in seen:
                            seen.add(other)
                            nxt.append(other)
            frontier = nxt
        return [self.get_node(nid) for nid in seen if nid != node_id and self.get_node(nid)]

    def get_stats(self) -> Dict[str, int]:
        labels: Dict[str, int] = {}
        for node in self.nodes.values():
            labels[node["label"]] = labels.get(node["label"], 0) + 1
        return {
            "node_count": len(self.nodes),
            "edge_count": len(self._edge_keys),
            "nodes_by_label": labels,
        }
