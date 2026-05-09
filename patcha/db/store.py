"""Vector storage using Qdrant."""

import logging
import os
import uuid
from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    PointStruct,
    Filter,
    FieldCondition,
    MatchValue,
    Range,
    PointIdsList,
)

from patcha.db.models import Event, Category
from patcha.config import config

log = logging.getLogger(__name__)


class VectorStore:
    def __init__(self):
        if os.getenv("QDRANT_URL"):
            self.client = QdrantClient(url=config.qdrant_url, check_compatibility=False)
        else:
            config.qdrant_path.mkdir(parents=True, exist_ok=True)
            lock_file = config.qdrant_path / ".lock"
            if lock_file.exists():
                lock_file.unlink()
            self.client = QdrantClient(path=str(config.qdrant_path))
        self.collection_name = config.collection_name
        self.vector_size = config.vector_size
        self._ensure_collection_exists()

    def _ensure_collection_exists(self):
        try:
            collections = self.client.get_collections().collections
            collection_names = [col.name for col in collections]

            if self.collection_name not in collection_names:
                self.client.create_collection(
                    collection_name=self.collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size, distance=Distance.COSINE
                    ),
                )
                log.info("created collection: %s", self.collection_name)
            else:
                log.debug("collection already exists: %s", self.collection_name)
        except Exception as e:
            log.error("error ensuring collection exists: %s", e)

    def store_event(self, event: Event) -> bool:
        if not event.embedding:
            log.debug("skipping event %s — no embedding", event.type)
            return False

        try:
            if event.source_doc_id:
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, event.source_doc_id))
                log.debug(
                    "dedup: using deterministic id for source_doc_id=%s → point_id=%s",
                    event.source_doc_id,
                    point_id,
                )
            else:
                point_id = str(uuid.uuid4())
                log.debug("new point id (no source_doc_id): %s", point_id)

            payload = {
                "timestamp": event.timestamp.isoformat(),
                "date": event.timestamp.date().isoformat(),
                "type": event.type.value,
                "source": event.source,
                "project": event.project,
                "summary": event.summary,
                "category": event.category.value if event.category else None,
                "metadata": event.metadata,
                "raw_content": event.raw_content,
            }

            point = PointStruct(id=point_id, vector=event.embedding, payload=payload)

            self.client.upsert(collection_name=self.collection_name, points=[point])
            log.debug("upserted event type=%s point_id=%s", event.type, point_id)
            return True

        except Exception as e:
            log.error("error storing event: %s", e)
            return False

    def store_events(self, events: List[Event]) -> int:
        successful_stores = 0
        points = []

        for event in events:
            if not event.embedding:
                log.debug("skipping event %s — no embedding", event.type)
                continue

            try:
                if event.source_doc_id:
                    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, event.source_doc_id))
                    log.debug(
                        "dedup: deterministic id for source_doc_id=%s → %s",
                        event.source_doc_id,
                        point_id,
                    )
                else:
                    point_id = str(uuid.uuid4())
                    log.debug("new point id: %s type=%s", point_id, event.type)

                payload = {
                    "timestamp": event.timestamp.isoformat(),
                    "date": event.timestamp.date().isoformat(),
                    "type": event.type.value,
                    "source": event.source,
                    "project": event.project,
                    "summary": event.summary,
                    "category": event.category.value if event.category else None,
                    "metadata": event.metadata,
                    "raw_content": event.raw_content,
                }

                point = PointStruct(
                    id=point_id, vector=event.embedding, payload=payload
                )

                points.append(point)

            except Exception as e:
                log.error("error preparing event for storage: %s", e)
                continue

        if points:
            try:
                log.debug(
                    "upserting batch of %d points to collection %s",
                    len(points),
                    self.collection_name,
                )
                self.client.upsert(collection_name=self.collection_name, points=points)
                successful_stores = len(points)
                log.info("stored %d events", successful_stores)
            except Exception as e:
                log.error("error batch storing events: %s", e)

        skipped = len(events) - successful_stores
        if skipped:
            log.debug("skipped %d events (no embedding or prep error)", skipped)

        return successful_stores

    def search_events(
        self,
        query_vector: List[float],
        limit: int = 10,
        date_filter: Optional[date] = None,
        category_filter: Optional[Category] = None,
        project_filter: Optional[str] = None,
        type_filter: Optional[str] = None,
    ) -> List[Dict[str, Any]]:

        filter_conditions = []

        if date_filter:
            log.debug("search filter: date=%s", date_filter.isoformat())
            filter_conditions.append(
                FieldCondition(
                    key="date", match=MatchValue(value=date_filter.isoformat())
                )
            )

        if category_filter:
            log.debug("search filter: category=%s", category_filter.value)
            filter_conditions.append(
                FieldCondition(
                    key="category", match=MatchValue(value=category_filter.value)
                )
            )

        if project_filter:
            log.debug("search filter: project=%s", project_filter)
            filter_conditions.append(
                FieldCondition(key="project", match=MatchValue(value=project_filter))
            )

        if type_filter:
            log.debug("search filter: type=%s", type_filter)
            filter_conditions.append(
                FieldCondition(key="type", match=MatchValue(value=type_filter))
            )

        query_filter = Filter(must=filter_conditions) if filter_conditions else None
        log.debug(
            "searching with %d filter(s), limit=%d", len(filter_conditions), limit
        )

        try:
            response = self.client.query_points(
                collection_name=self.collection_name,
                query=query_vector,
                limit=limit,
                query_filter=query_filter,
                with_payload=True,
            )
            results = response.points
            log.debug("search returned %d results", len(results))

            return [
                {"id": result.id, "score": result.score, "payload": result.payload}
                for result in results
            ]

        except Exception as e:
            log.error("error searching events: %s", e)
            return []

    def get_events_by_date(
        self, target_date: date, exclude_compacted: bool = False
    ) -> List[Dict[str, Any]]:
        try:
            must = [
                FieldCondition(
                    key="date", match=MatchValue(value=target_date.isoformat())
                )
            ]
            must_not = (
                [FieldCondition(key="compacted", match=MatchValue(value=True))]
                if exclude_compacted
                else None
            )
            filter_condition = Filter(must=must, must_not=must_not)

            all_points = []
            next_offset = None
            while True:
                points, next_offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=filter_condition,
                    limit=config.max_events_per_day,
                    offset=next_offset,
                    with_vectors=True,
                )
                all_points.extend(points)
                if next_offset is None:
                    break

            return [
                {"id": point.id, "payload": point.payload, "vector": point.vector}
                for point in all_points
            ]

        except Exception as e:
            log.error("error getting events by date: %s", e)
            return []

    def get_events_by_category(
        self,
        category: Category,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None,
    ) -> List[Dict[str, Any]]:

        filter_conditions = [
            FieldCondition(key="category", match=MatchValue(value=category.value))
        ]

        if start_date and end_date:
            filter_conditions.append(
                FieldCondition(
                    key="date",
                    range=Range(gte=start_date.isoformat(), lte=end_date.isoformat()),
                )
            )

        try:
            all_points = []
            next_offset = None
            while True:
                points, next_offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=Filter(must=filter_conditions),
                    limit=config.max_events_per_day,
                    offset=next_offset,
                )
                all_points.extend(points)
                if next_offset is None:
                    break

            return [{"id": point.id, "payload": point.payload} for point in all_points]

        except Exception as e:
            log.error("error getting events by category: %s", e)
            return []

    def get_recent_events(
        self, since: datetime, limit: int = 200
    ) -> List[Dict[str, Any]]:
        today = date.today()
        since_date = since.date()
        payloads = []
        current = since_date
        while current <= today:
            payloads += [
                p["payload"]
                for p in self.get_events_by_date(current, exclude_compacted=True)
            ]
            current += timedelta(days=1)

        since_str = since.isoformat()
        recent = [p for p in payloads if p.get("timestamp", "") >= since_str]
        recent.sort(key=lambda p: p.get("timestamp", ""), reverse=True)
        return list(reversed(recent[:limit]))

    def get_recent_events_with_vectors(
        self, since: datetime, limit: int = 200
    ) -> List[Dict[str, Any]]:
        today = date.today()
        since_date = since.date()
        rows = []
        current = since_date
        while current <= today:
            rows += self.get_events_by_date(current, exclude_compacted=True)
            current += timedelta(days=1)

        since_str = since.isoformat()
        recent = [
            r for r in rows if r.get("payload", {}).get("timestamp", "") >= since_str
        ]
        recent.sort(
            key=lambda r: r.get("payload", {}).get("timestamp", ""), reverse=True
        )
        return list(reversed(recent[:limit]))

    def get_collection_info(self) -> Dict[str, Any]:
        try:
            info = self.client.get_collection(self.collection_name)
            return {
                "name": self.collection_name,
                "vectors_count": info.vectors_count,
                "points_count": info.points_count,
                "status": info.status,
            }
        except Exception as e:
            log.error("error getting collection info: %s", e)
            return {}

    def store_task(self, task) -> bool:
        if not task.embedding:
            return False
        try:
            payload = {
                "type": "task",
                "timestamp": task.start_time.isoformat(),
                "date": task.start_time.date().isoformat(),
                "summary": task.title,
                "raw_content": task.description or task.title,
                "project": task.project,
                "category": task.category.value if task.category else None,
                "source": "compaction",
                "metadata": {
                    "task_id": task.id,
                    "status": task.status.value,
                    "duration_minutes": task.duration_minutes,
                    "confidence_score": task.confidence_score,
                    "tags": task.tags,
                },
            }
            self.client.upsert(
                collection_name=self.collection_name,
                points=[
                    PointStruct(id=task.id, vector=task.embedding, payload=payload)
                ],
            )
            return True
        except Exception as e:
            log.error("error storing task in mem collection: %s", e)
            return False

    def mark_events_compacted(self, point_ids: List[str]) -> None:
        if not point_ids:
            return
        try:
            self.client.set_payload(
                collection_name=self.collection_name,
                payload={"compacted": True},
                points=PointIdsList(points=point_ids),
            )
        except Exception as e:
            log.error("error marking events compacted: %s", e)

    def delete_events_by_date(self, target_date: date) -> bool:
        try:
            filter_condition = Filter(
                must=[
                    FieldCondition(
                        key="date", match=MatchValue(value=target_date.isoformat())
                    )
                ]
            )

            self.client.delete(
                collection_name=self.collection_name, points_selector=filter_condition
            )

            return True

        except Exception as e:
            log.error("error deleting events by date: %s", e)
            return False
