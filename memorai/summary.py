"""Daily activity summarization service."""

import json
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Dict, List, Optional, Any
from collections import defaultdict, Counter
from openai import OpenAI

from memorai.db.models import Category, DailySummary, Task, DailyTaskSummary, TaskStatus, TaskPriority
from memorai.db.store import VectorStore
from memorai.db.tasks import TaskStore
from memorai.config import config
from memorai.prompts import render_system, render_user


class DailySummarizer:
    def __init__(self, vector_store: VectorStore):
        self.vector_store = vector_store
        self.client = OpenAI(api_key=config.openai_api_key)
        self.model_name = "gpt-4o-mini"

    def generate_daily_summary(self, target_date: date) -> Optional[DailySummary]:
        events = self.vector_store.get_events_by_date(target_date)

        if not events:
            print(f"No events found for {target_date}")
            return None

        # Group events by category
        events_by_category = defaultdict(list)
        project_counter = Counter()

        for event_data in events:
            payload = event_data["payload"]
            category_str = payload.get("category")

            if category_str:
                try:
                    category = Category(category_str)
                    events_by_category[category].append(payload)
                except ValueError:
                    events_by_category[Category.OTHER].append(payload)
            else:
                events_by_category[Category.OTHER].append(payload)

            project = payload.get("project")
            if project:
                project_counter[project] += 1

        # Generate category summaries
        category_summaries = {}
        for category, category_events in events_by_category.items():
            summary = self._generate_category_summary(category, category_events)
            category_summaries[category] = summary

        # Generate overall summary
        overall_summary = self._generate_overall_summary(
            target_date,
            events_by_category,
            len(events),
            list(project_counter.keys())
        )

        # Create summary object
        daily_summary = DailySummary(
            date=target_date.isoformat(),
            category_summaries=category_summaries,
            overall_summary=overall_summary,
            total_events=len(events),
            events_by_category={cat: len(events) for cat, events in events_by_category.items()},
            projects_worked_on=list(project_counter.keys()),
            created_at=datetime.now()
        )

        return daily_summary

    def _generate_category_summary(self, category: Category, events: List[Dict]) -> str:
        if not events:
            return f"No {category.value.lower()} activities recorded."

        # Limit events to avoid token limits
        sample_events = events[:20]
        summaries = [event.get("summary", event.get("raw_content", "")[:100]) for event in sample_events]

        user_prompt = render_user(
            "category_summary",
            category_name=category.value.lower(),
            event_count=len(events),
            summaries=summaries[:15],
            extra_count=max(0, len(events) - 15),
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error generating {category.value} summary: {e}")
            return self._generate_fallback_category_summary(category, events)

    def _generate_fallback_category_summary(self, category: Category, events: List[Dict]) -> str:
        count = len(events)
        projects = set(event.get("project") for event in events if event.get("project"))

        fallback_templates = {
            Category.CODING: f"Completed {count} coding activities across {len(projects)} projects.",
            Category.DEBUGGING: f"Worked on {count} debugging tasks.",
            Category.RESEARCH: f"Conducted {count} research activities.",
            Category.LEARNING: f"Engaged in {count} learning activities.",
            Category.WRITING: f"Completed {count} writing tasks.",
            Category.COMMUNICATION: f"Participated in {count} communication activities.",
            Category.OTHER: f"Completed {count} miscellaneous activities."
        }

        summary = fallback_templates.get(category, f"Completed {count} {category.value.lower()} activities.")

        if projects:
            project_list = ", ".join(list(projects)[:3])
            if len(projects) > 3:
                project_list += f" and {len(projects) - 3} others"
            summary += f" Projects involved: {project_list}."

        return summary

    def _generate_overall_summary(
        self,
        target_date: date,
        events_by_category: Dict[Category, List],
        total_events: int,
        projects: List[str]
    ) -> str:

        category_counts = {cat.value: len(events) for cat, events in events_by_category.items()}
        top_categories = sorted(category_counts.items(), key=lambda x: x[1], reverse=True)[:3]

        projects_str = ', '.join(projects[:5]) + ("..." if len(projects) > 5 else "")
        user_prompt = render_user(
            "overall_summary",
            date_str=target_date.strftime('%B %d, %Y'),
            total_events=total_events,
            top_categories_str=', '.join([f"{cat} ({count})" for cat, count in top_categories]),
            projects_str=projects_str,
        )

        try:
            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()
        except Exception as e:
            print(f"Error generating overall summary: {e}")
            return self._generate_fallback_overall_summary(total_events, top_categories, projects)

    def _generate_fallback_overall_summary(
        self,
        total_events: int,
        top_categories: List[tuple],
        projects: List[str]
    ) -> str:

        if not top_categories:
            return f"Tracked {total_events} activities throughout the day."

        top_cat, top_count = top_categories[0]
        summary = f"Recorded {total_events} activities today, with {top_cat.lower()} being the primary focus ({top_count} activities)."

        if len(top_categories) > 1:
            second_cat, second_count = top_categories[1]
            summary += f" Also spent significant time on {second_cat.lower()} ({second_count} activities)."

        if projects:
            project_list = ", ".join(projects[:3])
            if len(projects) > 3:
                project_list += f" and {len(projects) - 3} others"
            summary += f" Projects involved: {project_list}."

        return summary

    def save_summary(self, summary: DailySummary, output_dir: Optional[Path] = None) -> bool:
        if output_dir is None:
            output_dir = config.data_dir / "summaries"

        output_dir.mkdir(parents=True, exist_ok=True)

        # Save as JSON
        json_file = output_dir / f"{summary.date}_summary.json"
        try:
            with open(json_file, "w", encoding="utf-8") as f:
                json.dump(summary.model_dump(), f, indent=2, default=str)
        except Exception as e:
            print(f"Error saving JSON summary: {e}")
            return False

        # Save as Markdown
        md_file = output_dir / f"{summary.date}_summary.md"
        try:
            md_content = self._format_summary_as_markdown(summary)
            with open(md_file, "w", encoding="utf-8") as f:
                f.write(md_content)
        except Exception as e:
            print(f"Error saving Markdown summary: {e}")
            return False

        return True

    def _format_summary_as_markdown(self, summary: DailySummary) -> str:
        date_str = datetime.fromisoformat(summary.date).strftime("%B %d, %Y")

        md_content = f"""# Daily Activity Summary - {date_str}

        ## Overview
        {summary.overall_summary}

        **Total Activities:** {summary.total_events}
        **Projects Worked On:** {len(summary.projects_worked_on)}

        ## Activities by Category

        """

        for category, count in summary.events_by_category.items():
            if count > 0:
                md_content += f"### {category.value} ({count} activities)\n\n"
                md_content += f"{summary.category_summaries.get(category, 'No summary available.')}\n\n"

        if summary.projects_worked_on:
            md_content += "## Projects\n\n"
            for project in summary.projects_worked_on:
                project_count = sum(
                    1 for cat, events in summary.events_by_category.items()
                    if events > 0  # This is a simplification - actual project counting would need event details
                )
                md_content += f"- **{project}**\n"
            md_content += "\n"

        md_content += f"---\n*Generated on {summary.created_at.strftime('%Y-%m-%d %H:%M:%S')}*\n"

        return md_content

    def load_summary(self, target_date: date, summaries_dir: Optional[Path] = None) -> Optional[DailySummary]:
        if summaries_dir is None:
            summaries_dir = config.data_dir / "summaries"

        json_file = summaries_dir / f"{target_date.isoformat()}_summary.json"

        if not json_file.exists():
            return None

        try:
            with open(json_file, "r", encoding="utf-8") as f:
                data = json.load(f)

            # Convert string dates back to proper types
            data["created_at"] = datetime.fromisoformat(data["created_at"])

            # Convert category strings back to Category enums
            if "category_summaries" in data:
                category_summaries = {}
                for cat_str, summary in data["category_summaries"].items():
                    try:
                        category = Category(cat_str)
                        category_summaries[category] = summary
                    except ValueError:
                        pass
                data["category_summaries"] = category_summaries

            if "events_by_category" in data:
                events_by_category = {}
                for cat_str, count in data["events_by_category"].items():
                    try:
                        category = Category(cat_str)
                        events_by_category[category] = count
                    except ValueError:
                        pass
                data["events_by_category"] = events_by_category

            return DailySummary(**data)

        except Exception as e:
            print(f"Error loading summary: {e}")
            return None


class TaskSummarizer:
    def __init__(self, task_store: TaskStore):
        self.task_store = task_store
        self.client = OpenAI(api_key=config.openai_api_key)
        self.model_name = "gpt-4o-mini"
        self.summaries_dir = config.data_dir / "task_summaries"
        self.summaries_dir.mkdir(exist_ok=True)

    def generate_daily_task_summary(
        self,
        target_date: date,
        save_to_file: bool = False
    ) -> DailyTaskSummary:
        """Generate a comprehensive daily task summary."""
        # Get tasks for the date
        tasks = self.task_store.get_tasks_by_date(target_date)

        if not tasks:
            return DailyTaskSummary(
                date=target_date.isoformat(),
                total_tasks=0,
                completed_tasks=0,
                active_tasks=0,
                tasks_by_category={},
                tasks_by_project={},
                task_summaries={},
                overall_summary="No tasks found for this date.",
                total_time_minutes=0,
                productivity_score=0.0
            )

        # Calculate basic statistics
        total_tasks = len(tasks)
        completed_tasks = len([t for t in tasks if t.status == TaskStatus.COMPLETED])
        active_tasks = len([t for t in tasks if t.status == TaskStatus.ACTIVE])
        total_time_minutes = sum(t.duration_minutes or 0 for t in tasks)

        # Group tasks by category
        tasks_by_category = {}
        for task in tasks:
            if task.category:
                cat = task.category
                if cat not in tasks_by_category:
                    tasks_by_category[cat] = 0
                tasks_by_category[cat] += 1

        # Group tasks by project
        tasks_by_project = {}
        for task in tasks:
            if task.project:
                proj = task.project
                if proj not in tasks_by_project:
                    tasks_by_project[proj] = 0
                tasks_by_project[proj] += 1

        # Generate AI summaries for individual tasks
        task_summaries = {}
        for task in tasks[:10]:  # Limit to top 10 tasks
            summary = self._generate_task_summary(task)
            task_summaries[task.id] = summary

        # Generate overall summary using AI
        overall_summary = self._generate_overall_summary(tasks, target_date)

        # Calculate productivity score
        completion_rate = completed_tasks / total_tasks if total_tasks > 0 else 0
        avg_confidence = sum(t.confidence_score for t in tasks) / total_tasks if total_tasks > 0 else 0
        task_diversity = len(set(t.category for t in tasks if t.category)) / 7  # Normalize by total categories
        productivity_score = (completion_rate * 0.5) + (avg_confidence * 0.3) + (task_diversity * 0.2)

        # Create summary object
        summary = DailyTaskSummary(
            date=target_date.isoformat(),
            total_tasks=total_tasks,
            completed_tasks=completed_tasks,
            active_tasks=active_tasks,
            tasks_by_category=tasks_by_category,
            tasks_by_project=tasks_by_project,
            task_summaries=task_summaries,
            overall_summary=overall_summary,
            total_time_minutes=total_time_minutes,
            productivity_score=round(productivity_score, 2)
        )

        # Save to file if requested
        if save_to_file:
            self._save_task_summary(summary)

        return summary

    def generate_rich_daily_summary(
        self,
        target_date: date,
        save_to_file: bool = False,
        use_ai: bool = True
    ) -> Dict[str, Any]:
        """Generate a rich narrative daily summary showing individual tasks."""
        # Get tasks for the date
        tasks = self.task_store.get_tasks_by_date(target_date)

        if not tasks:
            return {
                "date": target_date.isoformat(),
                "overview": "No tasks found for this date.",
                "total_tasks": 0,
                "tasks": [],
                "projects": []
            }

        # Generate individual task summaries (limit AI calls to avoid timeout)
        task_summaries = []
        for i, task in enumerate(tasks):
            if use_ai and i < 10:  # Only use AI for top 10 tasks to avoid timeout
                task_summary = self._generate_task_summary(task)
            else:
                # Generate simple summary without AI for remaining tasks
                task_summary = f"{task.title} - {task.activity_count} activities over {task.duration_minutes or 0} minutes"

            task_summaries.append({
                "id": task.id,
                "title": task.title,
                "summary": task_summary,
                "category": task.category.value if task.category else "Other",
                "project": task.project,
                "status": task.status.value,
                "duration_minutes": task.duration_minutes or 0,
                "activity_count": task.activity_count,
                "confidence_score": task.confidence_score
            })

        # Sort tasks by duration (most time spent first)
        task_summaries.sort(key=lambda x: x["duration_minutes"], reverse=True)

        # Generate overall day summary
        total_tasks = len(tasks)
        if use_ai:
            overview = self._generate_task_based_overview(target_date, tasks, task_summaries)
        else:
            # Generate simple overview without AI
            completed_tasks = len([t for t in tasks if t.status.value == 'completed'])
            completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
            top_tasks = [t["title"] for t in task_summaries[:3]]
            overview = f"{target_date.strftime('%B %d, %Y')} was a productive day with {total_tasks} tasks completed ({completion_rate:.1f}% completion rate). Key tasks included: {', '.join(top_tasks)}."

        # Extract projects
        projects = list(set([task.project for task in tasks if task.project]))

        result = {
            "date": target_date.isoformat(),
            "overview": overview,
            "total_tasks": total_tasks,
            "tasks": task_summaries,
            "projects": projects,
            "total_time_minutes": sum(t.duration_minutes or 0 for t in tasks)
        }

        if save_to_file:
            self._save_rich_summary(result)

        return result

    def _extract_themes_from_tasks(self, tasks: List[Task]) -> List[str]:
        """Extract key themes from task titles and descriptions."""
        themes = set()

        for task in tasks:
            if task.title:
                # Extract meaningful words from titles
                title_words = task.title.lower().split()
                for word in title_words:
                    if len(word) > 3 and word not in ['task', 'work', 'other', 'general']:
                        themes.add(word.title())

        return sorted(list(themes))[:5]  # Return top 5 themes

    def _generate_category_summary(self, category: str, tasks: List[Task], themes: List[str]) -> str:
        """Generate an AI summary for a category of tasks."""
        try:
            task_details = []
            for task in tasks[:5]:  # Limit to top 5 tasks
                detail = f"- {task.title} ({task.duration_minutes or 0}m, {task.activity_count} activities)"
                task_details.append(detail)

            task_list = "\n".join(task_details)
            themes_str = ", ".join(themes) if themes else "general activities"

            user_prompt = render_user(
                "task_category_summary",
                category=category.lower(),
                task_details=task_details,
                themes_str=themes_str,
                task_count=len(tasks),
            )

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating category summary for {category}: {e}")
            themes_str = f" focused on {', '.join(themes)}" if themes else ""
            return f"Completed {len(tasks)} {category.lower()} tasks{themes_str} totaling {sum(t.duration_minutes or 0 for t in tasks)} minutes."

    def _generate_rich_overview(
        self,
        target_date: date,
        tasks: List[Task],
        category_summaries: Dict[str, Any]
    ) -> str:
        """Generate a rich narrative overview of the day."""
        try:
            # Prepare overview data
            total_tasks = len(tasks)
            completed_tasks = len([t for t in tasks if t.status.value == 'completed'])
            completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
            total_time_hours = sum(t.duration_minutes or 0 for t in tasks) / 60

            # Top categories by task count
            top_categories = sorted(
                category_summaries.items(),
                key=lambda x: x[1]['count'],
                reverse=True
            )[:3]

            # Extract projects
            projects = list(set([t.project for t in tasks if t.project]))

            user_prompt = render_user(
                "task_rich_overview",
                date_str=target_date.strftime('%B %d, %Y'),
                total_tasks=total_tasks,
                completed_tasks=completed_tasks,
                completion_rate=f"{completion_rate:.1f}",
                total_time_hours=f"{total_time_hours:.1f}",
                top_categories=top_categories,
                projects_str=', '.join(projects) if projects else 'Various personal tasks',
            )

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating rich overview: {e}")
            top_cat = max(category_summaries.items(), key=lambda x: x[1]['count'])[0] if category_summaries else "general"
            return f"{target_date.strftime('%B %d, %Y')} was a productive day with {total_tasks} tasks completed, with primary focus on {top_cat.lower()} work."

    def _generate_task_based_overview(
        self,
        target_date: date,
        tasks: List[Task],
        task_summaries: List[Dict[str, Any]]
    ) -> str:
        """Generate an overview focused on individual tasks rather than categories."""
        try:
            # Prepare overview data
            total_tasks = len(tasks)
            completed_tasks = len([t for t in tasks if t.status.value == 'completed'])
            completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
            total_time_hours = sum(t.duration_minutes or 0 for t in tasks) / 60

            # Top tasks by time spent
            top_tasks = task_summaries[:5]  # Already sorted by duration
            top_task_titles = [task["title"] for task in top_tasks]

            # Extract projects
            projects = list(set([task.project for task in tasks if task.project]))

            user_prompt = render_user(
                "task_based_overview",
                date_str=target_date.strftime('%B %d, %Y'),
                total_tasks=total_tasks,
                completed_tasks=completed_tasks,
                completion_rate=f"{completion_rate:.1f}",
                total_time_hours=f"{total_time_hours:.1f}",
                top_task_titles=top_task_titles,
                projects_str=', '.join(projects) if projects else 'Various personal tasks',
            )

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating task-based overview: {e}")
            top_tasks = task_summaries[:3]
            task_names = [task["title"] for task in top_tasks]
            return f"{target_date.strftime('%B %d, %Y')} was a productive day with {total_tasks} tasks completed ({completion_rate:.1f}% completion rate). Major focus areas included: {', '.join(task_names)}."

    def _save_rich_summary(self, summary: Dict[str, Any]):
        """Save rich summary to file."""
        try:
            # Save JSON version
            date_str = summary["date"]
            json_file = self.summaries_dir / f"{date_str}_rich_task_summary.json"
            with open(json_file, 'w') as f:
                json.dump(summary, f, indent=2, default=str)

        except Exception as e:
            print(f"Error saving rich task summary: {e}")

    def _generate_task_summary(self, task: Task) -> str:
        """Generate an AI summary for a single task."""
        try:
            user_prompt = render_user(
                "task_summary",
                title=task.title,
                description=task.description or "No description",
                status=task.status.value,
                category=task.category.value if task.category else "None",
                duration_minutes=task.duration_minutes or 0,
                activity_count=task.activity_count,
                confidence_score=f"{task.confidence_score:.2f}",
            )

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating task summary: {e}")
            return f"Task: {task.title} ({task.status.value}, {task.duration_minutes or 0}m)"

    def _generate_overall_summary(self, tasks: List[Task], target_date: date) -> str:
        """Generate an overall AI summary of the day's tasks."""
        if not tasks:
            return "No tasks were recorded for this day."

        try:
            # Prepare task information for AI
            task_info = []
            for task in tasks[:15]:  # Limit to top 15 tasks
                info = f"- {task.title} ({task.status.value}, {task.category.value if task.category else 'N/A'}, {task.duration_minutes or 0}m)"
                task_info.append(info)

            task_list = "\n".join(task_info)

            # Calculate key metrics
            completion_rate = len([t for t in tasks if t.status == TaskStatus.COMPLETED]) / len(tasks) * 100
            total_time_hours = sum(t.duration_minutes or 0 for t in tasks) / 60
            main_categories = Counter(t.category.value for t in tasks if t.category).most_common(3)

            user_prompt = render_user(
                "task_daily_overall",
                date_str=str(target_date),
                task_info=task_info,
                total_tasks=len(tasks),
                completion_rate=f"{completion_rate:.1f}",
                total_time_hours=f"{total_time_hours:.1f}",
                main_categories_str=', '.join([f'{cat} ({count})' for cat, count in main_categories]),
            )

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating overall summary: {e}")
            return f"Completed {len(tasks)} tasks with a focus on {Counter(t.category.value for t in tasks if t.category).most_common(1)[0][0] if tasks else 'various activities'}."

    def _save_task_summary(self, summary: DailyTaskSummary):
        """Save task summary to local storage."""
        try:
            # Save JSON version
            json_file = self.summaries_dir / f"{summary.date}_task_summary.json"
            with open(json_file, 'w') as f:
                json.dump(summary.model_dump(mode='json'), f, indent=2, default=str)

            # Save Markdown version
            md_file = self.summaries_dir / f"{summary.date}_task_summary.md"
            with open(md_file, 'w') as f:
                f.write(self._format_summary_as_markdown(summary))

        except Exception as e:
            print(f"Error saving task summary: {e}")

    def _format_summary_as_markdown(self, summary: DailyTaskSummary) -> str:
        """Format task summary as Markdown."""
        md_content = f"""# Task Summary - {summary.date}

## Overview
{summary.overall_summary}

## Key Metrics
- **Total Tasks**: {summary.total_tasks}
- **Completed**: {summary.completed_tasks} ({summary.completed_tasks/summary.total_tasks*100:.1f}% completion rate)
- **Active**: {summary.active_tasks}
- **Total Time**: {summary.total_time_minutes} minutes ({summary.total_time_minutes//60}h {summary.total_time_minutes%60}m)
- **Productivity Score**: {summary.productivity_score:.2f}/1.0

"""

        # Tasks by Category
        if summary.tasks_by_category:
            md_content += "## Tasks by Category\n"
            for category, count in sorted(summary.tasks_by_category.items(), key=lambda x: x[1], reverse=True):
                percentage = count / summary.total_tasks * 100
                md_content += f"- **{category.value}**: {count} tasks ({percentage:.1f}%)\n"
            md_content += "\n"

        # Tasks by Project
        if summary.tasks_by_project:
            md_content += "## Tasks by Project\n"
            for project, count in sorted(summary.tasks_by_project.items(), key=lambda x: x[1], reverse=True)[:5]:
                percentage = count / summary.total_tasks * 100
                md_content += f"- **{project}**: {count} tasks ({percentage:.1f}%)\n"
            md_content += "\n"

        # Individual Task Summaries
        if summary.task_summaries:
            md_content += "## Key Tasks\n"
            for task_id, task_summary in list(summary.task_summaries.items())[:5]:
                md_content += f"- {task_summary}\n"
            md_content += "\n"

        md_content += f"*Generated on {summary.created_at.strftime('%Y-%m-%d %H:%M:%S')}*\n"

        return md_content

    def generate_weekly_task_summary(
        self,
        start_date: date,
        save_to_file: bool = False
    ) -> Dict[str, Any]:
        """Generate a weekly task summary."""
        end_date = start_date + timedelta(days=6)
        weekly_tasks = self.task_store.get_tasks_by_date_range(start_date, end_date)

        if not weekly_tasks:
            return {
                "week_start": start_date.isoformat(),
                "week_end": end_date.isoformat(),
                "total_tasks": 0,
                "summary": "No tasks found for this week."
            }

        # Generate daily summaries for the week
        daily_summaries = {}
        current_date = start_date
        while current_date <= end_date:
            daily_tasks = [t for t in weekly_tasks if t.start_time.date() == current_date]
            if daily_tasks:
                daily_summary = self.generate_daily_task_summary(current_date, save_to_file=False)
                daily_summaries[current_date.isoformat()] = daily_summary
            current_date += timedelta(days=1)

        # Calculate weekly statistics
        total_tasks = len(weekly_tasks)
        completed_tasks = len([t for t in weekly_tasks if t.status == TaskStatus.COMPLETED])
        total_time_minutes = sum(t.duration_minutes or 0 for t in weekly_tasks)

        # Category trends
        category_counts = Counter(t.category.value for t in weekly_tasks if t.category)

        # Generate weekly insights
        weekly_insights = self._generate_weekly_insights(weekly_tasks, start_date, end_date)

        weekly_summary = {
            "week_start": start_date.isoformat(),
            "week_end": end_date.isoformat(),
            "total_tasks": total_tasks,
            "completed_tasks": completed_tasks,
            "completion_rate": completed_tasks / total_tasks if total_tasks > 0 else 0,
            "total_time_minutes": total_time_minutes,
            "daily_summaries": daily_summaries,
            "category_distribution": dict(category_counts),
            "weekly_insights": weekly_insights,
            "generated_at": datetime.now().isoformat()
        }

        if save_to_file:
            self._save_weekly_summary(weekly_summary)

        return weekly_summary

    def _generate_weekly_insights(
        self,
        weekly_tasks: List[Task],
        start_date: date,
        end_date: date
    ) -> str:
        """Generate AI insights for the week."""
        if not weekly_tasks:
            return "No tasks to analyze for this week."

        try:
            # Analyze patterns
            daily_task_counts = defaultdict(int)
            daily_completion_rates = defaultdict(list)

            for task in weekly_tasks:
                task_date = task.start_time.date()
                daily_task_counts[task_date] += 1
                if task.status == TaskStatus.COMPLETED:
                    daily_completion_rates[task_date].append(1)
                else:
                    daily_completion_rates[task_date].append(0)

            # Find patterns
            busiest_day = max(daily_task_counts.items(), key=lambda x: x[1])
            avg_daily_tasks = sum(daily_task_counts.values()) / len(daily_task_counts)

            completion_rates = {
                date: sum(rates) / len(rates) if rates else 0
                for date, rates in daily_completion_rates.items()
            }
            best_day = max(completion_rates.items(), key=lambda x: x[1]) if completion_rates else None

            main_categories = Counter(t.category.value for t in weekly_tasks if t.category).most_common(3)

            user_prompt = render_user(
                "task_weekly_insights",
                start_date=str(start_date),
                end_date=str(end_date),
                total_tasks=len(weekly_tasks),
                completed_tasks=len([t for t in weekly_tasks if t.status == TaskStatus.COMPLETED]),
                busiest_day=str(busiest_day[0]),
                busiest_day_count=busiest_day[1],
                avg_daily_tasks=f"{avg_daily_tasks:.1f}",
                best_day=str(best_day[0]) if best_day else 'N/A',
                best_day_rate=f"{best_day[1]*100:.1f}" if best_day else '0.0',
                main_categories_str=', '.join([f'{cat} ({count})' for cat, count in main_categories]),
            )

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[
                    {"role": "system", "content": render_system()},
                    {"role": "user", "content": user_prompt},
                ],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating weekly insights: {e}")
            return f"Week of {start_date}: Completed {len([t for t in weekly_tasks if t.status == TaskStatus.COMPLETED])} out of {len(weekly_tasks)} tasks."

    def _save_weekly_summary(self, summary: Dict[str, Any]):
        """Save weekly summary to file."""
        try:
            week_start = summary["week_start"]
            json_file = self.summaries_dir / f"week_{week_start}_summary.json"

            with open(json_file, 'w') as f:
                json.dump(summary, f, indent=2, default=str)

        except Exception as e:
            print(f"Error saving weekly summary: {e}")

    def get_productivity_trends(
        self,
        days: int = 30
    ) -> Dict[str, Any]:
        """Analyze productivity trends over the specified number of days."""
        end_date = date.today()
        start_date = end_date - timedelta(days=days-1)

        all_tasks = self.task_store.get_tasks_by_date_range(start_date, end_date)

        if not all_tasks:
            return {"error": "No tasks found in the specified period"}

        # Daily metrics
        daily_metrics = defaultdict(lambda: {
            "tasks": 0,
            "completed": 0,
            "time_minutes": 0,
            "categories": set()
        })

        for task in all_tasks:
            task_date = task.start_time.date()
            daily_metrics[task_date]["tasks"] += 1
            if task.status == TaskStatus.COMPLETED:
                daily_metrics[task_date]["completed"] += 1
            daily_metrics[task_date]["time_minutes"] += task.duration_minutes or 0
            if task.category:
                daily_metrics[task_date]["categories"].add(task.category.value)

        # Calculate trends
        completion_rates = []
        daily_task_counts = []
        daily_time_spent = []

        for i in range(days):
            current_date = start_date + timedelta(days=i)
            metrics = daily_metrics[current_date]

            completion_rate = metrics["completed"] / metrics["tasks"] if metrics["tasks"] > 0 else 0
            completion_rates.append(completion_rate)
            daily_task_counts.append(metrics["tasks"])
            daily_time_spent.append(metrics["time_minutes"])

        # Calculate averages and trends
        avg_completion_rate = sum(completion_rates) / len(completion_rates)
        avg_daily_tasks = sum(daily_task_counts) / len(daily_task_counts)
        avg_daily_time = sum(daily_time_spent) / len(daily_time_spent)

        return {
            "period": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "days_analyzed": days
            },
            "averages": {
                "completion_rate": round(avg_completion_rate, 3),
                "daily_tasks": round(avg_daily_tasks, 1),
                "daily_time_minutes": round(avg_daily_time, 1)
            },
            "trends": {
                "completion_rates": completion_rates,
                "daily_task_counts": daily_task_counts,
                "daily_time_spent": daily_time_spent
            },
            "total_tasks": len(all_tasks),
            "total_completed": len([t for t in all_tasks if t.status == TaskStatus.COMPLETED]),
            "most_productive_day": max(daily_metrics.items(), key=lambda x: x[1]["completed"])[0].isoformat() if daily_metrics else None
        }


