"""Re-embed existing Qdrant vectors with the local FastEmbed model.

Switching from OpenAI's 1536-dim embeddings to a local 768-dim model makes the
stored vectors incompatible. This migration re-embeds every point from its
stored payload text (events from raw_content, tasks from title+description) into
freshly created collections sized for the new model, preserving point ids and
payloads.

Safety: each collection is rebuilt via a temporary collection — the original is
only deleted after the temp copy is fully populated, so an interrupted run never
loses data outright.
"""

import logging
from datetime import datetime
from typing import Callable, Optional

from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
)
from rich.console import Console

from patcha import embedding
from patcha.config import config
from patcha.db.models import Category, Event, EventType
from patcha.db.store import VectorStore, _get_client
from patcha.db.tasks import TaskStore
from patcha.process import _build_embedding_text

log = logging.getLogger(__name__)
console = Console()


def _collections(client) -> list[str]:
    return [c.name for c in client.get_collections().collections]


def _collection_size(client, name: str) -> Optional[int]:
    try:
        return client.get_collection(name).config.params.vectors.size
    except Exception:
        return None


def _event_text(payload: dict) -> str:
    try:
        event = Event(
            timestamp=datetime.fromisoformat(payload["timestamp"]),
            type=EventType(payload["type"]),
            source=payload.get("source", "unknown"),
            project=payload.get("project"),
            raw_content=payload.get("raw_content", ""),
            summary=payload.get("summary"),
            category=Category(payload["category"]) if payload.get("category") else None,
            metadata=payload.get("metadata", {}),
        )
        return _build_embedding_text(event)
    except Exception as e:
        log.warning("could not rebuild event text: %s", e)
        return ""


def _task_text(payload: dict) -> str:
    parts = [payload.get("title") or "", payload.get("description") or ""]
    return " ".join(p for p in parts if p).strip()


def _new_collection(client, name: str) -> None:
    client.create_collection(
        collection_name=name,
        vectors_config=VectorParams(size=config.vector_size, distance=Distance.COSINE),
    )


def _reembed_collection(
    client,
    name: str,
    text_fn: Callable[[dict], str],
    batch_size: int,
) -> int:
    """Re-embed every point in `name` into a temp collection then swap it in."""
    temp = f"{name}_reembed"
    if temp in _collections(client):
        client.delete_collection(temp)
    _new_collection(client, temp)

    migrated = 0
    skipped = 0
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=name,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        if not points:
            break

        batch = []
        texts = []
        for point in points:
            text = text_fn(point.payload or {})
            if not text:
                skipped += 1
                continue
            batch.append(point)
            texts.append(text)

        if batch:
            vectors = embedding.embed_many(texts)
            client.upsert(
                collection_name=temp,
                points=[
                    PointStruct(id=p.id, vector=v, payload=p.payload)
                    for p, v in zip(batch, vectors)
                ],
            )
            migrated += len(batch)

        if offset is None:
            break

    # Swap: original is deleted only now that temp is fully built.
    client.delete_collection(name)
    _new_collection(client, name)
    offset = None
    while True:
        points, offset = client.scroll(
            collection_name=temp,
            limit=batch_size,
            offset=offset,
            with_payload=True,
            with_vectors=True,
        )
        if not points:
            break
        client.upsert(
            collection_name=name,
            points=[
                PointStruct(id=p.id, vector=p.vector, payload=p.payload) for p in points
            ],
        )
        if offset is None:
            break
    client.delete_collection(temp)

    if skipped:
        console.print(
            f"[yellow]{name}: skipped {skipped} point(s) with no re-embeddable text[/yellow]"
        )
    return migrated


def reembed(dry_run: bool = False, batch_size: int = 128) -> dict:
    client = _get_client()
    existing = _collections(client)
    target = config.vector_size
    report: dict = {"target_dim": target, "collections": {}}

    plan = [
        (config.collection_name, _event_text),
        ("tasks", _task_text),
    ]

    for name, text_fn in plan:
        if name not in existing:
            console.print(f"[dim]{name}: not present, skipping[/dim]")
            continue
        size = _collection_size(client, name)
        count = client.count(collection_name=name).count
        entry = {"current_dim": size, "points": count, "migrated": 0}

        if size == target:
            console.print(
                f"[green]{name}: already {target}-dim ({count} points), nothing to do[/green]"
            )
            report["collections"][name] = entry
            continue

        console.print(
            f"[cyan]{name}: {count} points at dim={size} -> re-embedding to {target}[/cyan]"
        )
        if dry_run:
            report["collections"][name] = entry
            continue

        migrated = _reembed_collection(client, name, text_fn, batch_size)
        entry["migrated"] = migrated
        console.print(f"[green]{name}: re-embedded {migrated} points[/green]")
        report["collections"][name] = entry

    if not dry_run:
        # Recreate payload indexes and normalize collection config.
        VectorStore()
        TaskStore()

    console.print(
        "[dim]Note: chunked events are re-embedded from their full raw_content "
        "(original sub-chunk boundaries are not preserved).[/dim]"
    )
    return report
