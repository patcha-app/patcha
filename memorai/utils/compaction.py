import json
import logging
import uuid
from datetime import datetime, timezone
from typing import List, Optional

from openai import OpenAI
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, PointStruct, VectorParams

from memorai.config import config
from memorai.db.models import Event, EventType

log = logging.getLogger(__name__)

_PROMPT = """\
Summarise the following developer activity into one compact paragraph (60 words max).

Preserve:
- What was built or changed (file names, features)
- What was researched (docs, articles, URLs)
- Key commands run
- The apparent current focus

Rules: past tense except for current focus. Be specific — include file names, \
tool names, and task names. Skip noise (window switches, repeated identical commands).

Activity ({start} to {end}):
{events}

Compact summary:"""


def _format_events_for_prompt(events: List[Event]) -> str:
    lines = []
    for e in events:
        ts = e.timestamp.strftime("%H:%M")
        if e.type == EventType.TERMINAL:
            try:
                cmd = json.loads(e.raw_content).get("command", e.raw_content)
            except Exception:
                cmd = e.raw_content
            lines.append(f"[{ts}] terminal: {cmd[:100]}")

        elif e.type == EventType.BROWSER:
            try:
                data = json.loads(e.raw_content)
                lines.append(f"[{ts}] browser: {data.get('title', '')} | {data.get('domain', '')}")
            except Exception:
                lines.append(f"[{ts}] browser: {e.raw_content[:100]}")

        elif e.type in (EventType.GIT_COMMIT, EventType.GIT_STASH):
            try:
                data = json.loads(e.raw_content)
                msg = data.get("message", "")
                files = data.get("files_changed", [])
                line = f"[{ts}] {e.type.value}: {msg}"
                if files:
                    line += " | " + ", ".join(files[:5])
                lines.append(line)
            except Exception:
                lines.append(f"[{ts}] {e.type.value}: {e.raw_content[:100]}")

        elif e.type == EventType.GIT_STAGED:
            lines.append(f"[{ts}] git-staged: {e.raw_content[:100]}")

        elif e.type in (EventType.SCREEN, EventType.WINDOW):
            meta = e.metadata or {}
            app = meta.get("app_name", "")
            title = meta.get("window_title", "")
            lines.append(f"[{ts}] {e.type.value}: {app} — {title}")

    return "\n".join(lines) if lines else "(no events)"


class Compactor:
    def __init__(self):
        self._client = QdrantClient(url=config.qdrant_url, check_compatibility=False)
        self._openai = OpenAI(api_key=config.openai_api_key)
        self._collection = config.digest_collection_name
        self._last_compaction: Optional[datetime] = None
        self._ensure_collection()

    def _ensure_collection(self):
        try:
            names = [c.name for c in self._client.get_collections().collections]
            if self._collection not in names:
                self._client.create_collection(
                    collection_name=self._collection,
                    vectors_config=VectorParams(size=config.vector_size, distance=Distance.COSINE),
                )
                log.info("created digest collection: %s", self._collection)
        except Exception as e:
            log.error("error ensuring digest collection: %s", e)

    def _summarise(self, events: List[Event], start: datetime, end: datetime) -> Optional[str]:
        formatted = _format_events_for_prompt(events)
        prompt = _PROMPT.format(
            start=start.strftime("%H:%M"),
            end=end.strftime("%H:%M"),
            events=formatted,
        )
        try:
            response = self._openai.chat.completions.create(
                model="gpt-4o-mini",
                messages=[{"role": "user", "content": prompt}],
                max_tokens=120,
            )
            summary = response.choices[0].message.content.strip()
            log.debug("compaction summary (%d events): %s", len(events), summary[:80])
            return summary
        except Exception as e:
            log.error("compaction LLM call failed: %s", e)
            return None

    def _embed(self, text: str) -> Optional[List[float]]:
        try:
            response = self._openai.embeddings.create(
                model="text-embedding-3-small",
                input=text,
            )
            return response.data[0].embedding
        except Exception as e:
            log.error("compaction embedding failed: %s", e)
            return None

    def _store(self, summary: str, embedding: List[float], start: datetime, end: datetime, event_count: int):
        point = PointStruct(
            id=str(uuid.uuid4()),
            vector=embedding,
            payload={
                "summary": summary,
                "start_time": start.isoformat(),
                "end_time": end.isoformat(),
                "created_at_epoch": end.timestamp(),
                "event_count": event_count,
            },
        )
        self._client.upsert(collection_name=self._collection, points=[point])
        log.info("stored digest: %d events → %d chars", event_count, len(summary))

    def maybe_compact(self, events: List[Event], window_start: datetime, window_end: datetime):
        if not events:
            return

        interval_seconds = config.compaction_interval_minutes * 60
        if self._last_compaction and (window_end - self._last_compaction).total_seconds() < interval_seconds:
            log.debug("compaction skipped — %ds since last (interval %ds)",
                      (window_end - self._last_compaction).total_seconds(), interval_seconds)
            return

        summary = self._summarise(events, window_start, window_end)
        if not summary:
            return

        embedding = self._embed(summary)
        if not embedding:
            return

        self._store(summary, embedding, window_start, window_end, len(events))
        self._last_compaction = window_end

    def get_recent_digests(self, n: int = 3) -> str:
        try:
            points, _ = self._client.scroll(
                collection_name=self._collection,
                limit=200,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as e:
            log.error("error fetching digests: %s", e)
            return "# Recent digests\nUnavailable."

        if not points:
            return "# Recent digests\nNone yet."

        sorted_points = sorted(points, key=lambda p: p.payload.get("created_at_epoch", 0), reverse=True)
        recent = sorted_points[:n]
        recent.reverse()

        lines = []
        for p in recent:
            start = p.payload.get("start_time", "")[:16].replace("T", " ")
            end = p.payload.get("end_time", "")[:16].replace("T", " ")
            summary = p.payload.get("summary", "")
            lines.append(f"[{start} – {end}] {summary}")

        return "# Recent digests\n" + "\n".join(lines)
