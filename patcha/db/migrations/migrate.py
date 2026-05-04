"""Migration utilities for converting from activity-first to task-first architecture."""

from datetime import datetime, date, timedelta
from typing import List, Dict, Any, Optional
from rich.console import Console
from rich.progress import (
    Progress,
    SpinnerColumn,
    TextColumn,
    BarColumn,
    TaskProgressColumn,
)

from patcha.db.models import Event, EventType, Category
from patcha.db.store import VectorStore
from patcha.compaction import TaskIdentifier
from patcha.db.tasks import TaskStore
from patcha.process import EventPreprocessor

console = Console()


class DataMigration:
    def __init__(self):
        self.vector_store = VectorStore()
        self.preprocessor = EventPreprocessor()
        self.task_identifier = TaskIdentifier(self.preprocessor)
        self.task_store = TaskStore()

    def migrate_date_range(
        self,
        start_date: date,
        end_date: date,
        batch_size: int = 50,
        dry_run: bool = False,
    ) -> Dict[str, Any]:
        """
        Migrate activities to tasks for a date range.

        Args:
            start_date: Start date for migration
            end_date: End date for migration
            batch_size: Number of activities to process in each batch
            dry_run: If True, don't actually store tasks, just analyze

        Returns:
            Migration statistics
        """
        console.print(
            f"[bold cyan]Migrating data from {start_date} to {end_date}[/bold cyan]"
        )

        if dry_run:
            console.print("[yellow]DRY RUN MODE - No data will be stored[/yellow]")

        total_dates = (end_date - start_date).days + 1
        total_activities = 0
        total_tasks_identified = 0
        total_tasks_stored = 0
        errors = []
        daily_stats = {}

        with Progress(
            SpinnerColumn(),
            TextColumn("[progress.description]{task.description}"),
            BarColumn(),
            TaskProgressColumn(),
            console=console,
        ) as progress:
            main_task = progress.add_task("Migrating dates...", total=total_dates)

            current_date = start_date
            while current_date <= end_date:
                try:
                    # Update progress
                    progress.update(
                        main_task,
                        description=f"Processing {current_date}...",
                        advance=1,
                    )

                    # Migrate single date
                    date_stats = self.migrate_single_date(
                        current_date, batch_size, dry_run, progress
                    )

                    daily_stats[current_date.isoformat()] = date_stats
                    total_activities += date_stats.get("activities_processed", 0)
                    total_tasks_identified += date_stats.get("tasks_identified", 0)
                    total_tasks_stored += date_stats.get("tasks_stored", 0)

                    if date_stats.get("errors"):
                        errors.extend(date_stats["errors"])

                except Exception as e:
                    error_msg = f"Error processing {current_date}: {str(e)}"
                    errors.append(error_msg)
                    console.print(f"[red]{error_msg}[/red]")

                current_date += timedelta(days=1)

        # Generate migration report
        report = {
            "migration_completed_at": datetime.now().isoformat(),
            "date_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_dates": total_dates,
            },
            "summary": {
                "total_activities_processed": total_activities,
                "total_tasks_identified": total_tasks_identified,
                "total_tasks_stored": total_tasks_stored,
                "success_rate": total_tasks_stored / max(total_tasks_identified, 1),
                "errors_count": len(errors),
            },
            "daily_stats": daily_stats,
            "errors": errors,
            "dry_run": dry_run,
        }

        # Display final report
        self._display_migration_report(report)

        return report

    def migrate_single_date(
        self,
        target_date: date,
        batch_size: int = 50,
        dry_run: bool = False,
        progress: Optional[Progress] = None,
    ) -> Dict[str, Any]:
        """Migrate activities for a single date."""

        date_stats = {
            "date": target_date.isoformat(),
            "activities_found": 0,
            "activities_processed": 0,
            "tasks_identified": 0,
            "tasks_stored": 0,
            "errors": [],
        }

        try:
            # Get activities for the date
            activities_data = self.vector_store.get_events_by_date(target_date)
            date_stats["activities_found"] = len(activities_data)

            if not activities_data:
                return date_stats

            if progress:
                date_task = progress.add_task(
                    f"Processing {len(activities_data)} activities for {target_date}...",
                    total=len(activities_data),
                )

            # Convert activity data to Event objects
            events = []
            for i, activity_data in enumerate(activities_data):
                try:
                    event = self._convert_activity_to_event(activity_data)
                    if event:
                        events.append(event)
                        date_stats["activities_processed"] += 1

                    if progress:
                        progress.update(date_task, advance=1)

                except Exception as e:
                    error_msg = f"Error converting activity {i}: {str(e)}"
                    date_stats["errors"].append(error_msg)

            if not events:
                return date_stats

            # Process events in batches to identify tasks
            identified_tasks = []
            for i in range(0, len(events), batch_size):
                batch = events[i : i + batch_size]
                try:
                    batch_tasks = self.task_identifier.identify_tasks_from_activities(
                        batch
                    )
                    identified_tasks.extend(batch_tasks)
                except Exception as e:
                    error_msg = f"Error identifying tasks for batch {i // batch_size + 1}: {str(e)}"
                    date_stats["errors"].append(error_msg)

            date_stats["tasks_identified"] = len(identified_tasks)

            # Store tasks if not dry run
            if not dry_run and identified_tasks:
                stored_count = 0
                for task in identified_tasks:
                    try:
                        if self.task_store.store_task(task):
                            stored_count += 1

                            # Update original events with task IDs
                            self._link_activities_to_task(task, activities_data)

                    except Exception as e:
                        error_msg = f"Error storing task {task.id}: {str(e)}"
                        date_stats["errors"].append(error_msg)

                date_stats["tasks_stored"] = stored_count

            if progress:
                progress.remove_task(date_task)

        except Exception as e:
            date_stats["errors"].append(f"General error for {target_date}: {str(e)}")

        return date_stats

    def _convert_activity_to_event(
        self, activity_data: Dict[str, Any]
    ) -> Optional[Event]:
        """Convert vector store activity data to Event object."""
        try:
            payload = activity_data.get("payload", {})

            # Create Event object
            event = Event(
                timestamp=datetime.fromisoformat(payload["timestamp"]),
                type=EventType(payload["type"]),
                source=payload.get("source", "unknown"),
                project=payload.get("project"),
                raw_content=payload.get("raw_content", ""),
                summary=payload.get("summary"),
                category=Category(payload["category"])
                if payload.get("category")
                else None,
                metadata=payload.get("metadata", {}),
                embedding=None,  # Vector embeddings are stored separately
            )

            # Try to get embedding from vector store
            try:
                if "id" in activity_data:
                    # Retrieve the actual vector for this point
                    points = self.vector_store.client.retrieve(
                        collection_name=self.vector_store.collection_name,
                        ids=[activity_data["id"]],
                    )
                    if points and len(points) > 0:
                        event.embedding = points[0].vector
            except Exception:
                pass  # Embedding not critical for task identification

            return event

        except Exception as e:
            console.print(f"[red]Error converting activity: {e}[/red]")
            return None

    def _link_activities_to_task(self, task, activities_data: List[Dict[str, Any]]):
        """Update original activity records with task ID."""
        try:
            # This would update the original activities in the vector store
            # with task_id information. For now, we'll skip this step
            # as it would require more complex vector store updates
            pass
        except Exception as e:
            console.print(
                f"[yellow]Warning: Could not link activities to task {task.id}: {e}[/yellow]"
            )

    def _display_migration_report(self, report: Dict[str, Any]):
        """Display a comprehensive migration report."""
        console.print("\n" + "=" * 60)
        console.print("[bold cyan]MIGRATION REPORT[/bold cyan]")
        console.print("=" * 60)

        # Summary
        summary = report["summary"]
        console.print("\n[bold]Summary:[/bold]")
        console.print(
            f"• Activities processed: {summary['total_activities_processed']}"
        )
        console.print(f"• Tasks identified: {summary['total_tasks_identified']}")
        console.print(f"• Tasks stored: {summary['total_tasks_stored']}")
        console.print(f"• Success rate: {summary['success_rate']:.1%}")
        console.print(f"• Errors: {summary['errors_count']}")

        if report["dry_run"]:
            console.print(
                "\n[yellow]This was a DRY RUN - no data was actually stored[/yellow]"
            )

        # Daily breakdown (show top/bottom performers)
        daily_stats = report["daily_stats"]
        if daily_stats:
            console.print(
                "\n[bold]Daily Breakdown (Top 5 dates by task identification):[/bold]"
            )
            sorted_dates = sorted(
                daily_stats.items(),
                key=lambda x: x[1].get("tasks_identified", 0),
                reverse=True,
            )

            for i, (date_str, stats) in enumerate(sorted_dates[:5], 1):
                console.print(
                    f"{i}. {date_str}: {stats.get('tasks_identified', 0)} tasks from {stats.get('activities_processed', 0)} activities"
                )

        # Errors
        if report["errors"]:
            console.print(f"\n[bold red]Errors ({len(report['errors'])}):[/bold red]")
            for i, error in enumerate(report["errors"][:10], 1):  # Show first 10 errors
                console.print(f"{i}. {error}")
            if len(report["errors"]) > 10:
                console.print(f"... and {len(report['errors']) - 10} more errors")

        console.print("=" * 60)

    def analyze_migration_feasibility(
        self, start_date: date, end_date: date
    ) -> Dict[str, Any]:
        """Analyze how much data would be migrated and potential issues."""

        console.print(
            f"[bold cyan]Analyzing migration feasibility for {start_date} to {end_date}[/bold cyan]"
        )

        analysis = {
            "date_range": {
                "start_date": start_date.isoformat(),
                "end_date": end_date.isoformat(),
                "total_days": (end_date - start_date).days + 1,
            },
            "daily_activity_counts": {},
            "total_activities": 0,
            "dates_with_data": 0,
            "estimated_tasks": 0,
            "potential_issues": [],
        }

        current_date = start_date
        while current_date <= end_date:
            try:
                activities = self.vector_store.get_events_by_date(current_date)
                activity_count = len(activities)

                analysis["daily_activity_counts"][current_date.isoformat()] = (
                    activity_count
                )
                analysis["total_activities"] += activity_count

                if activity_count > 0:
                    analysis["dates_with_data"] += 1
                    # Rough estimate: 1 task per 3-5 activities
                    estimated_daily_tasks = max(1, activity_count // 4)
                    analysis["estimated_tasks"] += estimated_daily_tasks

                # Check for potential issues
                if activity_count > 200:
                    analysis["potential_issues"].append(
                        f"{current_date}: High activity count ({activity_count}) may require batch processing"
                    )

            except Exception as e:
                analysis["potential_issues"].append(
                    f"{current_date}: Error accessing data - {str(e)}"
                )

            current_date += timedelta(days=1)

        # Analysis summary
        console.print("\n[bold]Migration Feasibility Analysis:[/bold]")
        console.print(
            f"• Total activities to process: {analysis['total_activities']:,}"
        )
        console.print(
            f"• Days with data: {analysis['dates_with_data']}/{analysis['date_range']['total_days']}"
        )
        console.print(f"• Estimated tasks to create: {analysis['estimated_tasks']:,}")
        console.print(
            f"• Average activities per day: {analysis['total_activities'] / max(analysis['dates_with_data'], 1):.1f}"
        )

        if analysis["potential_issues"]:
            console.print(
                f"\n[yellow]Potential Issues ({len(analysis['potential_issues'])}):[/yellow]"
            )
            for issue in analysis["potential_issues"][:5]:
                console.print(f"• {issue}")
            if len(analysis["potential_issues"]) > 5:
                console.print(
                    f"• ... and {len(analysis['potential_issues']) - 5} more issues"
                )

        # Recommendations
        recommendations = []
        if analysis["total_activities"] > 10000:
            recommendations.append("Consider migrating in smaller date ranges")
        if analysis["total_activities"] > 50000:
            recommendations.append("Use batch processing with batch_size=20-30")
        if len(analysis["potential_issues"]) > 0:
            recommendations.append("Review potential issues before proceeding")

        if recommendations:
            console.print("\n[bold blue]Recommendations:[/bold blue]")
            for rec in recommendations:
                console.print(f"• {rec}")

        analysis["recommendations"] = recommendations
        analysis["analyzed_at"] = datetime.now().isoformat()

        return analysis

    def cleanup_duplicate_tasks(
        self, target_date: Optional[date] = None
    ) -> Dict[str, Any]:
        """Clean up duplicate tasks that might have been created during migration."""

        console.print("[bold cyan]Cleaning up duplicate tasks...[/bold cyan]")

        if target_date:
            tasks = self.task_store.get_tasks_by_date(target_date)
            date_range = f"for {target_date}"
        else:
            # Get tasks from last 30 days
            end_date = date.today()
            start_date = end_date - timedelta(days=30)
            tasks = self.task_store.get_tasks_by_date_range(start_date, end_date)
            date_range = "from last 30 days"

        console.print(f"Analyzing {len(tasks)} tasks {date_range}...")

        if not tasks:
            return {"message": "No tasks found to analyze", "duplicates_removed": 0}

        # Find potential duplicates based on similarity
        duplicates_found = []
        tasks_to_remove = []

        for i, task1 in enumerate(tasks):
            for j, task2 in enumerate(tasks[i + 1 :], start=i + 1):
                similarity_score = self._calculate_task_similarity_for_cleanup(
                    task1, task2
                )

                if similarity_score > 0.9:  # Very high similarity threshold
                    duplicates_found.append(
                        {
                            "task1": {"id": task1.id, "title": task1.title},
                            "task2": {"id": task2.id, "title": task2.title},
                            "similarity": similarity_score,
                        }
                    )

                    # Keep the task with more activities or higher confidence
                    if task1.activity_count > task2.activity_count or (
                        task1.activity_count == task2.activity_count
                        and task1.confidence_score > task2.confidence_score
                    ):
                        tasks_to_remove.append(task2.id)
                    else:
                        tasks_to_remove.append(task1.id)

        # Remove duplicates
        removed_count = 0
        for task_id in set(tasks_to_remove):  # Remove duplicates from removal list
            if self.task_store.delete_task(task_id):
                removed_count += 1

        cleanup_report = {
            "analyzed_tasks": len(tasks),
            "duplicates_found": len(duplicates_found),
            "duplicates_removed": removed_count,
            "duplicate_pairs": duplicates_found[:10],  # Show first 10 pairs
            "cleaned_at": datetime.now().isoformat(),
        }

        console.print(
            f"[green]Cleanup completed: {removed_count} duplicate tasks removed[/green]"
        )

        return cleanup_report

    def _calculate_task_similarity_for_cleanup(self, task1, task2) -> float:
        """Calculate similarity between two tasks for duplicate detection."""
        similarity = 0.0
        factors = 0

        # Title similarity (very important for duplicates)
        if task1.title and task2.title:
            if task1.title.lower().strip() == task2.title.lower().strip():
                similarity += 0.5
            elif (
                task1.title.lower() in task2.title.lower()
                or task2.title.lower() in task1.title.lower()
            ):
                similarity += 0.3
            factors += 0.5

        # Time proximity (tasks very close in time might be duplicates)
        if task1.start_time and task2.start_time:
            time_diff_minutes = (
                abs((task1.start_time - task2.start_time).total_seconds()) / 60
            )
            if time_diff_minutes < 5:  # Within 5 minutes
                similarity += 0.3
            elif time_diff_minutes < 30:  # Within 30 minutes
                similarity += 0.1
            factors += 0.3

        # Same category and project
        if task1.category == task2.category and task1.project == task2.project:
            similarity += 0.2
        factors += 0.2

        return similarity / factors if factors > 0 else 0.0


# CLI integration functions
def migrate_cli_wrapper(start_date: str, end_date: str, batch_size: int, dry_run: bool):
    """Wrapper function for CLI integration."""
    migration = DataMigration()

    start = datetime.strptime(start_date, "%Y-%m-%d").date()
    end = datetime.strptime(end_date, "%Y-%m-%d").date()

    return migration.migrate_date_range(start, end, batch_size, dry_run)
