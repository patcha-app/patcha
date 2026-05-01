"""Task storage and retrieval system using Qdrant and local JSON storage."""

import json
import uuid
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, Filter, FieldCondition, MatchValue, Range

from memorai.db.models import Task, TaskStatus, Category, TaskSearchResult, TaskPriority
from memorai.config import config


class TaskStore:
    def __init__(self):
        self.client = QdrantClient(url=config.qdrant_url, check_compatibility=False)
        self.tasks_collection_name = "tasks"
        self.vector_size = config.vector_size
        self.local_storage_path = config.data_dir / "tasks"
        self.local_storage_path.mkdir(parents=True, exist_ok=True)
        self._ensure_collection_exists()

    def _ensure_collection_exists(self):
        """Ensure the tasks collection exists in Qdrant."""
        try:
            collections = self.client.get_collections().collections
            collection_names = [col.name for col in collections]

            if self.tasks_collection_name not in collection_names:
                self.client.create_collection(
                    collection_name=self.tasks_collection_name,
                    vectors_config=VectorParams(
                        size=self.vector_size,
                        distance=Distance.COSINE
                    )
                )
                print(f"Created tasks collection: {self.tasks_collection_name}")
        except Exception as e:
            print(f"Error ensuring tasks collection exists: {e}")

    def store_task(self, task: Task) -> bool:
        """Store a task in both Qdrant and local JSON storage."""
        try:
            # Store in Qdrant if task has embedding
            if task.embedding:
                self._store_task_vector(task)

            # Store in local JSON
            self._store_task_local(task)

            return True
        except Exception as e:
            print(f"Error storing task {task.id}: {e}")
            return False

    def _store_task_vector(self, task: Task):
        """Store task in Qdrant vector database."""
        payload = {
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status.value,
            "priority": task.priority.value,
            "category": task.category.value if task.category else None,
            "project": task.project,
            "start_time": task.start_time.isoformat(),
            "end_time": task.end_time.isoformat() if task.end_time else None,
            "date": task.start_time.date().isoformat(),
            "duration_minutes": task.duration_minutes,
            "activity_count": task.activity_count,
            "confidence_score": task.confidence_score,
            "parent_task_id": task.parent_task_id,
            "tags": task.tags,
            "created_at": task.created_at.isoformat(),
            "updated_at": task.updated_at.isoformat(),
        }

        point = PointStruct(
            id=task.id,
            vector=task.embedding,
            payload=payload
        )

        self.client.upsert(
            collection_name=self.tasks_collection_name,
            points=[point]
        )

    def _store_task_local(self, task: Task):
        """Store task in local JSON file."""
        date_str = task.start_time.date().isoformat()
        file_path = self.local_storage_path / f"{date_str}_tasks.json"

        # Load existing tasks for the date
        tasks_data = {}
        if file_path.exists():
            with open(file_path, 'r') as f:
                tasks_data = json.load(f)

        # Add or update the task
        tasks_data[task.id] = task.model_dump(mode='json')

        # Save back to file
        with open(file_path, 'w') as f:
            json.dump(tasks_data, f, indent=2, default=str)

    def get_task(self, task_id: str) -> Optional[Task]:
        """Retrieve a task by ID."""
        try:
            # First try to find it in local storage
            for file_path in self.local_storage_path.glob("*_tasks.json"):
                with open(file_path, 'r') as f:
                    tasks_data = json.load(f)
                    if task_id in tasks_data:
                        return Task(**tasks_data[task_id])

            return None
        except Exception as e:
            print(f"Error retrieving task {task_id}: {e}")
            return None

    def get_tasks_by_date(self, target_date: date) -> List[Task]:
        """Get all tasks for a specific date."""
        try:
            date_str = target_date.isoformat()
            file_path = self.local_storage_path / f"{date_str}_tasks.json"

            if not file_path.exists():
                return []

            with open(file_path, 'r') as f:
                tasks_data = json.load(f)

            tasks = []
            for task_data in tasks_data.values():
                tasks.append(Task(**task_data))

            return sorted(tasks, key=lambda x: x.start_time)
        except Exception as e:
            print(f"Error getting tasks for date {target_date}: {e}")
            return []

    def get_tasks_by_date_range(
        self,
        start_date: date,
        end_date: date
    ) -> List[Task]:
        """Get all tasks within a date range."""
        all_tasks = []
        current_date = start_date

        while current_date <= end_date:
            daily_tasks = self.get_tasks_by_date(current_date)
            all_tasks.extend(daily_tasks)
            current_date += timedelta(days=1)

        return sorted(all_tasks, key=lambda x: x.start_time)

    def get_active_tasks(self) -> List[Task]:
        """Get all active tasks."""
        # Look in the last 7 days for active tasks
        end_date = date.today()
        start_date = end_date - timedelta(days=7)

        all_tasks = self.get_tasks_by_date_range(start_date, end_date)
        return [task for task in all_tasks if task.status == TaskStatus.ACTIVE]

    def search_tasks(
        self,
        query: str,
        query_vector: Optional[List[float]] = None,
        limit: int = 10,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[TaskSearchResult]:
        """Search tasks using vector similarity and/or text filters."""
        results = []

        try:
            # If we have a query vector, search in Qdrant
            if query_vector:
                # Build Qdrant filters
                qdrant_filters = []

                if filters:
                    if 'status' in filters:
                        qdrant_filters.append(
                            FieldCondition(key="status", match=MatchValue(value=filters['status']))
                        )
                    if 'category' in filters:
                        qdrant_filters.append(
                            FieldCondition(key="category", match=MatchValue(value=filters['category']))
                        )
                    if 'project' in filters:
                        qdrant_filters.append(
                            FieldCondition(key="project", match=MatchValue(value=filters['project']))
                        )
                    if 'date_from' in filters and 'date_to' in filters:
                        qdrant_filters.append(
                            FieldCondition(
                                key="date",
                                range=Range(
                                    gte=filters['date_from'],
                                    lte=filters['date_to']
                                )
                            )
                        )

                filter_condition = Filter(must=qdrant_filters) if qdrant_filters else None

                # Search in Qdrant
                search_results = self.client.search(
                    collection_name=self.tasks_collection_name,
                    query_vector=query_vector,
                    query_filter=filter_condition,
                    limit=limit
                )

                # Convert to TaskSearchResult
                for result in search_results:
                    task = self.get_task(result.payload["task_id"])
                    if task:
                        results.append(TaskSearchResult(
                            task=task,
                            score=result.score,
                            match_reason="vector_similarity"
                        ))

            # If no vector search or need more results, do text-based search
            if len(results) < limit and query:
                text_results = self._text_search_tasks(query, limit - len(results), filters)

                # Avoid duplicates
                existing_ids = {r.task.id for r in results}
                for text_result in text_results:
                    if text_result.task.id not in existing_ids:
                        results.append(text_result)

        except Exception as e:
            print(f"Error searching tasks: {e}")

        return results[:limit]

    def _text_search_tasks(
        self,
        query: str,
        limit: int,
        filters: Optional[Dict[str, Any]] = None
    ) -> List[TaskSearchResult]:
        """Perform text-based search on local task storage."""
        results = []
        query_lower = query.lower()

        # Search in recent files (last 30 days)
        end_date = date.today()
        start_date = end_date - timedelta(days=30)

        all_tasks = self.get_tasks_by_date_range(start_date, end_date)

        for task in all_tasks:
            # Apply filters
            if filters:
                if 'status' in filters and task.status.value != filters['status']:
                    continue
                if 'category' in filters and (not task.category or task.category.value != filters['category']):
                    continue
                if 'project' in filters and task.project != filters['project']:
                    continue

            # Check if query matches task content
            score = 0.0
            match_reasons = []

            # Title match (highest weight)
            if query_lower in task.title.lower():
                score += 0.5
                match_reasons.append("title")

            # Description match
            if task.description and query_lower in task.description.lower():
                score += 0.3
                match_reasons.append("description")

            # Tag match
            for tag in task.tags:
                if query_lower in tag.lower():
                    score += 0.2
                    match_reasons.append("tag")
                    break

            if score > 0:
                results.append(TaskSearchResult(
                    task=task,
                    score=score,
                    match_reason=", ".join(match_reasons)
                ))

        # Sort by score and return top results
        results.sort(key=lambda x: x.score, reverse=True)
        return results[:limit]

    def update_task(self, task: Task) -> bool:
        """Update an existing task."""
        task.updated_at = datetime.now()
        return self.store_task(task)

    def delete_task(self, task_id: str) -> bool:
        """Delete a task from both Qdrant and local storage."""
        try:
            # Delete from Qdrant if it exists
            try:
                self.client.delete(
                    collection_name=self.tasks_collection_name,
                    points_selector=[task_id]
                )
            except Exception:
                pass  # Task might not exist in Qdrant

            # Delete from local storage
            for file_path in self.local_storage_path.glob("*_tasks.json"):
                with open(file_path, 'r') as f:
                    tasks_data = json.load(f)

                if task_id in tasks_data:
                    del tasks_data[task_id]
                    with open(file_path, 'w') as f:
                        json.dump(tasks_data, f, indent=2, default=str)
                    break

            return True
        except Exception as e:
            print(f"Error deleting task {task_id}: {e}")
            return False

    def get_task_statistics(self, start_date: date, end_date: date) -> Dict[str, Any]:
        """Get comprehensive task statistics for a date range."""
        tasks = self.get_tasks_by_date_range(start_date, end_date)

        if not tasks:
            return {
                "total_tasks": 0,
                "completed_tasks": 0,
                "active_tasks": 0,
                "paused_tasks": 0,
                "abandoned_tasks": 0,
                "total_time_minutes": 0,
                "average_task_duration": 0,
                "tasks_by_category": {},
                "tasks_by_project": {},
                "tasks_by_priority": {},
                "productivity_score": 0.0
            }

        # Basic counts
        total_tasks = len(tasks)
        completed_tasks = len([t for t in tasks if t.status == TaskStatus.COMPLETED])
        active_tasks = len([t for t in tasks if t.status == TaskStatus.ACTIVE])
        paused_tasks = len([t for t in tasks if t.status == TaskStatus.PAUSED])
        abandoned_tasks = len([t for t in tasks if t.status == TaskStatus.ABANDONED])

        # Time calculations
        total_time_minutes = sum(t.duration_minutes or 0 for t in tasks)
        average_duration = total_time_minutes / total_tasks if total_tasks > 0 else 0

        # Category breakdown
        tasks_by_category = {}
        for task in tasks:
            if task.category:
                category = task.category.value
                if category not in tasks_by_category:
                    tasks_by_category[category] = {"count": 0, "time_minutes": 0}
                tasks_by_category[category]["count"] += 1
                tasks_by_category[category]["time_minutes"] += task.duration_minutes or 0

        # Project breakdown
        tasks_by_project = {}
        for task in tasks:
            if task.project:
                project = task.project
                if project not in tasks_by_project:
                    tasks_by_project[project] = {"count": 0, "time_minutes": 0}
                tasks_by_project[project]["count"] += 1
                tasks_by_project[project]["time_minutes"] += task.duration_minutes or 0

        # Priority breakdown
        tasks_by_priority = {}
        for task in tasks:
            priority = task.priority.value
            if priority not in tasks_by_priority:
                tasks_by_priority[priority] = {"count": 0, "time_minutes": 0}
            tasks_by_priority[priority]["count"] += 1
            tasks_by_priority[priority]["time_minutes"] += task.duration_minutes or 0

        # Productivity score (0-1 based on completion rate and average confidence)
        completion_rate = completed_tasks / total_tasks if total_tasks > 0 else 0
        average_confidence = sum(t.confidence_score for t in tasks) / total_tasks if total_tasks > 0 else 0
        productivity_score = (completion_rate * 0.7) + (average_confidence * 0.3)

        return {
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "active_tasks": active_tasks,
            "paused_tasks": paused_tasks,
            "abandoned_tasks": abandoned_tasks,
            "total_time_minutes": total_time_minutes,
            "average_task_duration": round(average_duration, 1),
            "tasks_by_category": tasks_by_category,
            "tasks_by_project": tasks_by_project,
            "tasks_by_priority": tasks_by_priority,
            "productivity_score": round(productivity_score, 2)
        }

    def get_recent_tasks(self, limit: int = 20) -> List[Task]:
        """Get the most recent tasks."""
        # Look in the last 7 days
        end_date = date.today()
        start_date = end_date - timedelta(days=7)

        tasks = self.get_tasks_by_date_range(start_date, end_date)
        tasks.sort(key=lambda x: x.updated_at or x.created_at, reverse=True)
        return tasks[:limit]

    def get_tasks_by_status(self, status: TaskStatus) -> List[Task]:
        """Get all tasks with a specific status."""
        # Look in the last 30 days
        end_date = date.today()
        start_date = end_date - timedelta(days=30)

        tasks = self.get_tasks_by_date_range(start_date, end_date)
        return [task for task in tasks if task.status == status]
