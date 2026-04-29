"""Context retrieval for agent consumption — Layer 1 (working memory) and Layer 2 (semantic search)."""

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, List, Dict, Any

import numpy as np

from memorai.config import config

if TYPE_CHECKING:
    from memorai.db.store import VectorStore
    from memorai.process import EventPreprocessor

log = logging.getLogger(__name__)


def _cosine_similarity(a: List[float], b: List[float]) -> float:
    va, vb = np.array(a), np.array(b)
    denom = np.linalg.norm(va) * np.linalg.norm(vb)
    return float(np.dot(va, vb) / denom) if denom else 0.0


def _format_line(payload: dict) -> str:
    ts = payload.get("timestamp", "")
    hhmm = ts[11:16] if len(ts) >= 16 else "??:??"
    event_type = payload.get("type", "unknown")
    meta = payload.get("metadata") or {}
    raw = payload.get("raw_content", "")

    if event_type == "terminal":
        try:
            detail = json.loads(raw).get("command", raw)[:120]
        except Exception:
            detail = raw[:120]

    elif event_type == "browser":
        try:
            data = json.loads(raw)
            title = data.get("title", "")
            domain = data.get("domain", "")
            detail = f"{title} | {domain}" if domain else title
        except Exception:
            detail = raw[:80]

    elif event_type in ("git_commit", "git_stash"):
        try:
            data = json.loads(raw)
            msg = data.get("message", "")
            files = data.get("files_changed", [])
            detail = msg + (" | " + ", ".join(files[:5]) if files else "")
        except Exception:
            detail = raw[:120]

    elif event_type == "git_staged":
        detail = raw[:120]

    elif event_type in ("screen", "window"):
        app = meta.get("app_name", "")
        title = meta.get("window_title", "")
        detail = f"{app} — {title}" if title else app

    else:
        detail = raw[:120]

    return f"[{hhmm}] {event_type}: {detail}"


def _dedup_by_similarity(rows: List[Dict[str, Any]], threshold: float) -> List[Dict[str, Any]]:
    # rows must be sorted ascending by timestamp
    last_vector: dict[str, List[float]] = {}
    kept = []

    for row in rows:
        p = row.get("payload", {})
        vector = row.get("vector")
        event_type = p.get("type", "")

        if vector and event_type in last_vector:
            sim = _cosine_similarity(vector, last_vector[event_type])
            if sim >= threshold:
                for i in range(len(kept) - 1, -1, -1):
                    if kept[i].get("payload", {}).get("type") == event_type:
                        kept[i] = row
                        break
                last_vector[event_type] = vector
                continue

        kept.append(row)
        if vector:
            last_vector[event_type] = vector

    return kept


def get_working_memory(store: "VectorStore", minutes: int = 15) -> str:
    since = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    rows = store.get_recent_events_with_vectors(since)

    rows = _dedup_by_similarity(rows, config.working_memory_dedup_threshold)

    lines = [_format_line(r.get("payload", {})) for r in rows]

    if not lines:
        return f"# Working memory (last {minutes}m)\nNo activity recorded."

    return f"# Working memory (last {minutes}m)\n" + "\n".join(lines)


def get_recent_activity(store: "VectorStore", hours: int = 3) -> str:
    since = datetime.now(timezone.utc) - timedelta(hours=hours)
    rows = store.get_recent_events_with_vectors(since)
    rows = _dedup_by_similarity(rows, config.working_memory_dedup_threshold)
    lines = [_format_line(r.get("payload", {})) for r in rows]
    if not lines:
        return f"# Recent activity (last {hours}h)\nNo activity recorded."
    return f"# Recent activity (last {hours}h)\n" + "\n".join(lines)


def search_activity(store: "VectorStore", preprocessor: "EventPreprocessor", query: str, limit: int = 5) -> str:
    embedding = preprocessor.generate_embedding(query)
    if not embedding:
        return f'# Search results for "{query}"\nEmbedding failed — cannot search.'

    results = store.search_events(embedding, limit=limit)
    if not results:
        return f'# Search results for "{query}"\nNo results found.'

    lines = []
    for r in results:
        score = round(r.get("score", 0), 3)
        p = r.get("payload", {})
        ts = p.get("timestamp", "")[:16].replace("T", " ")
        line = _format_line(p)
        lines.append(f"[score={score} | {ts}] {line.split('] ', 1)[-1]}")

    return f'# Search results for "{query}"\n' + "\n".join(lines)
