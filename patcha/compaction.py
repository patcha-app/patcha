import json
import logging
import threading
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
from patcha.api_client import PatchaLLMClient
from sklearn.cluster import DBSCAN, HDBSCAN
from sklearn.metrics.pairwise import cosine_similarity

from patcha.config import config
from patcha.config import settings as _settings
from patcha.db.activity_graph import ActivityGraph
from patcha.db.models import Category, Event, EventType, Task, TaskPriority, TaskStatus
from patcha.db.retrieval.rag import RAGSystem
from patcha.db.store import VectorStore
from patcha.db.tasks import TaskStore
from patcha.process import EventPreprocessor
from patcha.prompts import render_system, render_user

log = logging.getLogger(__name__)

_STATE_FILE = config.data_dir / "daily_compaction.json"


def _load_state() -> dict:
    if _STATE_FILE.exists():
        try:
            with open(_STATE_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return {"compacted_dates": [], "last_trigger_date": None}


def _save_state(state: dict):
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(_STATE_FILE, "w") as f:
        json.dump(state, f)


class LLMTaskAnalyzer:
    def __init__(self, rag_system=None):
        self.client = PatchaLLMClient()
        self.model_name = "gpt-4o-mini"
        self.rag_system = rag_system

    def analyze_task_chunk(
        self, activities: List[Event], use_rag: bool = True
    ) -> Dict[str, Any]:
        if not activities:
            return self._empty_task_analysis()

        aggregated_content = self._aggregate_activity_content(activities)
        base_analysis = self._generate_llm_analysis(aggregated_content, activities)

        if use_rag and self.rag_system and base_analysis:
            try:
                enhanced_analysis = self.rag_system.enhance_task_analysis_with_rag(
                    activities, base_analysis
                )
                return enhanced_analysis
            except Exception as e:
                print(f"RAG enhancement failed, using base analysis: {e}")
                return base_analysis

        return base_analysis

    def _aggregate_activity_content(self, activities: List[Event]) -> str:
        content_parts = []

        for i, activity in enumerate(activities, 1):
            timestamp = activity.timestamp.strftime("%H:%M")
            project_tag = f" | project: {activity.project}" if activity.project else ""
            source_tag = f" ({activity.source})" if activity.source else ""
            header = (
                f"{i}. [{timestamp}] {activity.type.value}{project_tag}{source_tag}"
            )
            detail_lines = []

            if activity.type == EventType.TERMINAL:
                try:
                    data = json.loads(activity.raw_content)
                    cmd = data.get("command", "").strip()
                    detail_lines.append(
                        f"   CMD: {cmd}"
                        if cmd
                        else f"   {activity.summary or activity.raw_content}"
                    )
                except (json.JSONDecodeError, TypeError):
                    detail_lines.append(
                        f"   {activity.summary or activity.raw_content}"
                    )

            elif activity.type == EventType.BROWSER:
                try:
                    data = json.loads(activity.raw_content)
                    url = data.get("url", "")
                    title = data.get("title", "")
                    if url:
                        detail_lines.append(f"   URL: {url}")
                    if title:
                        detail_lines.append(f"   TITLE: {title}")
                    if not url and not title:
                        detail_lines.append(
                            f"   {activity.summary or activity.raw_content}"
                        )
                except (json.JSONDecodeError, TypeError):
                    detail_lines.append(
                        f"   {activity.summary or activity.raw_content}"
                    )

            elif activity.type in (EventType.GIT_COMMIT, EventType.GIT_STASH):
                try:
                    data = json.loads(activity.raw_content)
                    message = data.get("message", "").strip()
                    branch = data.get("branch", "")
                    files = data.get("files_changed", [])
                    if branch and message:
                        detail_lines.append(
                            f'   BRANCH: {branch} | COMMIT: "{message}"'
                        )
                    elif message:
                        detail_lines.append(f'   COMMIT: "{message}"')
                    if files:
                        detail_lines.append(f"   FILES: {', '.join(files[:5])}")
                except (json.JSONDecodeError, TypeError):
                    detail_lines.append(
                        f"   {activity.summary or activity.raw_content}"
                    )

            elif activity.type == EventType.GIT_STAGED:
                detail_lines.append(f"   {activity.raw_content}")

            elif activity.type == EventType.SCREEN:
                app = activity.metadata.get("app_name", "")
                window = activity.metadata.get("window_title", "")
                if app:
                    detail_lines.append(
                        f"   APP: {app}" + (f" | WINDOW: {window}" if window else "")
                    )
                text = activity.raw_content.strip()
                if text:
                    detail_lines.append(f"   TEXT: {text}")

            elif activity.type == EventType.WINDOW:
                app = activity.metadata.get("app_name", "")
                window = activity.metadata.get("window_title", "")
                if app:
                    detail_lines.append(
                        f"   APP: {app}" + (f" | WINDOW: {window}" if window else "")
                    )
                elif activity.summary or activity.raw_content:
                    detail_lines.append(
                        f"   {activity.summary or activity.raw_content}"
                    )

            else:
                detail_lines.append(
                    f"   {activity.summary or activity.raw_content or f'{activity.type.value} activity'}"
                )

            content_parts.append(header)
            content_parts.extend(detail_lines)

        return "\n".join(content_parts)

    def _generate_llm_analysis(
        self, content: str, activities: List[Event]
    ) -> Dict[str, Any]:
        start_time = min(activity.timestamp for activity in activities)
        end_time = max(activity.timestamp for activity in activities)
        duration_minutes = int((end_time - start_time).total_seconds() / 60)

        categories = [a.category.value for a in activities if a.category]
        primary_category = (
            max(set(categories), key=categories.count) if categories else "Other"
        )

        projects = [a.project for a in activities if a.project]
        primary_project = max(set(projects), key=projects.count) if projects else None

        user_prompt = render_user(
            "task_analysis",
            activities=content,
            duration_minutes=duration_minutes,
            activity_count=len(activities),
            start_time=start_time.strftime("%H:%M"),
            end_time=end_time.strftime("%H:%M"),
            primary_category=primary_category,
            primary_project=primary_project or "Personal",
            accomplishment_count=min(len(activities), 8),
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return self._parse_llm_response(
                response.choices[0].message.content, activities
            )

        except Exception as e:
            print(f"Error generating LLM analysis: {e}")
            return self._fallback_analysis(activities)

    def _parse_llm_response(
        self, response_text: str, activities: List[Event]
    ) -> Dict[str, Any]:
        analysis = {
            "title": "Unknown Task",
            "accomplishments": [],
            "description": "Task analysis unavailable",
            "themes": [],
            "confidence_score": 0.8,
        }

        lines = response_text.strip().split("\n")
        current_section = None

        for line in lines:
            line = line.strip()
            if not line:
                continue

            if line.startswith("TASK_TITLE:"):
                analysis["title"] = line.replace("TASK_TITLE:", "").strip()
            elif line.startswith("ACCOMPLISHMENTS:"):
                current_section = "accomplishments"
            elif line.startswith("DESCRIPTION:"):
                analysis["description"] = line.replace("DESCRIPTION:", "").strip()
                current_section = None
            elif line.startswith("MAIN_THEMES:"):
                themes_text = line.replace("MAIN_THEMES:", "").strip()
                analysis["themes"] = [theme.strip() for theme in themes_text.split(",")]
                current_section = None
            elif current_section == "accomplishments" and line.startswith("-"):
                accomplishment = line.replace("-", "").strip()
                if accomplishment:
                    analysis["accomplishments"].append(accomplishment)

        analysis["activity_count"] = len(activities)
        analysis["start_time"] = min(activity.timestamp for activity in activities)
        analysis["end_time"] = max(activity.timestamp for activity in activities)
        analysis["duration_minutes"] = int(
            (analysis["end_time"] - analysis["start_time"]).total_seconds() / 60
        )

        return analysis

    def _fallback_analysis(self, activities: List[Event]) -> Dict[str, Any]:
        categories = [a.category.value for a in activities if a.category]
        primary_category = (
            max(set(categories), key=categories.count) if categories else "General"
        )

        snippets = []
        for a in activities:
            text = (a.summary or a.raw_content or "").strip()
            if text:
                snippets.append(text[:120])

        title = snippets[0][:50] if snippets else f"{primary_category} Work"
        description = (
            "; ".join(snippets[:3])
            if snippets
            else f"{len(activities)} {primary_category.lower()} activities"
        )

        return {
            "title": title,
            "accomplishments": snippets[:3],
            "description": description,
            "themes": [primary_category],
            "confidence_score": 0.4,
            "activity_count": len(activities),
            "start_time": min(a.timestamp for a in activities),
            "end_time": max(a.timestamp for a in activities),
            "duration_minutes": int(
                (
                    max(a.timestamp for a in activities)
                    - min(a.timestamp for a in activities)
                ).total_seconds()
                / 60
            ),
        }

    def _empty_task_analysis(self) -> Dict[str, Any]:
        return {
            "title": "Empty Task",
            "accomplishments": [],
            "description": "No activities found",
            "themes": [],
            "confidence_score": 0.0,
            "activity_count": 0,
            "start_time": datetime.now(),
            "end_time": datetime.now(),
            "duration_minutes": 0,
        }


class TaskIdentifier:
    def __init__(
        self,
        preprocessor: EventPreprocessor,
        vector_store: Optional[VectorStore] = None,
    ):
        self.preprocessor = preprocessor

        self.rag_system = None
        if vector_store:
            self.rag_system = RAGSystem(vector_store, preprocessor)

        self.llm_analyzer = LLMTaskAnalyzer(rag_system=self.rag_system)
        self.client = PatchaLLMClient()
        self.model_name = "gpt-4o-mini"
        self._thread_local = threading.local()

    def identify_tasks_from_activities(
        self,
        activities: List[Event],
        similarity_threshold: float = 0.7,
        min_activities_per_task: int = 2,
        use_ai_enhancement: bool = True,
        max_workers: int = 6,
        max_activities_per_task: int = 30,
        clustering_method: str = "hdbscan",
    ) -> List[Task]:
        if len(activities) < min_activities_per_task:
            return []

        activities = sorted(activities, key=lambda x: x.timestamp)

        print(
            f"Creating semantic chunks from {len(activities)} activities using {clustering_method}..."
        )
        initial_chunks = self._create_semantic_chunks(
            activities, similarity_threshold, min_activities_per_task, clustering_method
        )
        print(
            f"Created {len(initial_chunks)} initial semantic chunks (outliers filtered)"
        )

        chunks = self._split_oversized_chunks(initial_chunks, max_activities_per_task)
        if len(chunks) != len(initial_chunks):
            print(
                f"Split large chunks: {len(initial_chunks)} -> {len(chunks)} chunks (max {max_activities_per_task} activities per task)"
            )

        all_tasks = []
        if use_ai_enhancement:
            print(f"Analyzing {len(chunks)} chunks with parallel LLM processing...")
            all_tasks = self._process_chunks_parallel(
                chunks, max_workers=min(len(chunks), max_workers)
            )
        else:
            print("Creating tasks without LLM analysis...")
            for chunk in chunks:
                task = self._create_basic_task_from_chunk(chunk)
                if task:
                    all_tasks.append(task)

        print(f"Generated {len(all_tasks)} meaningful tasks")
        return all_tasks

    def _create_semantic_chunks(
        self,
        activities: List[Event],
        similarity_threshold: float,
        min_activities_per_chunk: int,
        clustering_method: str = "hdbscan",
    ) -> List[List[Event]]:
        project_groups: Dict[str, List[Event]] = defaultdict(list)
        for a in activities:
            key = a.project if a.project is not None else "__none__"
            project_groups[key].append(a)

        all_chunks: List[List[Event]] = []
        for group_events in project_groups.values():
            all_chunks.extend(
                self._cluster_project_group(
                    group_events,
                    similarity_threshold,
                    min_activities_per_chunk,
                    clustering_method,
                )
            )
        return all_chunks

    def _cluster_project_group(
        self,
        activities: List[Event],
        similarity_threshold: float,
        min_activities_per_chunk: int,
        clustering_method: str = "hdbscan",
    ) -> List[List[Event]]:
        activities_with_embeddings = [a for a in activities if a.embedding is not None]

        if len(activities_with_embeddings) < min_activities_per_chunk:
            return []

        embeddings = np.array([a.embedding for a in activities_with_embeddings])

        if clustering_method == "hdbscan":
            clusterer = HDBSCAN(
                min_cluster_size=min_activities_per_chunk,
                min_samples=max(1, min_activities_per_chunk - 1),
                metric="cosine",
                cluster_selection_epsilon=1 - similarity_threshold
                if similarity_threshold > 0.5
                else None,
                cluster_selection_method="eom",
            )
            cluster_labels = clusterer.fit_predict(embeddings)
        else:
            similarity_matrix = cosine_similarity(embeddings)
            distance_matrix = np.clip(1 - similarity_matrix, 0, 2)
            clusterer = DBSCAN(
                eps=1 - similarity_threshold,
                min_samples=min_activities_per_chunk,
                metric="precomputed",
            )
            cluster_labels = clusterer.fit_predict(distance_matrix)

        chunks: Dict[int, List[Event]] = defaultdict(list)
        outliers = []

        for idx, label in enumerate(cluster_labels):
            if label == -1:
                outliers.append(activities_with_embeddings[idx])
            else:
                chunks[label].append(activities_with_embeddings[idx])

        valid_chunks = [
            c for c in chunks.values() if len(c) >= min_activities_per_chunk
        ]

        if (
            not valid_chunks
            and len(activities_with_embeddings) >= min_activities_per_chunk
        ):
            valid_chunks = [activities_with_embeddings]

        print(f"  Found {len(outliers)} outliers (excluded from chunks)")
        return valid_chunks

    def _fallback_category_chunks(
        self, activities: List[Event], min_activities_per_chunk: int
    ) -> List[List[Event]]:
        category_groups = defaultdict(list)
        for activity in activities:
            category = activity.category.value if activity.category else "Other"
            category_groups[category].append(activity)

        return [
            chunk
            for chunk in category_groups.values()
            if len(chunk) >= min_activities_per_chunk
        ]

    def _split_oversized_chunks(
        self, chunks: List[List[Event]], max_size: int
    ) -> List[List[Event]]:
        result_chunks = []

        for chunk in chunks:
            if len(chunk) <= max_size:
                result_chunks.append(chunk)
            else:
                print(
                    f"  Splitting chunk with {len(chunk)} activities into smaller sub-chunks..."
                )
                sub_chunks = self._split_chunk_temporally(chunk, max_size)
                result_chunks.extend(sub_chunks)
                print(f"    -> Created {len(sub_chunks)} sub-chunks")

        return result_chunks

    def _split_chunk_temporally(
        self, chunk: List[Event], max_size: int
    ) -> List[List[Event]]:
        sorted_activities = sorted(chunk, key=lambda x: x.timestamp)

        sub_chunks = []
        current_chunk = []

        for activity in sorted_activities:
            if len(current_chunk) >= max_size:
                if current_chunk:
                    sub_chunks.append(current_chunk)
                current_chunk = [activity]
            else:
                current_chunk.append(activity)

        if current_chunk:
            sub_chunks.append(current_chunk)

        return self._merge_tiny_chunks(sub_chunks, min_size=2)

    def _merge_tiny_chunks(
        self, chunks: List[List[Event]], min_size: int
    ) -> List[List[Event]]:
        if not chunks:
            return []

        merged_chunks = []
        i = 0

        while i < len(chunks):
            current_chunk = chunks[i]

            if len(current_chunk) < min_size and i + 1 < len(chunks):
                next_chunk = chunks[i + 1]
                if len(current_chunk) + len(next_chunk) <= min_size * 3:
                    merged_chunk = current_chunk + next_chunk
                    merged_chunks.append(merged_chunk)
                    i += 2
                    continue

            merged_chunks.append(current_chunk)
            i += 1

        return merged_chunks

    def _create_task_from_llm_analysis(self, chunk: List[Event]) -> Optional[Task]:
        try:
            analysis = self.llm_analyzer.analyze_task_chunk(chunk)

            if not analysis or analysis["confidence_score"] < 0.3:
                return None

            task = self._build_task_from_analysis(chunk, analysis)
            return task

        except Exception as e:
            print(f"Error in LLM task analysis: {e}")
            return self._create_basic_task_from_chunk(chunk)

    def _create_basic_task_from_chunk(self, chunk: List[Event]) -> Optional[Task]:
        if not chunk:
            return None
        return self._create_task_from_activities(chunk)

    def _build_task_from_analysis(
        self, chunk: List[Event], analysis: Dict[str, Any]
    ) -> Task:
        start_time = analysis["start_time"]
        end_time = analysis["end_time"]
        duration_minutes = analysis["duration_minutes"]

        categories = [a.category for a in chunk if a.category]
        category = max(set(categories), key=categories.count) if categories else None

        projects = [a.project for a in chunk if a.project]
        project = max(set(projects), key=projects.count) if projects else None

        activity_ids = [f"{a.type.value}_{a.timestamp.isoformat()}" for a in chunk]

        embeddings = [a.embedding for a in chunk if a.embedding]
        average_embedding = None
        if embeddings:
            average_embedding = np.mean(embeddings, axis=0).tolist()

        task = Task(
            title=analysis["title"],
            description=analysis["description"],
            status=TaskStatus.COMPLETED
            if end_time < datetime.now(end_time.tzinfo) - timedelta(hours=1)
            else TaskStatus.ACTIVE,
            category=category,
            project=project,
            start_time=start_time,
            end_time=end_time,
            last_activity_time=end_time,
            duration_minutes=duration_minutes,
            activities=activity_ids,
            activity_count=len(chunk),
            embedding=average_embedding,
            confidence_score=analysis["confidence_score"],
            tags=analysis["themes"],
        )

        task.description = analysis["description"]
        if analysis["accomplishments"]:
            task.metadata["accomplishments"] = analysis["accomplishments"]

        return task

    def _generate_descriptive_title(self, task: Task, all_activities: List[Event]):
        task_activities = []
        for activity in all_activities:
            activity_id = f"{activity.type.value}_{activity.timestamp.isoformat()}"
            if activity_id in task.activities:
                task_activities.append(activity)

        if not task_activities:
            task.title = f"{task.category.value if task.category else 'Unknown'} Task"
            task.description = f"Task involving {task.activity_count} activities"
            return

        browser_patterns = self._analyze_browser_activities(task_activities)
        git_patterns = self._analyze_git_activities(task_activities)
        terminal_patterns = self._analyze_terminal_activities(task_activities)

        title_parts = []
        description_parts = []

        if browser_patterns["keywords"]:
            if len(browser_patterns["keywords"]) <= 2:
                title_parts.extend(browser_patterns["keywords"])
            else:
                title_parts.append(f"{browser_patterns['keywords'][0]} research")
            description_parts.append(f"Browsed {browser_patterns['sites_count']} sites")

        if git_patterns["keywords"]:
            title_parts.extend(git_patterns["keywords"][:2])
            description_parts.append(f"Git work on {git_patterns['files_count']} files")

        if terminal_patterns["keywords"]:
            title_parts.extend(terminal_patterns["keywords"][:2])
            description_parts.append(
                f"Terminal commands: {terminal_patterns['command_types']}"
            )

        if title_parts:
            unique_parts = list(dict.fromkeys(title_parts))
            task.title = " ".join(word.capitalize() for word in unique_parts[:3])
            if len(task.title) < 10:
                task.title += f" ({task.category.value if task.category else 'Work'})"
        else:
            task.title = f"{task.project or task.category.value if task.category else 'General'} Work"

        if description_parts:
            task.description = "; ".join(description_parts)
        else:
            task.description = f"Task involving {task.activity_count} activities over {task.duration_minutes} minutes"

    def _analyze_browser_activities(self, activities: List[Event]) -> dict:
        keywords = set()
        domains = set()

        for activity in activities:
            if activity.type.value.upper() == "BROWSER":
                content = activity.summary or activity.raw_content or ""
                content_lower = content.lower()

                if "youtube" in content_lower:
                    domains.add("YouTube")
                if "google" in content_lower:
                    domains.add("Google")
                if "github" in content_lower:
                    domains.add("GitHub")
                if "chatgpt" in content_lower:
                    domains.add("ChatGPT")

                if "iphone" in content_lower:
                    keywords.add("iPhone")
                if "samsung" in content_lower:
                    keywords.add("Samsung")
                if "ios" in content_lower:
                    keywords.add("iOS")
                if "calendar" in content_lower:
                    keywords.add("Calendar")
                if "camera" in content_lower:
                    keywords.add("Camera")
                if "comparison" in content_lower or "compare" in content_lower:
                    keywords.add("Comparison")
                if "tui" in content_lower and "best" in content_lower:
                    keywords.add("TUI Research")
                if "webcontainer" in content_lower:
                    keywords.add("Development")
                if "research" in content_lower or "tutorial" in content_lower:
                    keywords.add("Research")
                if "documentation" in content_lower or "docs" in content_lower:
                    keywords.add("Documentation")
                if "chatgpt" in content_lower:
                    keywords.add("AI Assistance")

        return {
            "keywords": list(keywords),
            "domains": list(domains),
            "sites_count": len(domains),
        }

    def _analyze_git_activities(self, activities: List[Event]) -> dict:
        keywords = set()
        files = set()

        for activity in activities:
            if activity.type.value.upper() in ["GIT_COMMIT", "GIT_STASH"]:
                content = activity.summary or activity.raw_content or ""
                content_lower = content.lower()

                if "auth" in content_lower or "login" in content_lower:
                    keywords.add("Authentication")
                if "api" in content_lower:
                    keywords.add("API")
                if "database" in content_lower or "db" in content_lower:
                    keywords.add("Database")
                if "test" in content_lower:
                    keywords.add("Testing")
                if "fix" in content_lower or "bug" in content_lower:
                    keywords.add("Bug Fixes")
                if "feature" in content_lower:
                    keywords.add("Feature Development")

                if ".py" in content_lower:
                    files.add("Python")
                if ".js" in content_lower or ".ts" in content_lower:
                    files.add("JavaScript")
                if ".sql" in content_lower:
                    files.add("SQL")

        return {
            "keywords": list(keywords),
            "files": list(files),
            "files_count": len(files),
        }

    def _analyze_terminal_activities(self, activities: List[Event]) -> dict:
        keywords = set()
        commands = set()

        for activity in activities:
            if activity.type.value.upper() == "TERMINAL":
                content = activity.summary or activity.raw_content or ""
                content_lower = content.lower()

                if "git" in content_lower:
                    commands.add("Git")
                if "docker" in content_lower:
                    commands.add("Docker")
                if "npm" in content_lower or "yarn" in content_lower:
                    commands.add("Package Management")
                if "python" in content_lower or "pip" in content_lower:
                    commands.add("Python")
                if (
                    "database" in content_lower
                    or "psql" in content_lower
                    or "mysql" in content_lower
                ):
                    commands.add("Database")

                if "deploy" in content_lower:
                    keywords.add("Deployment")
                if "test" in content_lower:
                    keywords.add("Testing")
                if "build" in content_lower:
                    keywords.add("Build")
                if "install" in content_lower:
                    keywords.add("Installation")

        return {
            "keywords": list(keywords),
            "commands": list(commands),
            "command_types": ", ".join(list(commands)[:3]),
        }

    def _group_by_time_windows(
        self, activities: List[Event], window_minutes: int
    ) -> List[List[Event]]:
        if not activities:
            return []

        groups = []
        current_group = [activities[0]]

        for activity in activities[1:]:
            time_diff = (
                activity.timestamp - current_group[-1].timestamp
            ).total_seconds() / 60

            if time_diff <= window_minutes:
                current_group.append(activity)
            else:
                groups.append(current_group)
                current_group = [activity]

        groups.append(current_group)
        return groups

    def _cluster_by_semantic_similarity(
        self, activities: List[Event], threshold: float, min_activities: int
    ) -> List[Task]:
        tasks = []

        activities_with_embeddings = [a for a in activities if a.embedding is not None]
        activities_without_embeddings = [a for a in activities if a.embedding is None]

        print(f"  Activities with embeddings: {len(activities_with_embeddings)}")
        print(f"  Activities without embeddings: {len(activities_without_embeddings)}")

        if len(activities_with_embeddings) >= min_activities:
            semantic_tasks = self._perform_semantic_clustering(
                activities_with_embeddings, threshold, min_activities
            )
            tasks.extend(semantic_tasks)

        if activities_without_embeddings:
            fallback_tasks = self._group_activities_by_category(
                activities_without_embeddings, min_activities
            )
            tasks.extend(fallback_tasks)

        return tasks

    def _perform_semantic_clustering(
        self, activities: List[Event], threshold: float, min_activities: int
    ) -> List[Task]:
        embeddings = np.array([a.embedding for a in activities])

        similarity_matrix = cosine_similarity(embeddings)
        distance_matrix = 1 - similarity_matrix

        clusterer = DBSCAN(
            eps=1 - threshold, min_samples=min_activities, metric="precomputed"
        )
        cluster_labels = clusterer.fit_predict(distance_matrix)

        clusters = defaultdict(list)
        for idx, label in enumerate(cluster_labels):
            clusters[label].append(activities[idx])

        if -1 in clusters:
            noise_activities = clusters[-1]
            del clusters[-1]

            for noise_activity in noise_activities:
                if len(clusters) > 0:
                    best_cluster = self._assign_to_best_cluster(
                        noise_activity, clusters, threshold
                    )
                    if best_cluster is not None:
                        clusters[best_cluster].append(noise_activity)

        tasks = []
        for cluster_id, cluster_activities in clusters.items():
            if len(cluster_activities) >= min_activities:
                task = self._create_task_from_activities(cluster_activities)
                tasks.append(task)

        return tasks

    def _assign_to_best_cluster(
        self,
        activity: Event,
        clusters: Dict[int, List[Event]],
        similarity_threshold: float = 0.5,
    ) -> Optional[int]:
        if not activity.embedding:
            return None

        best_cluster = None
        best_similarity = 0.0

        for cluster_id, cluster_activities in clusters.items():
            cluster_embeddings = np.array(
                [a.embedding for a in cluster_activities if a.embedding]
            )
            if not len(cluster_embeddings):
                continue

            similarities = cosine_similarity([activity.embedding], cluster_embeddings)[
                0
            ]
            avg_similarity = float(np.mean(similarities))
            if avg_similarity > best_similarity:
                best_similarity = avg_similarity
                best_cluster = cluster_id

        return best_cluster if best_similarity > similarity_threshold else None

    def _group_activities_by_category(
        self, activities: List[Event], min_activities: int
    ) -> List[Task]:
        if not activities:
            return []

        category_groups = defaultdict(list)
        for activity in activities:
            category = activity.category if activity.category else "OTHER"
            category_groups[category].append(activity)

        tasks = []
        for category, group_activities in category_groups.items():
            if len(group_activities) >= min_activities:
                task = self._create_task_from_activities(group_activities)
                tasks.append(task)

        remaining_activities = []
        for category, group_activities in category_groups.items():
            if len(group_activities) < min_activities:
                remaining_activities.extend(group_activities)

        if len(remaining_activities) >= min_activities:
            mixed_task = self._create_task_from_activities(remaining_activities)
            tasks.append(mixed_task)

        return tasks

    def _create_task_from_activities(self, activities: List[Event]) -> Task:
        if not activities:
            raise ValueError("Cannot create task from empty activity list")

        activities = sorted(activities, key=lambda x: x.timestamp)

        start_time = activities[0].timestamp
        end_time = activities[-1].timestamp
        last_activity_time = end_time

        duration_minutes = int((end_time - start_time).total_seconds() / 60)

        projects = [a.project for a in activities if a.project]
        project = max(set(projects), key=projects.count) if projects else None

        categories = [a.category for a in activities if a.category]
        category = max(set(categories), key=categories.count) if categories else None

        activity_ids = [f"{a.type.value}_{a.timestamp.isoformat()}" for a in activities]

        embeddings = [a.embedding for a in activities if a.embedding]
        average_embedding = None
        if embeddings:
            average_embedding = np.mean(embeddings, axis=0).tolist()

        task = Task(
            title="",
            description="",
            status=TaskStatus.COMPLETED
            if end_time < datetime.now(end_time.tzinfo) - timedelta(hours=1)
            else TaskStatus.ACTIVE,
            category=category,
            project=project,
            start_time=start_time,
            end_time=end_time,
            last_activity_time=last_activity_time,
            duration_minutes=duration_minutes,
            activities=activity_ids,
            activity_count=len(activities),
            embedding=average_embedding,
            confidence_score=0.7,
        )

        return task

    def _enhance_task_with_ai(self, task: Task, all_activities: List[Event]):
        task_activities = []
        for activity in all_activities:
            activity_id = f"{activity.type.value}_{activity.timestamp.isoformat()}"
            if activity_id in task.activities:
                task_activities.append(activity)

        if not task_activities:
            return

        activity_summaries = []
        for activity in task_activities[:5]:
            summary = activity.summary or activity.raw_content[:100]
            activity_summaries.append(f"- {activity.type.value}: {summary}")

        user_prompt = render_user(
            "task_enhance",
            activities_text="\n".join(activity_summaries),
            project=task.project or "Unknown",
            category=task.category.value if task.category else "Unknown",
            duration_minutes=task.duration_minutes,
            activity_count=task.activity_count,
        )

        try:
            import concurrent.futures

            def generate_content_with_timeout():
                return self.client.chat.completions.create(
                    model=self.model_name,
                    messages=[
                        {"role": "system", "content": render_system()},
                        {"role": "user", "content": user_prompt},
                    ],
                )

            with concurrent.futures.ThreadPoolExecutor() as executor:
                future = executor.submit(generate_content_with_timeout)
                try:
                    response = future.result(timeout=10)
                    response_text = response.choices[0].message.content.strip()
                except concurrent.futures.TimeoutError:
                    print("AI enhancement timeout for task - using fallback values")
                    raise Exception("AI timeout")

            lines = response_text.split("\n")
            for line in lines:
                if line.startswith("TITLE:"):
                    task.title = line.replace("TITLE:", "").strip()
                elif line.startswith("DESCRIPTION:"):
                    task.description = line.replace("DESCRIPTION:", "").strip()
                elif line.startswith("PRIORITY:"):
                    priority_str = line.replace("PRIORITY:", "").strip().lower()
                    if priority_str in ["low", "medium", "high", "urgent"]:
                        task.priority = TaskPriority(priority_str)
                elif line.startswith("CONFIDENCE:"):
                    try:
                        confidence = float(line.replace("CONFIDENCE:", "").strip())
                        task.confidence_score = max(0.0, min(1.0, confidence))
                    except ValueError:
                        pass

            if not task.title:
                task.title = (
                    f"{task.category.value if task.category else 'Unknown'} Task"
                )
            if not task.description:
                task.description = f"Task involving {task.activity_count} activities"

        except Exception as e:
            print(f"Error enhancing task with AI: {e}")
            task.title = f"{task.category.value if task.category else 'Unknown'} Task"
            task.description = f"Task involving {task.activity_count} activities over {task.duration_minutes} minutes"
            task.confidence_score = 0.5

    def identify_task_boundaries(
        self, activities: List[Event], context_switch_threshold_minutes: int = 15
    ) -> List[Tuple[int, int]]:
        if len(activities) < 2:
            return [(0, len(activities) - 1)] if activities else []

        boundaries = []
        current_start = 0

        for i in range(1, len(activities)):
            prev_activity = activities[i - 1]
            curr_activity = activities[i]

            time_gap = (
                curr_activity.timestamp - prev_activity.timestamp
            ).total_seconds() / 60
            project_change = prev_activity.project != curr_activity.project
            category_change = prev_activity.category != curr_activity.category

            if (
                time_gap > context_switch_threshold_minutes
                or (project_change and time_gap > 5)
                or (category_change and time_gap > 10)
            ):
                boundaries.append((current_start, i - 1))
                current_start = i

        boundaries.append((current_start, len(activities) - 1))

        return boundaries

    def merge_similar_tasks(
        self, tasks: List[Task], similarity_threshold: float = 0.8
    ) -> List[Task]:
        if len(tasks) < 2:
            return tasks

        merged_tasks = []
        used_indices = set()

        for i, task1 in enumerate(tasks):
            if i in used_indices:
                continue

            similar_tasks = [task1]
            used_indices.add(i)

            for j, task2 in enumerate(tasks[i + 1 :], start=i + 1):
                if j in used_indices:
                    continue

                similarity = self._calculate_task_similarity(task1, task2)
                if similarity > similarity_threshold:
                    similar_tasks.append(task2)
                    used_indices.add(j)

            if len(similar_tasks) > 1:
                merged_task = self._merge_tasks(similar_tasks)
                merged_tasks.append(merged_task)
            else:
                merged_tasks.append(task1)

        return merged_tasks

    def _calculate_task_similarity(self, task1: Task, task2: Task) -> float:
        similarity_score = 0.0
        factors = 0

        if task1.embedding and task2.embedding:
            embedding_sim = cosine_similarity([task1.embedding], [task2.embedding])[0][
                0
            ]
            similarity_score += embedding_sim * 0.5
            factors += 0.5

        if task1.category == task2.category:
            similarity_score += 0.2
        factors += 0.2

        if task1.project == task2.project:
            similarity_score += 0.2
        factors += 0.2

        if task1.start_time and task2.start_time:
            time_diff_hours = (
                abs((task1.start_time - task2.start_time).total_seconds()) / 3600
            )
            time_similarity = max(0, 1 - time_diff_hours / 24)
            similarity_score += time_similarity * 0.1
        factors += 0.1

        return similarity_score / factors if factors > 0 else 0.0

    def _merge_tasks(self, tasks: List[Task]) -> Task:
        if len(tasks) == 1:
            return tasks[0]

        tasks = sorted(tasks, key=lambda x: x.start_time)

        all_activities = []
        for task in tasks:
            all_activities.extend(task.activities)

        merged_task = tasks[0]
        merged_task.activities = all_activities
        merged_task.activity_count = len(all_activities)
        merged_task.end_time = max(task.end_time or task.start_time for task in tasks)
        merged_task.last_activity_time = merged_task.end_time

        if merged_task.start_time and merged_task.end_time:
            duration = merged_task.end_time - merged_task.start_time
            merged_task.duration_minutes = int(duration.total_seconds() / 60)

        embeddings = [task.embedding for task in tasks if task.embedding]
        if embeddings:
            merged_task.embedding = np.mean(embeddings, axis=0).tolist()

        task_titles = [task.title for task in tasks if task.title]
        if len(task_titles) > 1:
            merged_task.title = f"Merged: {', '.join(task_titles[:2])}"
            if len(task_titles) > 2:
                merged_task.title += f" (and {len(task_titles) - 2} more)"

        merged_task.updated_at = datetime.now()

        return merged_task

    def _process_chunks_parallel(
        self, chunks: List[List[Event]], max_workers: int = 8
    ) -> List[Task]:
        if not chunks:
            return []

        completed_count = 0
        total_chunks = len(chunks)

        def process_single_chunk(chunk_data):
            chunk_index, chunk = chunk_data
            local_analyzer = LLMTaskAnalyzer(rag_system=self.rag_system)

            try:
                task = self._create_task_from_llm_analysis_safe(chunk, local_analyzer)
                return chunk_index, task
            except Exception as e:
                print(f"  Error processing chunk {chunk_index + 1}: {e}")
                return chunk_index, None

        tasks = []
        print(f"  Starting parallel processing with {max_workers} workers...")

        with ThreadPoolExecutor(max_workers=max_workers) as executor:
            future_to_chunk = {
                executor.submit(process_single_chunk, (i, chunk)): i
                for i, chunk in enumerate(chunks)
            }

            for future in as_completed(future_to_chunk):
                chunk_index, task = future.result()
                completed_count += 1

                if task:
                    tasks.append(task)
                    print(
                        f"  Completed chunk {chunk_index + 1}/{total_chunks} - {completed_count}/{total_chunks} done"
                    )
                else:
                    print(
                        f"  Failed chunk {chunk_index + 1}/{total_chunks} - {completed_count}/{total_chunks} done"
                    )

        print(
            f"  Parallel processing complete: {len(tasks)} tasks generated from {total_chunks} chunks"
        )
        return tasks

    def _create_task_from_llm_analysis_safe(
        self, chunk: List[Event], analyzer: LLMTaskAnalyzer
    ) -> Optional[Task]:
        if not chunk:
            return None

        try:
            analysis = analyzer.analyze_task_chunk(chunk)
            if not analysis:
                return None

            return self._build_task_from_analysis(chunk, analysis)

        except Exception as e:
            print(f"Error in LLM analysis: {e}")
            return self._create_basic_task_from_chunk(chunk)


class DailyCompactor:
    def __init__(self):
        self.vector_store = VectorStore()
        self.task_store = TaskStore()
        preprocessor = EventPreprocessor()
        self.task_identifier = TaskIdentifier(preprocessor, vector_store=None)
        self.activity_graph = (
            ActivityGraph() if _settings.get("enable_activity_graph") else None
        )

    def _reconstruct_events(self, raw_records: list) -> List[Event]:
        events = []
        for record in raw_records:
            vector = record.get("vector")
            if vector is None:
                continue
            payload = record["payload"]
            try:
                event = Event(
                    timestamp=datetime.fromisoformat(payload["timestamp"]),
                    type=EventType(payload["type"]),
                    source=payload.get("source", ""),
                    project=payload.get("project"),
                    raw_content=payload.get("raw_content", ""),
                    metadata=payload.get("metadata") or {},
                    summary=payload.get("summary"),
                    category=Category(payload["category"])
                    if payload.get("category")
                    else None,
                    embedding=vector,
                )
                events.append(event)
            except Exception as e:
                log.debug("skipping malformed record: %s", e)
        return events

    def _dedup_by_content(self, events: List[Event]) -> List[Event]:
        seen: dict = {}
        kept: List[Event] = []

        for event in events:
            key = None

            if event.type == EventType.TERMINAL:
                try:
                    cmd = json.loads(event.raw_content).get(
                        "command", event.raw_content
                    )
                except Exception:
                    cmd = event.raw_content
                key = ("terminal", cmd)

            elif event.type == EventType.BROWSER:
                try:
                    url = json.loads(event.raw_content).get("url", event.raw_content)
                except Exception:
                    url = event.raw_content
                key = ("browser", url)

            elif event.type in (EventType.WINDOW, EventType.SCREEN):
                app = (event.metadata or {}).get("app_name", "")
                title = (event.metadata or {}).get("window_title", "")
                key = (event.type.value, app, title)

            if key is None:
                kept.append(event)
                continue

            if key not in seen:
                seen[key] = len(kept)
                kept.append(event)
            else:
                kept[seen[key]] = event

        return kept

    def _dedup_by_vector(self, events: List[Event]) -> List[Event]:
        threshold = config.working_memory_dedup_threshold
        kept: List[Event] = []
        kept_vecs: List[np.ndarray] = []

        for event in events:
            if event.embedding is None:
                kept.append(event)
                continue

            emb = np.array(event.embedding, dtype=np.float32)
            norm = np.linalg.norm(emb)
            if norm == 0:
                kept.append(event)
                continue
            emb_n = emb / norm

            if kept_vecs:
                matrix = np.stack(kept_vecs)
                sims = matrix @ emb_n
                if float(sims.max()) >= threshold:
                    continue

            kept.append(event)
            kept_vecs.append(emb_n)

        return kept

    def _dedup_events(self, events: List[Event]) -> List[Event]:
        before = len(events)
        events = sorted(events, key=lambda e: e.timestamp)
        events = self._dedup_by_content(events)
        after_content = len(events)
        log.info("content dedup: %d -> %d events", before, after_content)

        if len(events) > 50:
            events = self._dedup_by_vector(events)
            log.info("vector dedup: %d -> %d events", after_content, len(events))

        return events

    def compact_day(
        self, target_date: date, dry_run: bool = False, force: bool = False
    ) -> dict:
        today = datetime.now().date()
        if target_date >= today:
            return {"skipped": True, "reason": "cannot_compact_today_or_future"}

        state = _load_state()
        date_str = target_date.isoformat()

        if date_str in state["compacted_dates"] and not force:
            log.info("daily compaction: %s already compacted, skipping", date_str)
            return {"skipped": True, "reason": "already_compacted", "date": date_str}

        raw_records = self.vector_store.get_events_by_date(
            target_date, exclude_compacted=True
        )
        log.info(
            "daily compaction: fetched %d raw events for %s", len(raw_records), date_str
        )

        if not raw_records:
            if not dry_run:
                if date_str not in state["compacted_dates"]:
                    state["compacted_dates"].append(date_str)
                _save_state(state)
            return {
                "date": date_str,
                "event_count": 0,
                "deduped_count": 0,
                "task_count": 0,
                "dry_run": dry_run,
            }

        events = self._reconstruct_events(raw_records)
        events = self._dedup_events(events)
        log.info(
            "daily compaction: %d events after dedup for %s", len(events), date_str
        )

        tasks = self.task_identifier.identify_tasks_from_activities(
            events,
            min_activities_per_task=config.daily_compaction_min_activities,
        )
        log.info("daily compaction: identified %d tasks for %s", len(tasks), date_str)

        if not dry_run:
            failed = [task for task in tasks if not self.task_store.store_task(task)]
            if failed:
                log.error(
                    "daily compaction: %d/%d tasks failed to store for %s — skipping deletion",
                    len(failed),
                    len(tasks),
                    date_str,
                )
                return {
                    "date": date_str,
                    "event_count": len(raw_records),
                    "deduped_count": len(events),
                    "task_count": len(tasks),
                    "tasks_stored": len(tasks) - len(failed),
                    "dry_run": dry_run,
                    "error": "partial_task_store_failure",
                }
            for task in tasks:
                self.vector_store.store_task(task)
            if self.activity_graph:
                # Activity ids use the same {type}_{ts_iso} scheme TaskIdentifier
                # assigns (see _create_task_from_*). Resolve each back to its event so
                # the graph can link Task -> ScreenEvent/etc via its (label, ts_ms) index.
                event_lookup = {
                    f"{e.type.value}_{e.timestamp.isoformat()}": e for e in events
                }
                for task in tasks:
                    self.activity_graph.upsert_task(task, event_lookup)
            self.vector_store.delete_events_by_date(target_date)
            if date_str not in state["compacted_dates"]:
                state["compacted_dates"].append(date_str)
            _save_state(state)

        return {
            "date": date_str,
            "event_count": len(raw_records),
            "deduped_count": len(events),
            "task_count": len(tasks),
            "dry_run": dry_run,
        }

    def maybe_compact_previous_days(self):
        state = _load_state()
        today = datetime.now().date()
        today_str = today.isoformat()

        if state.get("last_trigger_date") == today_str:
            log.debug("daily compaction already ran today, skipping")
            return

        for days_ago in range(1, 8):
            target = today - timedelta(days=days_ago)
            target_str = target.isoformat()
            if target_str in state["compacted_dates"]:
                continue
            try:
                result = self.compact_day(target)
                log.info("daily compaction result for %s: %s", target_str, result)
            except Exception as e:
                log.error("daily compaction failed for %s: %s", target_str, e)

        state = _load_state()
        state["last_trigger_date"] = today_str
        _save_state(state)
