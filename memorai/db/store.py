"""Vector storage using Qdrant."""

import uuid
from datetime import datetime, date
from typing import List, Optional, Dict, Any
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, Range

from memorai.db.models import Event, Category
from memorai.config import config


class VectorStore:
    def __init__(self):
        self.client = QdrantClient(url=config.qdrant_url, check_compatibility=False)
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
                        size=self.vector_size,
                        distance=Distance.COSINE
                    )
                )
                print(f"Created collection: {self.collection_name}")
        except Exception as e:
            print(f"Error ensuring collection exists: {e}")

    def store_event(self, event: Event) -> bool:
        if not event.embedding:
            print(f"Event {event.type} has no embedding, skipping storage")
            return False

        try:
            if event.source_doc_id:
                point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, event.source_doc_id))
            else:
                point_id = str(uuid.uuid4())

            payload = {
                "timestamp": event.timestamp.isoformat(),
                "date": event.timestamp.date().isoformat(),
                "type": event.type.value,
                "source": event.source,
                "project": event.project,
                "summary": event.summary,
                "category": event.category.value if event.category else None,
                "metadata": event.metadata,
                "raw_content": event.raw_content[:1000]
            }

            point = PointStruct(
                id=point_id,
                vector=event.embedding,
                payload=payload
            )

            self.client.upsert(
                collection_name=self.collection_name,
                points=[point]
            )

            return True

        except Exception as e:
            print(f"Error storing event: {e}")
            return False

    def store_events(self, events: List[Event]) -> int:
        successful_stores = 0
        points = []

        for event in events:
            if not event.embedding:
                continue

            try:
                if event.source_doc_id:
                    point_id = str(uuid.uuid5(uuid.NAMESPACE_URL, event.source_doc_id))
                else:
                    point_id = str(uuid.uuid4())

                payload = {
                    "timestamp": event.timestamp.isoformat(),
                    "date": event.timestamp.date().isoformat(),
                    "type": event.type.value,
                    "source": event.source,
                    "project": event.project,
                    "summary": event.summary,
                    "category": event.category.value if event.category else None,
                    "metadata": event.metadata,
                    "raw_content": event.raw_content[:1000]
                }

                point = PointStruct(
                    id=point_id,
                    vector=event.embedding,
                    payload=payload
                )

                points.append(point)

            except Exception as e:
                print(f"Error preparing event for storage: {e}")
                continue

        if points:
            try:
                self.client.upsert(
                    collection_name=self.collection_name,
                    points=points
                )
                successful_stores = len(points)
                print(f"Stored {successful_stores} events")
            except Exception as e:
                print(f"Error batch storing events: {e}")

        return successful_stores

    def search_events(
        self,
        query_vector: List[float],
        limit: int = 10,
        date_filter: Optional[date] = None,
        category_filter: Optional[Category] = None,
        project_filter: Optional[str] = None,
        type_filter: Optional[str] = None
    ) -> List[Dict[str, Any]]:

        filter_conditions = []

        if date_filter:
            filter_conditions.append(
                FieldCondition(
                    key="date",
                    match=MatchValue(value=date_filter.isoformat())
                )
            )

        if category_filter:
            filter_conditions.append(
                FieldCondition(
                    key="category",
                    match=MatchValue(value=category_filter.value)
                )
            )

        if project_filter:
            filter_conditions.append(
                FieldCondition(
                    key="project",
                    match=MatchValue(value=project_filter)
                )
            )

        if type_filter:
            filter_conditions.append(
                FieldCondition(
                    key="type",
                    match=MatchValue(value=type_filter)
                )
            )

        query_filter = Filter(must=filter_conditions) if filter_conditions else None

        try:
            results = self.client.search(
                collection_name=self.collection_name,
                query_vector=query_vector,
                limit=limit,
                query_filter=query_filter
            )

            return [
                {
                    "id": result.id,
                    "score": result.score,
                    "payload": result.payload
                }
                for result in results
            ]

        except Exception as e:
            print(f"Error searching events: {e}")
            return []

    def get_events_by_date(self, target_date: date) -> List[Dict[str, Any]]:
        try:
            filter_condition = Filter(
                must=[
                    FieldCondition(
                        key="date",
                        match=MatchValue(value=target_date.isoformat())
                    )
                ]
            )

            all_points = []
            next_offset = None
            while True:
                points, next_offset = self.client.scroll(
                    collection_name=self.collection_name,
                    scroll_filter=filter_condition,
                    limit=config.max_events_per_day,
                    offset=next_offset,
                    with_vectors=True
                )
                all_points.extend(points)
                if next_offset is None:
                    break

            return [
                {
                    "id": point.id,
                    "payload": point.payload,
                    "vector": point.vector
                }
                for point in all_points
            ]

        except Exception as e:
            print(f"Error getting events by date: {e}")
            return []

    def get_events_by_category(
        self,
        category: Category,
        start_date: Optional[date] = None,
        end_date: Optional[date] = None
    ) -> List[Dict[str, Any]]:

        filter_conditions = [
            FieldCondition(
                key="category",
                match=MatchValue(value=category.value)
            )
        ]

        if start_date and end_date:
            filter_conditions.append(
                FieldCondition(
                    key="date",
                    range=Range(
                        gte=start_date.isoformat(),
                        lte=end_date.isoformat()
                    )
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
                    offset=next_offset
                )
                all_points.extend(points)
                if next_offset is None:
                    break

            return [
                {
                    "id": point.id,
                    "payload": point.payload
                }
                for point in all_points
            ]

        except Exception as e:
            print(f"Error getting events by category: {e}")
            return []

    def get_collection_info(self) -> Dict[str, Any]:
        try:
            info = self.client.get_collection(self.collection_name)
            return {
                "name": self.collection_name,
                "vectors_count": info.vectors_count,
                "points_count": info.points_count,
                "status": info.status
            }
        except Exception as e:
            print(f"Error getting collection info: {e}")
            return {}

    def delete_events_by_date(self, target_date: date) -> bool:
        try:
            filter_condition = Filter(
                must=[
                    FieldCondition(
                        key="date",
                        match=MatchValue(value=target_date.isoformat())
                    )
                ]
            )

            self.client.delete(
                collection_name=self.collection_name,
                points_selector=filter_condition
            )

            return True

        except Exception as e:
            print(f"Error deleting events by date: {e}")
            return False