class RAGTaskSummarizer(TaskSummarizer):
    """Enhanced task summarizer with RAG capabilities for contextual insights."""

    def __init__(self, task_store: TaskStore, vector_store: VectorStore):
        super().__init__(task_store)
        self.vector_store = vector_store
        from memorai.process import EventPreprocessor
        self.preprocessor = EventPreprocessor()
        from memorai.db.retrieval.rag import RAGSystem
        self.rag_system = RAGSystem(vector_store, self.preprocessor)

    def generate_rich_daily_summary_with_rag(
        self,
        target_date: date,
        save_to_file: bool = False,
        use_ai: bool = True
    ) -> Dict[str, Any]:
        """
        Generate a rich daily summary enhanced with RAG contextual insights.

        Args:
            target_date: Date to generate summary for
            save_to_file: Whether to save the summary to a file
            use_ai: Whether to use AI enhancement

        Returns:
            Enhanced daily summary with contextual insights
        """
        # Get base summary
        base_summary = self.generate_rich_daily_summary(
            target_date, save_to_file=False, use_ai=use_ai
        )

        if not use_ai or not base_summary.get("tasks"):
            if save_to_file:
                self._save_summary(target_date, base_summary)
            return base_summary

        # Get activities for the day
        activities_data = self.vector_store.get_events_by_date(target_date)
        if not activities_data:
            if save_to_file:
                self._save_summary(target_date, base_summary)
            return base_summary

        # Convert to Event objects
        activities = self._convert_to_events(activities_data)

        # Enhance with RAG
        try:
            enhanced_summary = self.rag_system.enhance_daily_summary_with_rag(
                target_date, activities, base_summary
            )

            # Add RAG metadata
            enhanced_summary["rag_enhanced"] = True
            enhanced_summary["enhancement_timestamp"] = datetime.now().isoformat()

            if save_to_file:
                self._save_enhanced_summary(target_date, enhanced_summary)

            return enhanced_summary

        except Exception as e:
            print(f"RAG enhancement failed for daily summary: {e}")
            if save_to_file:
                self._save_summary(target_date, base_summary)
            return base_summary

    def generate_contextual_project_summary(
        self,
        project_name: str,
        days_back: int = 30
    ) -> Dict[str, Any]:
        """
        Generate a project-focused summary with RAG insights about progression and patterns.

        Args:
            project_name: Name of the project to summarize
            days_back: Number of days to look back

        Returns:
            Project summary with contextual insights
        """
        end_date = datetime.now().date()

        # Collect project activities across date range
        project_activities = []
        project_dates = []

        for days in range(days_back):
            check_date = end_date - datetime.timedelta(days=days)
            day_activities = self.vector_store.get_events_by_date(check_date)

            # Filter for project activities
            day_project_activities = [
                activity for activity in day_activities
                if activity["payload"].get("project") == project_name
            ]

            if day_project_activities:
                project_activities.extend(day_project_activities)
                project_dates.append(check_date)

        if not project_activities:
            return {
                "project": project_name,
                "error": "No activities found for this project in the specified timeframe",
                "days_searched": days_back
            }

        # Convert to events and get RAG context
        events = self._convert_to_events(project_activities)

        # Get contextual insights
        context = self.rag_system.retrieve_context_for_task_analysis(events, context_limit=20)

        # Generate project summary
        summary = {
            "project": project_name,
            "timeframe": f"Last {days_back} days",
            "total_activities": len(project_activities),
            "active_days": len(project_dates),
            "date_range": f"{min(project_dates)} to {max(project_dates)}",
            "patterns": context.get("common_patterns", []),
            "insights": context.get("category_insights", []),
            "productivity_trends": self._analyze_project_productivity(events, project_dates),
            "focus_areas": context.get("tool_usage", []),
            "recommendations": self._generate_project_recommendations(events, context)
        }

        return summary

    def generate_weekly_productivity_report(
        self,
        week_start: date
    ) -> Dict[str, Any]:
        """
        Generate a weekly productivity report with RAG-enhanced insights.

        Args:
            week_start: Start date of the week (should be a Monday)

        Returns:
            Weekly report with productivity patterns and insights
        """
        week_activities = []
        daily_summaries = []

        # Collect week's data
        for day_offset in range(7):
            current_date = week_start + datetime.timedelta(days=day_offset)
            day_activities = self.vector_store.get_events_by_date(current_date)

            if day_activities:
                week_activities.extend(day_activities)
                daily_summaries.append({
                    "date": current_date.isoformat(),
                    "activity_count": len(day_activities),
                    "day_name": current_date.strftime("%A")
                })

        if not week_activities:
            return {
                "week_start": week_start.isoformat(),
                "error": "No activities found for this week"
            }

        # Convert to events
        events = self._convert_to_events(week_activities)

        # Get comprehensive context
        task_context = self.rag_system.retrieve_context_for_task_analysis(events, context_limit=25)
        summary_context = self.rag_system.retrieve_context_for_summary(
            week_start, events, context_limit=20
        )

        # Analyze weekly patterns
        weekly_report = {
            "week_start": week_start.isoformat(),
            "week_end": (week_start + datetime.timedelta(days=6)).isoformat(),
            "total_activities": len(week_activities),
            "active_days": len(daily_summaries),
            "daily_breakdown": daily_summaries,

            # RAG-enhanced insights
            "productivity_patterns": summary_context.get("productivity_patterns", {}),
            "focus_trends": task_context.get("category_insights", []),
            "workflow_patterns": task_context.get("common_patterns", []),
            "project_distribution": self._analyze_weekly_projects(events),
            "time_usage_insights": self._analyze_weekly_time_patterns(events),

            # Comparative analysis
            "vs_previous_weeks": self._compare_with_previous_weeks(week_start, len(week_activities)),

            # Forward-looking
            "recommendations": self._generate_weekly_recommendations(events, task_context)
        }

        return weekly_report

    def contextual_search_with_insights(
        self,
        query: str,
        date_filter: Optional[date] = None,
        limit: int = 10
    ) -> Dict[str, Any]:
        """
        Perform contextual search with RAG-enhanced insights about the results.

        Args:
            query: Search query
            date_filter: Optional date to filter results
            limit: Maximum number of results

        Returns:
            Search results with contextual insights
        """
        from memorai.db.models import Event as EventModel
        # Use RAG system for contextual search
        search_results = self.rag_system.contextual_search(
            query, expand_context=True, limit=limit
        )

        if not search_results:
            return {
                "query": query,
                "results": [],
                "insights": "No matching activities found"
            }

        # Convert results to events for analysis
        result_events = []
        for result in search_results:
            try:
                payload = result["payload"]
                event = EventModel(
                    timestamp=datetime.fromisoformat(payload["timestamp"]),
                    type=payload["type"],
                    source=payload["source"],
                    project=payload.get("project"),
                    raw_content=payload.get("raw_content", ""),
                    summary=payload.get("summary"),
                    category=payload.get("category"),
                    embedding=result.get("vector")
                )
                result_events.append(event)
            except Exception as e:
                continue

        # Generate insights about the search results
        if result_events:
            context = self.rag_system.retrieve_context_for_task_analysis(
                result_events, context_limit=15
            )

            insights = {
                "patterns_found": context.get("common_patterns", []),
                "related_projects": context.get("project_context", []),
                "time_patterns": context.get("time_patterns", []),
                "categories_involved": context.get("category_insights", [])
            }
        else:
            insights = {}

        return {
            "query": query,
            "results": search_results,
            "result_count": len(search_results),
            "contextual_insights": insights,
            "search_enhanced": True
        }

    def _convert_to_events(self, activities_data: List[Dict[str, Any]]) -> List:
        """Convert activity data to Event objects."""
        from memorai.db.models import Event as EventModel
        events = []
        for activity_data in activities_data:
            try:
                payload = activity_data["payload"]
                event = EventModel(
                    timestamp=datetime.fromisoformat(payload["timestamp"]),
                    type=payload["type"],
                    source=payload["source"],
                    project=payload.get("project"),
                    raw_content=payload.get("raw_content", ""),
                    summary=payload.get("summary"),
                    category=payload.get("category"),
                    embedding=activity_data.get("vector")
                )
                events.append(event)
            except Exception as e:
                continue
        return events

    def _analyze_project_productivity(
        self,
        events: List,
        project_dates: List[date]
    ) -> Dict[str, Any]:
        """Analyze productivity trends for a project."""
        if not events or not project_dates:
            return {}

        # Calculate daily activity averages
        activities_per_day = len(events) / len(project_dates)

        # Category distribution
        categories = [e.category.value for e in events if e.category]
        category_distribution = {}
        if categories:
            from collections import Counter
            category_counter = Counter(categories)
            total_categorized = sum(category_counter.values())
            category_distribution = {
                cat: round(count / total_categorized * 100, 1)
                for cat, count in category_counter.items()
            }

        return {
            "activities_per_day": round(activities_per_day, 1),
            "total_active_days": len(project_dates),
            "category_distribution": category_distribution,
            "intensity": "High" if activities_per_day > 10 else "Medium" if activities_per_day > 5 else "Low"
        }

    def _analyze_weekly_projects(self, events: List) -> Dict[str, Any]:
        """Analyze project distribution for the week."""
        projects = [e.project for e in events if e.project]
        if not projects:
            return {"message": "No project information available"}

        from collections import Counter
        project_counter = Counter(projects)

        return {
            "total_projects": len(project_counter),
            "project_breakdown": dict(project_counter.most_common()),
            "primary_project": project_counter.most_common(1)[0] if project_counter else None
        }

    def _analyze_weekly_time_patterns(self, events: List) -> List[str]:
        """Analyze time usage patterns for the week."""
        patterns = []

        # Hour distribution
        hours = [e.timestamp.hour for e in events]
        if hours:
            avg_hour = sum(hours) / len(hours)
            early_activities = sum(1 for h in hours if h < 9)
            late_activities = sum(1 for h in hours if h > 18)

            if early_activities > len(hours) * 0.2:
                patterns.append("Significant early morning activity (before 9 AM)")
            if late_activities > len(hours) * 0.2:
                patterns.append("Notable evening work (after 6 PM)")
            if 9 <= avg_hour <= 17:
                patterns.append("Primarily standard business hours")

        # Day distribution
        weekdays = [e.timestamp.weekday() for e in events]
        weekend_activities = sum(1 for d in weekdays if d >= 5)
        if weekend_activities > 0:
            patterns.append(f"Weekend activity: {weekend_activities} activities")

        return patterns

    def _compare_with_previous_weeks(
        self,
        current_week_start: date,
        current_activity_count: int
    ) -> Dict[str, Any]:
        """Compare current week with previous weeks."""
        comparison = {"available": False}

        try:
            # Get previous 3 weeks for comparison
            previous_weeks = []
            for weeks_back in range(1, 4):
                prev_week_start = current_week_start - timedelta(weeks=weeks_back)
                week_activities = []

                for day_offset in range(7):
                    check_date = prev_week_start + timedelta(days=day_offset)
                    day_activities = self.vector_store.get_events_by_date(check_date)
                    if day_activities:
                        week_activities.extend(day_activities)

                if week_activities:
                    previous_weeks.append(len(week_activities))

            if previous_weeks:
                avg_previous = sum(previous_weeks) / len(previous_weeks)
                comparison = {
                    "available": True,
                    "current_week_activities": current_activity_count,
                    "average_previous_weeks": round(avg_previous, 1),
                    "trend": "above_average" if current_activity_count > avg_previous * 1.1
                            else "below_average" if current_activity_count < avg_previous * 0.9
                            else "consistent"
                }

        except Exception as e:
            print(f"Error in week comparison: {e}")

        return comparison

    def _generate_project_recommendations(
        self,
        events: List,
        context: Dict[str, Any]
    ) -> List[str]:
        """Generate recommendations based on project analysis."""
        recommendations = []

        # Pattern-based recommendations
        patterns = context.get("common_patterns", [])
        if any("browser" in pattern.lower() for pattern in patterns):
            recommendations.append("Consider consolidating research sessions for better focus")

        # Category insights
        insights = context.get("category_insights", [])
        if any("coding" in insight.lower() for insight in insights):
            recommendations.append("Maintain coding momentum - strong development patterns detected")

        if not recommendations:
            recommendations.append("Continue current work patterns - maintaining good project engagement")

        return recommendations

    def _generate_weekly_recommendations(
        self,
        events: List,
        context: Dict[str, Any]
    ) -> List[str]:
        """Generate weekly productivity recommendations."""
        recommendations = []

        # Focus analysis
        categories = [e.category.value for e in events if e.category]
        if categories:
            from collections import Counter
            cat_counter = Counter(categories)
            if len(cat_counter) > 5:
                recommendations.append("Consider focusing on fewer categories for deeper work")
            elif len(cat_counter) <= 2:
                recommendations.append("Good focus maintained - consider this pattern for future weeks")

        # Time patterns
        time_patterns = context.get("time_patterns", [])
        if any("morning" in pattern.lower() for pattern in time_patterns):
            recommendations.append("Leverage morning productivity patterns identified")

        return recommendations or ["Maintain current productivity patterns"]

    def _save_enhanced_summary(self, target_date: date, summary: Dict[str, Any]) -> None:
        """Save RAG-enhanced summary to file."""
        try:
            # Save in both regular and RAG-enhanced formats
            self._save_summary(target_date, summary)

            # Save RAG-specific insights separately
            rag_insights = {
                "date": target_date.isoformat(),
                "rag_enhanced": summary.get("rag_enhanced", False),
                "contextual_overview": summary.get("contextual_overview"),
                "enhancement_timestamp": summary.get("enhancement_timestamp"),
                "productivity_patterns": summary.get("productivity_patterns"),
                "historical_context": summary.get("historical_context")
            }

            rag_file = self.data_dir / f"{target_date.isoformat()}_rag_insights.json"
            with open(rag_file, 'w') as f:
                json.dump(rag_insights, f, indent=2, default=str)

        except Exception as e:
            print(f"Error saving enhanced summary: {e}")
