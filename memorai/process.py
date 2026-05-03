"""Event preprocessing — text extraction and embedding generation."""

import json
import logging
from typing import List, Optional

from openai import OpenAI

from memorai.config import config
from memorai.db.models import Event, EventType
from memorai.utils.chunking import chunk_text

log = logging.getLogger(__name__)


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
        self.client = OpenAI(api_key=config.openai_api_key)
        self.embedding_model = "text-embedding-3-small"

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
        chunks = chunk_text(text, config.max_embedding_tokens, config.embedding_chunk_overlap)

        if len(chunks) == 1:
            event.embedding = self.generate_embedding(chunks[0])
            return [event]

        log.debug("event split into %d chunks (type=%s)", len(chunks), event.type)
        results = []
        for i, chunk in enumerate(chunks):
            chunk_event = event.model_copy(deep=True)
            chunk_event.metadata = {**event.metadata, "chunk_index": i, "total_chunks": len(chunks)}
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
                log.error("error processing event: %s", e)
                processed.append(event)
        return processed
