"""Event preprocessing — text extraction and embedding generation."""

import json
import logging
from pathlib import Path
from typing import List, Optional

from patcha.api_client import PatchaLLMClient
from patcha.config import config
from patcha.db.models import Event, EventType
from patcha.utils.chunking import chunk_text

log = logging.getLogger(__name__)

_PENDING_PATH = config.data_dir / "pending_events.jsonl"


class PendingEventStore:
    def __init__(self, path: Path = _PENDING_PATH) -> None:
        self._path = path
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def save(self, event: Event) -> None:
        with open(self._path, "a") as f:
            f.write(event.model_dump_json() + "\n")

    def load_all(self) -> List[Event]:
        if not self._path.exists():
            return []
        events = []
        for line in self._path.read_text().splitlines():
            line = line.strip()
            if line:
                try:
                    events.append(Event.model_validate_json(line))
                except Exception as e:
                    log.warning("skipping corrupt pending event: %s", e)
        return events

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


def _build_embedding_text(event: Event) -> str:
    if event.type == EventType.BROWSER:
        try:
            data = json.loads(event.raw_content)
            parts = [p for p in (data.get("title"), data.get("domain")) if p]
            text = " | ".join(parts) if parts else event.raw_content
        except (json.JSONDecodeError, TypeError):
            text = event.raw_content

    elif event.type == EventType.TERMINAL:
        try:
            data = json.loads(event.raw_content)
            text = data.get("command", event.raw_content)
        except (json.JSONDecodeError, TypeError):
            text = event.raw_content

    elif event.type in (EventType.GIT_COMMIT, EventType.GIT_STASH):
        try:
            data = json.loads(event.raw_content)
            parts = []
            if data.get("message"):
                parts.append(data["message"])
            files = data.get("files_changed", [])
            if files:
                parts.append("files: " + ", ".join(files[:20]))
            if data.get("diff"):
                parts.append(data["diff"])
            text = " | ".join(parts) if parts else event.raw_content
        except (json.JSONDecodeError, TypeError):
            text = event.raw_content

    else:
        text = event.raw_content

    if event.project:
        text = f"{text} [{event.project}]"

    return text.strip()


class EventPreprocessor:
    def __init__(self):
        self.client = PatchaLLMClient()
        self.embedding_model = "text-embedding-3-small"
        self._pending_store = PendingEventStore()

    def generate_embedding(self, text: str) -> Optional[List[float]]:
        log.debug("generating embedding (len=%d chars)", len(text))
        try:
            response = self.client.embeddings.create(
                model=self.embedding_model,
                input=text,
            )
            embedding = response.data[0].embedding
            log.debug("embedding generated: dim=%d", len(embedding))
            return embedding
        except Exception as e:
            log.error("embedding failed: %s: %s", type(e).__name__, e)
            raise

    def process_event(self, event: Event) -> List[Event]:
        log.debug("processing event type=%s source=%s", event.type, event.source)
        text = _build_embedding_text(event)
        chunks = chunk_text(
            text, config.max_embedding_tokens, config.embedding_chunk_overlap
        )

        if len(chunks) == 1:
            event.embedding = self.generate_embedding(chunks[0])
            return [event]

        log.debug("event split into %d chunks (type=%s)", len(chunks), event.type)
        results = []
        for i, chunk in enumerate(chunks):
            chunk_event = event.model_copy(deep=True)
            chunk_event.metadata = {
                **event.metadata,
                "chunk_index": i,
                "total_chunks": len(chunks),
            }
            if event.source_doc_id:
                chunk_event.source_doc_id = f"{event.source_doc_id}::chunk::{i}"
            chunk_event.embedding = self.generate_embedding(chunk)
            results.append(chunk_event)

        return results

    def process_events(self, events: List[Event]) -> List[Event]:
        processed = []
        for event in events:
            try:
                processed.extend(self.process_event(event))
            except Exception as e:
                log.warning("embedding failed, queuing for later: %s", e)
                self._pending_store.save(event)
        return processed

    def process_pending(self) -> List[Event]:
        pending = self._pending_store.load_all()
        if not pending:
            return []

        # Expand events into (chunk_event, chunk_text) pairs
        pairs: List[tuple] = []
        for event in pending:
            text = _build_embedding_text(event)
            chunks = chunk_text(
                text, config.max_embedding_tokens, config.embedding_chunk_overlap
            )
            if len(chunks) == 1:
                pairs.append((event, chunks[0]))
            else:
                for i, chunk in enumerate(chunks):
                    chunk_event = event.model_copy(deep=True)
                    chunk_event.metadata = {
                        **event.metadata,
                        "chunk_index": i,
                        "total_chunks": len(chunks),
                    }
                    if event.source_doc_id:
                        chunk_event.source_doc_id = f"{event.source_doc_id}::chunk::{i}"
                    pairs.append((chunk_event, chunk))

        texts = [text for _, text in pairs]
        try:
            response = self.client.embeddings.create(
                model=self.embedding_model,
                input=texts,
            )
            result = []
            for (chunk_event, _), emb_data in zip(pairs, response.data):
                chunk_event.embedding = emb_data.embedding
                result.append(chunk_event)
            log.info(
                "bulk embedded %d pending events (%d chunks)", len(pending), len(result)
            )
            return result
        except Exception as e:
            log.error("bulk embedding of pending events failed: %s", e)
            return []

    def clear_pending(self) -> None:
        self._pending_store.clear()
