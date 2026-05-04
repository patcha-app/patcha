"""Task-aware search functionality that searches both tasks and activities."""

from datetime import datetime, date, timedelta
from typing import List, Optional, Dict, Any
from openai import OpenAI

from patcha.db.models import Task, TaskSearchResult, Category
from patcha.db.tasks import TaskStore
from patcha.db.store import VectorStore
from patcha.process import EventPreprocessor
from patcha.config import config


class TaskAwareSearchService:
    def __init__(
        self,
        task_store: TaskStore,
        vector_store: VectorStore,
        preprocessor: EventPreprocessor,
    ):
        self.task_store = task_store
        self.vector_store = vector_store
        self.preprocessor = preprocessor
        self.client = OpenAI(api_key=config.openai_api_key)
        self.model_name = "gpt-4o-mini"

    def search(
        self,
        query: str,
        search_type: str = "both",  # "tasks", "activities", "both"
        limit: int = 10,
        date_filter: Optional[date] = None,
        category_filter: Optional[Category] = None,
        project_filter: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Perform task-aware search across tasks and activities.

        Args:
            query: Search query
            search_type: What to search - "tasks", "activities", or "both"
            limit: Maximum number of results
            date_filter: Filter by specific date
            category_filter: Filter by category
            project_filter: Filter by project

        Returns:
            Dictionary containing task results, activity results, and metadata
        """
        # Generate embedding for the query
        query_embedding = self.preprocessor.generate_embedding(query)

        # Build filters
        filters = {}
        if date_filter:
            filters["date_from"] = date_filter.isoformat()
            filters["date_to"] = date_filter.isoformat()
        if category_filter:
            filters["category"] = category_filter.value
        if project_filter:
            filters["project"] = project_filter

        results = {
            "query": query,
            "search_type": search_type,
            "task_results": [],
            "activity_results": [],
            "combined_results": [],
            "total_results": 0,
            "search_metadata": {
                "filters_applied": filters,
                "has_embedding": query_embedding is not None,
                "timestamp": datetime.now().isoformat(),
            },
        }

        # Search tasks
        if search_type in ["tasks", "both"]:
            task_results = self.task_store.search_tasks(
                query=query, query_vector=query_embedding, limit=limit, filters=filters
            )
            results["task_results"] = [
                self._format_task_result(tr) for tr in task_results
            ]

        # Search activities
        if search_type in ["activities", "both"]:
            activity_results = self._search_activities(
                query, query_embedding, limit, filters
            )
            results["activity_results"] = activity_results

        # Combine and rank results if searching both
        if search_type == "both":
            combined_results = self._combine_and_rank_results(
                results["task_results"], results["activity_results"], limit
            )
            results["combined_results"] = combined_results

        results["total_results"] = len(results["task_results"]) + len(
            results["activity_results"]
        )

        return results

    def _search_activities(
        self,
        query: str,
        query_vector: Optional[List[float]],
        limit: int,
        filters: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        """Search activities using the vector store."""
        try:
            # Build Qdrant-style date filter
            date_from = filters.get("date_from")
            date_to = filters.get("date_to")

            activity_results = self.vector_store.search_events(
                query=query if not query_vector else None,
                query_vector=query_vector,
                limit=limit,
                date=date_from if date_from == date_to else None,
                category=filters.get("category"),
                project=filters.get("project"),
            )

            formatted_results = []
            for result in activity_results:
                formatted_results.append(
                    {
                        "type": "activity",
                        "score": result.get("score", 0.0),
                        "event_data": result["payload"],
                        "match_reason": "vector_similarity"
                        if query_vector
                        else "text_match",
                        "task_id": result["payload"].get("task_id"),
                        "summary": result["payload"].get("summary", ""),
                        "category": result["payload"].get("category"),
                        "project": result["payload"].get("project"),
                        "timestamp": result["payload"].get("timestamp"),
                    }
                )

            return formatted_results

        except Exception as e:
            print(f"Error searching activities: {e}")
            return []

    def _format_task_result(self, task_result: TaskSearchResult) -> Dict[str, Any]:
        """Format a TaskSearchResult for consistent output."""
        task = task_result.task
        return {
            "type": "task",
            "score": task_result.score,
            "task_id": task.id,
            "title": task.title,
            "description": task.description,
            "status": task.status.value,
            "priority": task.priority.value,
            "category": task.category.value if task.category else None,
            "project": task.project,
            "start_time": task.start_time.isoformat(),
            "end_time": task.end_time.isoformat() if task.end_time else None,
            "duration_minutes": task.duration_minutes,
            "activity_count": task.activity_count,
            "confidence_score": task.confidence_score,
            "tags": task.tags,
            "match_reason": task_result.match_reason,
            "matching_activities": task_result.matching_activities,
        }

    def _combine_and_rank_results(
        self,
        task_results: List[Dict[str, Any]],
        activity_results: List[Dict[str, Any]],
        limit: int,
    ) -> List[Dict[str, Any]]:
        """Combine task and activity results and rank them by relevance."""
        all_results = []

        # Add task results with boosted scores (tasks are more important)
        for result in task_results:
            result = result.copy()
            result["score"] *= 1.2  # Boost task scores
            all_results.append(result)

        # Add activity results
        for result in activity_results:
            # If this activity belongs to a task that's already in results, skip it
            # to avoid duplication
            task_id = result.get("task_id")
            if task_id:
                task_already_included = any(
                    r.get("task_id") == task_id
                    for r in all_results
                    if r["type"] == "task"
                )
                if task_already_included:
                    continue

            all_results.append(result)

        # Sort by score and return top results
        all_results.sort(key=lambda x: x["score"], reverse=True)
        return all_results[:limit]

    def search_tasks_by_content(
        self, query: str, include_activities: bool = True, limit: int = 10
    ) -> List[Dict[str, Any]]:
        """
        Search for tasks and optionally include their activity details.
        """
        # Get query embedding
        query_embedding = self.preprocessor.generate_embedding(query)

        # Search tasks
        task_results = self.task_store.search_tasks(
            query=query, query_vector=query_embedding, limit=limit
        )

        enriched_results = []
        for task_result in task_results:
            task = task_result.task
            result_data = self._format_task_result(task_result)

            if include_activities and task.activities:
                # Get activity details for this task
                activity_details = self._get_task_activities(task)
                result_data["activities"] = activity_details

            enriched_results.append(result_data)

        return enriched_results

    def _get_task_activities(self, task: Task) -> List[Dict[str, Any]]:
        """Get detailed activity information for a task."""
        activities = []

        # This is a simplified version - in practice you'd need to store
        # activity IDs more systematically or search for them
        try:
            # Search for activities that belong to this task
            task_activities = self.vector_store.search_events(
                query=None,
                query_vector=None,
                limit=100,  # Get all activities for the task
                filters={"task_id": task.id},
            )

            for activity_data in task_activities:
                activities.append(
                    {
                        "type": activity_data["payload"].get("type"),
                        "timestamp": activity_data["payload"].get("timestamp"),
                        "summary": activity_data["payload"].get("summary"),
                        "category": activity_data["payload"].get("category"),
                        "source": activity_data["payload"].get("source"),
                    }
                )

        except Exception as e:
            print(f"Error getting activities for task {task.id}: {e}")

        return activities

    def get_related_tasks(self, task: Task, limit: int = 5) -> List[Dict[str, Any]]:
        """Find tasks related to the given task."""
        if not task.embedding:
            return []

        try:
            # Search for similar tasks using the task's embedding
            similar_tasks = self.task_store.search_tasks(
                query="",  # Empty query, rely on vector search
                query_vector=task.embedding,
                limit=limit + 1,  # +1 because the original task might be included
            )

            # Remove the original task from results
            related_tasks = []
            for task_result in similar_tasks:
                if task_result.task.id != task.id:
                    related_tasks.append(self._format_task_result(task_result))

            return related_tasks[:limit]

        except Exception as e:
            print(f"Error finding related tasks: {e}")
            return []

    def search_by_time_range(
        self,
        start_date: date,
        end_date: date,
        query: Optional[str] = None,
        search_type: str = "both",
    ) -> Dict[str, Any]:
        """Search within a specific time range."""
        results = {
            "time_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
            },
            "tasks": [],
            "activities": [],
        }

        # Get tasks in date range
        if search_type in ["tasks", "both"]:
            tasks = self.task_store.get_tasks_by_date_range(start_date, end_date)

            # Filter by query if provided
            if query:
                query_lower = query.lower()
                filtered_tasks = []
                for task in tasks:
                    if (
                        query_lower in task.title.lower()
                        or (
                            task.description and query_lower in task.description.lower()
                        )
                        or any(query_lower in tag.lower() for tag in task.tags)
                    ):
                        filtered_tasks.append(task)
                tasks = filtered_tasks

            results["tasks"] = [
                {
                    "task_id": task.id,
                    "title": task.title,
                    "description": task.description,
                    "status": task.status.value,
                    "category": task.category.value if task.category else None,
                    "start_time": task.start_time.isoformat(),
                    "duration_minutes": task.duration_minutes,
                    "activity_count": task.activity_count,
                }
                for task in tasks
            ]

        # Get activities in date range
        if search_type in ["activities", "both"]:
            current_date = start_date
            while current_date <= end_date:
                daily_activities = self.vector_store.get_events_by_date(current_date)

                # Filter by query if provided
                if query:
                    query_lower = query.lower()
                    filtered_activities = []
                    for activity in daily_activities:
                        payload = activity["payload"]
                        summary = payload.get("summary", "")
                        if query_lower in summary.lower():
                            filtered_activities.append(activity)
                    daily_activities = filtered_activities

                # Add to results
                for activity in daily_activities:
                    results["activities"].append(
                        {
                            "type": activity["payload"].get("type"),
                            "timestamp": activity["payload"].get("timestamp"),
                            "summary": activity["payload"].get("summary"),
                            "category": activity["payload"].get("category"),
                            "project": activity["payload"].get("project"),
                            "task_id": activity["payload"].get("task_id"),
                        }
                    )

                current_date += timedelta(days=1)

        return results
