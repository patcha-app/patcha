import asyncio
import logging
import os
import signal
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Optional, List
import click
from rich.console import Console
from rich.table import Table
from rich.progress import Progress, SpinnerColumn, TextColumn
from rich.panel import Panel
from rich.markdown import Markdown

from patcha.collectors.git import GitCollector
from patcha.collectors.browser import BrowserCollector
from patcha.collectors.terminal import TerminalCollector
from patcha.collectors.window import WindowCollector
from patcha.collectors.accessibility import AccessibilityCollector
from patcha.process import EventPreprocessor
from patcha.db.store import VectorStore
from patcha.summary import DailySummarizer
from patcha.db.models import Category
from patcha.config import config
from patcha.daemon import ActivityDaemon
from patcha.db.retrieval.cluster import ActivityClusterer
from patcha.categorize import EnhancedCategorizer
from patcha.db.graph import KnowledgeGraph
from patcha.db.entities import EntityExtractor
from patcha.db.retrieval.graphrag import GraphRAGSystem

console = Console()

try:
    from importlib.metadata import version as _pkg_version
    _VERSION = _pkg_version("patcha")
except Exception:
    _VERSION = "0.1.0"

_preprocessor: Optional[EventPreprocessor] = None
_vector_store: Optional[VectorStore] = None

_COMMANDS_WITHOUT_KEY = {"start-daemon", "stop-daemon", "daemon-status", "status", "config"}


def _ensure_api_key() -> None:
    if config.openai_api_key:
        return
    console.print("[yellow]No OpenAI API key configured.[/yellow]")
    key = click.prompt("Enter your OpenAI API key", hide_input=True)
    env_file = Path.home() / ".patcha" / ".env"
    env_file.parent.mkdir(parents=True, exist_ok=True)
    with open(env_file, "a") as f:
        f.write(f"OPENAI_API_KEY={key}\n")
    os.environ["OPENAI_API_KEY"] = key
    config.openai_api_key = key
    console.print("[green]API key saved to ~/.patcha/.env[/green]")


def _get_preprocessor() -> EventPreprocessor:
    global _preprocessor
    if _preprocessor is None:
        _preprocessor = EventPreprocessor()
    return _preprocessor


def _get_vector_store() -> VectorStore:
    global _vector_store
    if _vector_store is None:
        _vector_store = VectorStore()
    return _vector_store


@click.group()
@click.version_option(version=_VERSION)
@click.option("--debug", is_flag=True, default=False, help="Enable debug logging")
@click.pass_context
def cli(ctx, debug):
    """patcha - AI-powered memory and activity tracking system."""
    from patcha.utils.logging import init_logging
    init_logging(level=logging.DEBUG if debug else logging.INFO)
    if ctx.invoked_subcommand not in _COMMANDS_WITHOUT_KEY:
        _ensure_api_key()


@cli.command()
@click.option("--date", "-d", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Date to collect activities for (default: today)")
@click.option("--sources", "-s", multiple=True,
              type=click.Choice(["git", "browser", "terminal", "window", "accessibility"]),
              default=["git", "browser", "terminal", "window", "accessibility"],
              help="Sources to collect from")
@click.option("--repo-path", "-r", type=click.Path(exists=True),
              help="Git repository path (default: current directory)")
def collect(date: Optional[datetime], sources: tuple, repo_path: Optional[str]):
    """Collect activities from various sources."""

    target_date = date.date() if date else datetime.now().date()
    since = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)

    console.print(f"Collecting activities for {target_date}")

    all_events = []

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console,
    ) as progress:

        if "git" in sources:
            task = progress.add_task("Collecting git activities...", total=None)
            git_collector = GitCollector(repo_path)
            events = git_collector.collect_commits(since)
            events.extend(git_collector.collect_stashes(since))
            all_events.extend(events)
            progress.update(task, description=f"Collected {len(events)} git activities")

        if "browser" in sources:
            task = progress.add_task("Collecting browser activities...", total=None)
            browser_collector = BrowserCollector()
            events = browser_collector.collect_all(since)
            all_events.extend(events)
            progress.update(task, description=f"Collected {len(events)} browser activities")

        if "terminal" in sources:
            task = progress.add_task("Collecting terminal activities...", total=None)
            terminal_collector = TerminalCollector()
            events = terminal_collector.collect_all(since)
            all_events.extend(events)
            progress.update(task, description=f"Collected {len(events)} terminal activities")

        if "window" in sources:
            task = progress.add_task("Collecting window sessions...", total=None)
            window_collector = WindowCollector()
            events = window_collector.collect_windows(since)
            all_events.extend(events)
            progress.update(task, description=f"Collected {len(events)} window sessions")

        if "accessibility" in sources:
            task = progress.add_task("Collecting accessibility text...", total=None)
            accessibility_collector = AccessibilityCollector()
            events = accessibility_collector.collect_screen_text(since)
            all_events.extend(events)
            progress.update(task, description=f"Collected {len(events)} accessibility events")

        if all_events:
            task = progress.add_task("Processing events...", total=len(all_events))
            preprocessor = _get_preprocessor()

            processed_events = []
            for i, event in enumerate(all_events):
                processed_event = preprocessor.process_event(event)
                processed_events.append(processed_event)
                progress.advance(task)

            task = progress.add_task("Storing events...", total=None)
            vector_store = _get_vector_store()
            stored_count = vector_store.store_events(processed_events)
            progress.update(task, description=f"Stored {stored_count} events")

    console.print(f"[green]Successfully collected and stored {len(all_events)} activities![/green]")


@cli.command()
@click.option("--date", "-d", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Date to observe (default: today)")
@click.option("--repo-path", "-r", type=click.Path(exists=True),
              help="Git repository path (default: current directory)")
@click.option("--min-cluster-size", default=3, show_default=True,
              help="Minimum events per cluster")
def observe(date: Optional[datetime], repo_path: Optional[str], min_cluster_size: int):
    """Observability layer: collect + cluster activities without LLM or Qdrant."""
    from patcha.db.retrieval.cluster import cluster_raw

    target_date = date.date() if date else datetime.now().date()
    since = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)

    console.print(f"Observing activities for [bold]{target_date}[/bold] (no LLM required)")

    all_events = []
    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}"),
                  console=console) as progress:

        for label, collector_fn in [
            ("git", lambda: _collect_git(repo_path, since)),
            ("browser", lambda: BrowserCollector().collect_all(since)),
            ("terminal", lambda: TerminalCollector().collect_all(since)),
            ("window", lambda: WindowCollector().collect_windows(since)),
            ("screen", lambda: AccessibilityCollector().collect_screen_text(since)),
        ]:
            task = progress.add_task(f"Collecting {label}...", total=None)
            try:
                events = collector_fn()
                all_events.extend(events)
                progress.update(task, description=f"[green]{label}[/] - {len(events)} events")
            except Exception as exc:
                progress.update(task, description=f"[yellow]{label}[/] - skipped ({exc})")

    if not all_events:
        console.print("[yellow]No events collected.[/yellow]")
        return

    clusters = cluster_raw(all_events, min_cluster_size=min_cluster_size)
    real = [c for c in clusters if not c["noise"]]
    noise = next((c for c in clusters if c["noise"]), None)

    console.print(f"\nFound [bold]{len(real)}[/bold] activity clusters "
                  f"from [bold]{len(all_events)}[/bold] events\n")

    table = Table(show_header=True, header_style="bold cyan", box=None, pad_edge=False)
    table.add_column("#", style="dim", width=3)
    table.add_column("Time range", min_width=20)
    table.add_column("Events", justify="right", width=7)
    table.add_column("Dominant source", min_width=14)
    table.add_column("Dominant app", min_width=16)
    table.add_column("Sources breakdown")

    for i, c in enumerate(real, 1):
        start = c["start_time"][11:16]
        end = c["end_time"][11:16]
        breakdown = "  ".join(f"{s}:{n}" for s, n in sorted(c["source_breakdown"].items()))
        table.add_row(
            str(i),
            f"{start} - {end}",
            str(c["size"]),
            c["dominant_source"] or "-",
            c["dominant_app"] or "-",
            breakdown,
        )

    console.print(table)

    if noise:
        console.print(f"\n[dim]{noise['size']} unclustered events (noise)[/dim]")


def _collect_git(repo_path: Optional[str], since: datetime):
    gc = GitCollector(repo_path)
    events = gc.collect_commits(since)
    events.extend(gc.collect_stashes(since))
    return events


@cli.command()
@click.option("--date", "-d", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Date to summarize (default: today)")
@click.option("--save", "-s", is_flag=True, help="Save summary to file")
@click.option("--output-dir", "-o", type=click.Path(),
              help="Output directory for summaries")
def summarize(date: Optional[datetime], save: bool, output_dir: Optional[str]):
    """Generate daily summary for activities."""

    target_date = date.date() if date else datetime.now().date()

    with console.status("Generating daily summary..."):
        vector_store = _get_vector_store()
        summarizer = DailySummarizer(vector_store)
        summary = summarizer.generate_daily_summary(target_date)

    if not summary:
        console.print(f"[yellow]No activities found for {target_date}[/yellow]")
        return

    console.print(Panel.fit(
        f"Daily Summary - {datetime.fromisoformat(summary.date).strftime('%B %d, %Y')}",
        style="bold blue"
    ))

    console.print(f"\n[bold]Overview:[/bold]")
    console.print(summary.overall_summary)

    console.print(f"\n[bold]Statistics:[/bold]")
    console.print(f"Total Activities: {summary.total_events}")
    console.print(f"Projects: {len(summary.projects_worked_on)}")

    table = Table(title="Activity Breakdown by Category")
    table.add_column("Category", style="cyan")
    table.add_column("Count", style="green")
    table.add_column("Summary", style="white", width=60)

    for category, count in summary.events_by_category.items():
        if count > 0:
            category_summary = summary.category_summaries.get(category, "No summary")
            table.add_row(category.value, str(count), category_summary[:100] + "..." if len(category_summary) > 100 else category_summary)

    console.print(table)

    if summary.projects_worked_on:
        console.print(f"\n[bold]Projects Worked On:[/bold]")
        for project in summary.projects_worked_on:
            console.print(f"- {project}")

    if save:
        output_path = Path(output_dir) if output_dir else None
        with console.status("Saving summary..."):
            success = summarizer.save_summary(summary, output_path)

        if success:
            save_dir = output_path or config.data_dir / "summaries"
            console.print(f"[green]Summary saved to {save_dir}[/green]")
        else:
            console.print("[red]Error saving summary[/red]")


@cli.command()
@click.argument("query")
@click.option("--limit", "-l", default=10, help="Number of results to return")
@click.option("--date", "-d", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="Filter by specific date")
@click.option("--category", "-c", type=click.Choice([cat.value for cat in Category]),
              help="Filter by category")
@click.option("--project", "-p", help="Filter by project")
def search(query: str, limit: int, date: Optional[datetime], category: Optional[str], project: Optional[str]):
    """Search activities using semantic search."""

    with console.status("Searching activities..."):
        preprocessor = _get_preprocessor()
        query_embedding = preprocessor.generate_embedding(query)

        if not query_embedding:
            console.print("[red]Error generating query embedding[/red]")
            return

        vector_store = _get_vector_store()

        date_filter = date.date() if date else None
        category_filter = Category(category) if category else None

        results = vector_store.search_events(
            query_vector=query_embedding,
            limit=limit,
            date_filter=date_filter,
            category_filter=category_filter,
            project_filter=project
        )

    if not results:
        console.print("[yellow]No results found[/yellow]")
        return

    console.print(f"Found {len(results)} results for: [bold]{query}[/bold]\n")

    for i, result in enumerate(results, 1):
        payload = result["payload"]
        score = result["score"]

        console.print(Panel(
            f"[bold]{payload.get('type', 'unknown').upper()}[/bold] - Score: {score:.3f}\n"
            f"[dim]{payload.get('timestamp', 'unknown')}[/dim]\n"
            f"Project: {payload.get('project', 'N/A')} | Category: {payload.get('category', 'N/A')}\n\n"
            f"{payload.get('summary', payload.get('raw_content', 'No content')[:200])}",
            title=f"Result {i}",
            expand=False
        ))


@cli.command()
@click.option("--start-date", "-s", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="Start date for review")
@click.option("--end-date", "-e", type=click.DateTime(formats=["%Y-%m-%d"]),
              help="End date for review")
@click.option("--days", "-d", default=7, help="Number of days to review (default: 7)")
def review(start_date: Optional[datetime], end_date: Optional[datetime], days: int):
    """Review activities over a date range."""

    if not start_date and not end_date:
        end_date = datetime.now()
        start_date = end_date - timedelta(days=days)
    elif start_date and not end_date:
        end_date = start_date + timedelta(days=days)
    elif end_date and not start_date:
        start_date = end_date - timedelta(days=days)

    current_date = start_date.date()
    end_date_obj = end_date.date()

    console.print(f"Review from {current_date} to {end_date_obj}\n")

    vector_store = _get_vector_store()
    summarizer = DailySummarizer(vector_store)

    while current_date <= end_date_obj:
        with console.status(f"Loading summary for {current_date}..."):
            summary = summarizer.load_summary(current_date)

        if summary:
            console.print(Panel(
                f"[bold]{datetime.fromisoformat(summary.date).strftime('%A, %B %d')}[/bold]\n"
                f"Activities: {summary.total_events} | "
                f"Projects: {len(summary.projects_worked_on)}\n\n"
                f"{summary.overall_summary}",
                style="blue"
            ))
        else:
            console.print(Panel(
                f"[bold]{current_date.strftime('%A, %B %d')}[/bold]\n"
                "[dim]No summary available[/dim]",
                style="dim"
            ))

        current_date += timedelta(days=1)


@cli.command()
def status():
    """Show system status and statistics."""

    try:
        vector_store = _get_vector_store()
        info = vector_store.get_collection_info()

        console.print(Panel.fit("System Status", style="bold green"))

        table = Table()
        table.add_column("Metric", style="cyan")
        table.add_column("Value", style="green")

        table.add_row("Collection Name", info.get("name", "Unknown"))
        table.add_row("Total Points", str(info.get("points_count", 0)))
        table.add_row("Vector Count", str(info.get("vectors_count", 0)))
        table.add_row("Status", info.get("status", "Unknown"))

        table.add_row("Qdrant URL", config.qdrant_url)
        table.add_row("Data Directory", str(config.data_dir))
        table.add_row("OpenAI API", "Configured" if config.openai_api_key else "Not configured")

        console.print(table)

        summaries_dir = config.data_dir / "summaries"
        if summaries_dir.exists():
            summary_count = len(list(summaries_dir.glob("*.json")))
            console.print(f"\n[bold]Summaries:[/bold] {summary_count} daily summaries saved")

    except Exception as e:
        console.print(f"[red]Error getting system status: {e}[/red]")


@cli.command()
@click.argument("config_key")
@click.argument("config_value", required=False)
def config_cmd(config_key: str, config_value: Optional[str]):
    """Get or set configuration values."""

    config_file = Path(".env")

    if config_value is None:
        value = getattr(config, config_key.lower(), None)
        if value:
            console.print(f"{config_key}: {value}")
        else:
            console.print(f"[yellow]Configuration key '{config_key}' not found[/yellow]")
    else:
        lines = []
        key_found = False

        if config_file.exists():
            with open(config_file, "r") as f:
                lines = f.readlines()

        for i, line in enumerate(lines):
            if line.startswith(f"{config_key.upper()}="):
                lines[i] = f"{config_key.upper()}={config_value}\n"
                key_found = True
                break

        if not key_found:
            lines.append(f"{config_key.upper()}={config_value}\n")

        with open(config_file, "w") as f:
            f.writelines(lines)

        console.print(f"[green]Set {config_key} = {config_value}[/green]")


@cli.command("compact-day")
@click.option("--date", "-d", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Date to compact (default: yesterday)")
@click.option("--dry-run", is_flag=True, default=False,
              help="Preview without writing or deleting anything")
@click.option("--force", is_flag=True, default=False,
              help="Re-compact even if already done")
def compact_day(date: Optional[datetime], dry_run: bool, force: bool):
    """Compact raw activities for a day into tasks, then delete the raw events."""
    from patcha.compaction import DailyCompactor

    if date is None:
        target = (datetime.now() - timedelta(days=1)).date()
    else:
        target = date.date()

    label = "[dim](dry run)[/dim]" if dry_run else ""
    console.print(f"Compacting {target} {label}")

    compactor = DailyCompactor()

    with console.status("Running compaction..."):
        result = compactor.compact_day(target, dry_run=dry_run, force=force)

    if result.get("skipped"):
        console.print(Panel(
            f"[yellow]Skipped:[/yellow] {result.get('reason', 'unknown')}\n"
            f"Date: {result.get('date', target)}",
            title="compact-day",
        ))
        return

    table = Table(show_header=False, box=None)
    table.add_column("Key", style="cyan")
    table.add_column("Value", style="white")
    table.add_row("Date", str(result.get("date", target)))
    table.add_row("Raw events fetched", str(result.get("event_count", 0)))
    table.add_row("After dedup", str(result.get("deduped_count", 0)))
    table.add_row("Tasks created", str(result.get("task_count", 0)))
    table.add_row("Raw events deleted", "no (dry run)" if dry_run else "yes")

    console.print(Panel(table, title="compact-day result"))


@cli.command()
@click.option("--poll-interval", "-p", default=60, help="Poll interval in seconds")
@click.option("--batch-size", "-b", default=50, help="Batch size for processing events")
@click.option("--foreground", "-f", is_flag=True, help="Run in foreground (don't daemonize)")
def start_daemon(poll_interval: int, batch_size: int, foreground: bool):
    """Start the background activity collection daemon."""

    daemon = ActivityDaemon(poll_interval=poll_interval, batch_size=batch_size)
    status = daemon.get_status()

    if status["running"]:
        console.print(f"[yellow]Daemon is already running (PID: {status['pid']})[/yellow]")
        return

    console.print(f"Starting daemon with {poll_interval}s poll interval...")

    if foreground:
        daemon.start()
    else:
        pid = os.fork()
        if pid == 0:
            os.setsid()
            pid2 = os.fork()
            if pid2 == 0:
                daemon.start()
            else:
                sys.exit(0)
        else:
            console.print(f"[green]Daemon started in background (PID: {pid})[/green]")


@cli.command()
def stop_daemon():
    """Stop the background activity collection daemon."""

    daemon = ActivityDaemon()
    status = daemon.get_status()

    if not status["running"]:
        console.print("[yellow]Daemon is not running[/yellow]")
        return

    pid = status["pid"]
    try:
        os.kill(pid, signal.SIGTERM)
        console.print(f"[green]Daemon stopped (PID: {pid})[/green]")
    except ProcessLookupError:
        console.print("[yellow]Daemon process not found (may have already stopped)[/yellow]")
        pid_file = config.data_dir / "daemon.pid"
        if pid_file.exists():
            pid_file.unlink()
    except PermissionError:
        console.print(f"[red]Permission denied. Cannot stop daemon (PID: {pid})[/red]")


@cli.command()
def daemon_status():
    """Show daemon status."""

    daemon = ActivityDaemon()
    status = daemon.get_status()

    if status["running"]:
        console.print(f"[green]Daemon is running[/green]")
        console.print(f"PID: {status['pid']}")

        if "start_time" in status:
            start_time = datetime.fromisoformat(status["start_time"])
            console.print(f"Started: {start_time.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        if "last_collection" in status:
            last_collection = datetime.fromisoformat(status["last_collection"])
            console.print(f"Last collection: {last_collection.strftime('%Y-%m-%d %H:%M:%S UTC')}")

        log_file = config.data_dir / "daemon.log"
        if log_file.exists():
            console.print("\n[bold]Recent log entries:[/bold]")
            try:
                with open(log_file, 'r') as f:
                    lines = f.readlines()[-10:]
                    for line in lines:
                        console.print(line.rstrip())
            except Exception as e:
                console.print(f"[red]Error reading log: {e}[/red]")
    else:
        console.print("[yellow]Daemon is not running[/yellow]")
        if "error" in status:
            console.print(f"[dim]Error: {status['error']}[/dim]")


@cli.command()
@click.option("--date", "-d", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Date to cluster activities for (default: today)")
@click.option("--method", "-m", type=click.Choice(["dbscan", "hdbscan", "kmeans"]),
              default="hdbscan", help="Clustering method")
@click.option("--eps", type=float, default=0.3,
              help="DBSCAN eps parameter (max distance for clustering)")
@click.option("--min-samples", type=int, default=2,
              help="Minimum samples per cluster")
@click.option("--min-cluster-size", type=int, default=3,
              help="HDBSCAN minimum cluster size")
def cluster(date: Optional[datetime], method: str, eps: float, min_samples: int, min_cluster_size: int):
    """Cluster activities by similarity for a specific date."""

    target_date = date.date() if date else datetime.now().date()

    try:
        vector_store = _get_vector_store()
        clusterer = ActivityClusterer(vector_store)

        console.print(f"[blue]Clustering activities for {target_date}...[/blue]")

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Analyzing activity patterns...", total=None)

            results = clusterer.cluster_activities_by_similarity(
                target_date=target_date,
                method=method,
                eps=eps,
                min_samples=min_samples,
                min_cluster_size=min_cluster_size
            )

        if not results["clusters"]:
            console.print(f"[yellow]{results.get('message', 'No clusters found')}[/yellow]")
            return

        console.print(f"\n[green]Found {results['num_clusters']} clusters from {results['total_events']} events[/green]")

        for cluster in results["clusters"]:
            cluster_info = f"**Size:** {cluster['size']} activities\n"
            cluster_info += f"**Dominant Category:** {cluster['dominant_category']}\n"
            cluster_info += f"**Average Similarity:** {cluster['avg_similarity']:.2f}\n"
            cluster_info += f"**Description:** {cluster['description']}\n\n"

            cluster_info += "**Sample Activities:**\n"
            for i, event in enumerate(cluster["events"][:3]):
                summary = event.get("summary", "No summary")[:80] + "..."
                cluster_info += f"- {summary}\n"

            if len(cluster["events"]) > 3:
                cluster_info += f"- ... and {len(cluster['events']) - 3} more\n"

            console.print(Panel(
                cluster_info,
                title=f"Cluster: {cluster['name']}",
                border_style="blue" if cluster["cluster_id"] != -1 else "dim"
            ))

        if results.get("num_noise", 0) > 0:
            console.print(f"\n[dim]{results['num_noise']} activities didn't fit into clear patterns[/dim]")

    except Exception as e:
        console.print(f"[red]Error clustering activities: {e}[/red]")


@cli.command()
@click.option("--start-date", "-s", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Start date for pattern analysis")
@click.option("--end-date", "-e", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="End date for pattern analysis")
@click.option("--days", "-n", type=int, default=7,
              help="Number of days to analyze (default: 7)")
def patterns(start_date: Optional[datetime], end_date: Optional[datetime], days: int):
    """Find recurring activity patterns across multiple days."""

    if not start_date:
        end_date = datetime.now().date()
        start_date = end_date - timedelta(days=days-1)
    else:
        start_date = start_date.date()
        if not end_date:
            end_date = start_date + timedelta(days=days-1)
        else:
            end_date = end_date.date()

    try:
        vector_store = _get_vector_store()
        clusterer = ActivityClusterer(vector_store)

        console.print(f"[blue]Analyzing activity patterns from {start_date} to {end_date}...[/blue]")

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Finding recurring patterns...", total=None)

            results = clusterer.find_activity_patterns(
                start_date=start_date,
                end_date=end_date
            )

        found_patterns = results["recurring_patterns"]

        if not found_patterns:
            console.print("[yellow]No recurring patterns found[/yellow]")
            return

        console.print(f"\n[green]Found {len(found_patterns)} recurring patterns[/green]")

        table = Table(title="Recurring Activity Patterns")
        table.add_column("Pattern", style="cyan")
        table.add_column("Frequency", justify="right", style="magenta")
        table.add_column("Category", style="green")
        table.add_column("Description", style="white")

        for pattern in found_patterns:
            table.add_row(
                pattern["pattern_id"],
                str(pattern["frequency"]),
                pattern["dominant_category"],
                pattern["description"]
            )

        console.print(table)

        for pattern in found_patterns[:3]:
            pattern_info = f"**Frequency:** {pattern['frequency']} occurrences\n"
            pattern_info += f"**Average cluster size:** {pattern['avg_cluster_size']:.1f} activities\n"
            pattern_info += f"**Example names:**\n"
            for name in pattern["example_names"]:
                pattern_info += f"- {name}\n"

            console.print(Panel(
                pattern_info,
                title=f"Pattern: {pattern['dominant_category']} Activities",
                border_style="green"
            ))

    except Exception as e:
        console.print(f"[red]Error analyzing patterns: {e}[/red]")


@cli.command()
@click.option("--days", "-n", type=int, default=7,
              help="Number of recent days to analyze (default: 7)")
@click.option("--sample-size", type=int, default=50,
              help="Maximum number of events to analyze (default: 50)")
def categorization_analysis(days: int, sample_size: int):
    """Analyze the performance of RAG vs AI categorization."""

    try:
        vector_store = _get_vector_store()
        preprocessor = _get_preprocessor()
        enhanced_categorizer = EnhancedCategorizer(vector_store, preprocessor)

        console.print(f"[blue]Analyzing categorization performance over the last {days} days...[/blue]")

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Comparing categorization methods...", total=None)

            results = enhanced_categorizer.analyze_categorization_performance(num_days=days)

        if "error" in results:
            console.print(f"[red]{results['error']}[/red]")
            console.print(f"[dim]{results.get('recommendation', '')}[/dim]")
            return

        analysis = results

        console.print(f"\n[green]Categorization Analysis Results[/green]")
        console.print(f"Sample size: {analysis['sample_size']} events")
        console.print(f"Period: {analysis['analysis_period']['start_date']} to {analysis['analysis_period']['end_date']}")

        agreement = analysis["rag_vs_ai_agreement"]
        console.print(f"\n**RAG vs AI Agreement:** {agreement['agreement_rate']:.1%}")
        console.print(f"- Agreements: {agreement['total_agreements']}")
        console.print(f"- Disagreements: {agreement['disagreements']}")

        rag_perf = analysis["rag_performance"]
        console.print(f"\n**RAG Performance:**")
        console.print(f"- Average confidence: {rag_perf['avg_confidence']:.2f}")
        console.print(f"- Method distribution: {rag_perf['method_distribution']}")

        if rag_perf["accuracy_vs_original"]:
            console.print(f"- Accuracy vs original: {rag_perf['accuracy_vs_original']:.1%}")

        if analysis["recommendations"]:
            console.print(f"\n[yellow]Recommendations:[/yellow]")
            for rec in analysis["recommendations"]:
                console.print(f"- {rec}")

    except Exception as e:
        console.print(f"[red]Error analyzing categorization: {e}[/red]")


@cli.command()
@click.option("--use-rag", is_flag=True, default=False,
              help="Use RAG-based categorization for new events")
@click.option("--confidence-threshold", type=float, default=0.7,
              help="Confidence threshold for RAG categorization")
def collect_enhanced(use_rag: bool, confidence_threshold: float):
    """Collect activities with enhanced categorization (RAG-based)."""

    target_date = datetime.now().date()

    try:
        vector_store = _get_vector_store()
        preprocessor = _get_preprocessor()
        enhanced_categorizer = EnhancedCategorizer(vector_store, preprocessor) if use_rag else None

        git_collector = GitCollector()
        browser_collector = BrowserCollector()
        terminal_collector = TerminalCollector()

        all_events = []

        console.print(f"[blue]Collecting activities for {target_date} with enhanced categorization...[/blue]")

        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Collecting git activities...", total=None)
            since = datetime.combine(target_date, datetime.min.time()).replace(tzinfo=timezone.utc)
            all_events.extend(git_collector.collect_commits(since))
            all_events.extend(git_collector.collect_stashes(since))

            progress.update(task, description="Collecting browser history...")
            all_events.extend(browser_collector.collect_all(since))

            progress.update(task, description="Collecting terminal history...")
            all_events.extend(terminal_collector.collect_all(since))

            today_events = [e for e in all_events if e.timestamp.date() == target_date]

            if not today_events:
                console.print("[yellow]No activities found for today[/yellow]")
                return

            progress.update(task, description=f"Processing {len(today_events)} events with enhanced categorization...")

            processed_events = preprocessor.process_events_with_rag(
                today_events,
                enhanced_categorizer
            )

            progress.update(task, description="Storing events...")
            stored_count = vector_store.store_events(processed_events)

        console.print(f"\n[green]Successfully processed and stored {stored_count} events[/green]")

        if use_rag:
            method_counts = {}
            for event in processed_events:
                if hasattr(event, 'metadata') and event.metadata and 'categorization' in event.metadata:
                    method = event.metadata['categorization'].get('method', 'unknown')
                    method_counts[method] = method_counts.get(method, 0) + 1

            if method_counts:
                console.print(f"\n**Categorization Methods Used:**")
                for method, count in method_counts.items():
                    console.print(f"- {method}: {count} events")

        category_counts = {}
        for event in processed_events:
            if event.category:
                category_counts[event.category.value] = category_counts.get(event.category.value, 0) + 1

        if category_counts:
            console.print(f"\n**Category Distribution:**")
            for category, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True):
                console.print(f"- {category}: {count} events")

    except Exception as e:
        console.print(f"[red]Error in enhanced collection: {e}[/red]")


@cli.command()
@click.option('-d', '--date', type=click.DateTime(formats=['%Y-%m-%d']),
              default=datetime.now().strftime('%Y-%m-%d'),
              help='Date to list tasks for (default: today)')
@click.option('--status', type=click.Choice(['active', 'completed', 'paused', 'abandoned']),
              help='Filter by task status')
@click.option('--category', type=click.Choice([c.value for c in Category]),
              help='Filter by category')
@click.option('--project', help='Filter by project')
def tasks(date, status, category, project):
    """List tasks for a specific date with optional filters."""
    from patcha.db.tasks import TaskStore
    from patcha.db.models import TaskStatus

    task_store = TaskStore()
    target_date = date.date()

    tasks_list = task_store.get_tasks_by_date(target_date)

    if status:
        tasks_list = [t for t in tasks_list if t.status.value == status]
    if category:
        tasks_list = [t for t in tasks_list if t.category and t.category.value == category]
    if project:
        tasks_list = [t for t in tasks_list if t.project == project]

    if not tasks_list:
        console.print(f"[yellow]No tasks found for {target_date}[/yellow]")
        return

    table = Table(title=f"Tasks for {target_date}")
    table.add_column("Title", style="cyan", no_wrap=True, min_width=20)
    table.add_column("Status", style="green")
    table.add_column("Category", style="blue")
    table.add_column("Duration", style="magenta")
    table.add_column("Activities", style="yellow")
    table.add_column("Priority", style="red")

    for task in sorted(tasks_list, key=lambda x: x.start_time):
        status_color = {
            "active": "green",
            "completed": "blue",
            "paused": "yellow",
            "abandoned": "red"
        }.get(task.status.value, "white")

        table.add_row(
            task.title[:30] + "..." if len(task.title) > 30 else task.title,
            f"[{status_color}]{task.status.value}[/{status_color}]",
            task.category.value if task.category else "None",
            f"{task.duration_minutes or 0}m",
            str(task.activity_count),
            task.priority.value
        )

    console.print(table)


@cli.command('task-details')
@click.argument('task_id')
def task_details(task_id):
    """Show detailed information about a specific task."""
    from patcha.db.tasks import TaskStore
    from patcha.db.retrieval.search import TaskAwareSearchService

    task_store = TaskStore()
    task = task_store.get_task(task_id)

    if not task:
        console.print(f"[red]Task not found: {task_id}[/red]")
        return

    console.print(Panel(f"[bold cyan]{task.title}[/bold cyan]"))

    details_table = Table(show_header=False, box=None)
    details_table.add_column("Field", style="bold")
    details_table.add_column("Value")

    details_table.add_row("ID", task.id)
    details_table.add_row("Status", f"[{task.status.value}]{task.status.value.upper()}[/{task.status.value}]")
    details_table.add_row("Priority", task.priority.value)
    details_table.add_row("Category", task.category.value if task.category else "None")
    details_table.add_row("Project", task.project or "None")
    details_table.add_row("Start Time", task.start_time.strftime("%Y-%m-%d %H:%M:%S"))
    details_table.add_row("End Time", task.end_time.strftime("%Y-%m-%d %H:%M:%S") if task.end_time else "Ongoing")
    details_table.add_row("Duration", f"{task.duration_minutes or 0} minutes")
    details_table.add_row("Activities", str(task.activity_count))
    details_table.add_row("Confidence", f"{task.confidence_score:.2f}")
    details_table.add_row("Tags", ", ".join(task.tags) if task.tags else "None")

    console.print(details_table)

    if task.description:
        console.print(f"\n[bold]Description:[/bold]\n{task.description}")

    if task.activities:
        console.print(f"\n[bold]Activities ({len(task.activities)}):[/bold]")
        for i, activity_id in enumerate(task.activities[:5], 1):
            console.print(f"  {i}. {activity_id}")
        if len(task.activities) > 5:
            console.print(f"  ... and {len(task.activities) - 5} more")


@cli.command('task-summary')
@click.option('-d', '--date', type=click.DateTime(formats=['%Y-%m-%d']),
              default=datetime.now().strftime('%Y-%m-%d'),
              help='Date to summarize tasks for (default: today)')
@click.option('-s', '--save', is_flag=True, help='Save summary to file')
def task_summary(date, save):
    """Generate a task-focused daily summary with rich narrative."""
    from patcha.db.tasks import TaskStore
    from patcha.summary import TaskSummarizer

    task_store = TaskStore()
    task_summarizer = TaskSummarizer(task_store)
    target_date = date.date()

    with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
        task = progress.add_task("Generating task summary...", total=None)

        rich_summary = task_summarizer.generate_rich_daily_summary(target_date, save_to_file=save, use_ai=True)

    if rich_summary["total_tasks"] == 0:
        console.print(f"[yellow]No tasks found for {target_date}[/yellow]")
        return

    console.print(Panel(f"[bold cyan]Daily Task Summary - {target_date.strftime('%B %d, %Y')}[/bold cyan]"))

    console.print(f"\n[bold]Overview:[/bold]")
    console.print(rich_summary["overview"])

    total_hours = rich_summary["total_time_minutes"] / 60
    console.print(f"\n[bold]Statistics:[/bold]")
    console.print(f"Total Tasks: {rich_summary['total_tasks']}")
    console.print(f"Projects: {len(rich_summary['projects'])}")
    console.print(f"Time Spent: {total_hours:.1f} hours")

    if rich_summary["tasks"]:
        tasks_table = Table(title="Individual Task Breakdown")
        tasks_table.add_column("Task Title", style="bold", width=25)
        tasks_table.add_column("Category", style="blue", width=10)
        tasks_table.add_column("Time", style="cyan", width=8)
        tasks_table.add_column("Activities", style="magenta", width=10)
        tasks_table.add_column("Summary", style="dim", width=45)

        for task_data in rich_summary["tasks"][:10]:
            title = task_data["title"]
            if len(title) > 23:
                title = title[:20] + "..."

            summary = task_data["summary"]
            if len(summary) > 80:
                summary = summary[:77] + "..."

            category = task_data["category"][:8] if task_data["category"] else "Other"

            tasks_table.add_row(
                title,
                category,
                f"{task_data['duration_minutes']}m",
                str(task_data["activity_count"]),
                summary
            )

        console.print(tasks_table)

    if rich_summary["projects"]:
        console.print(f"\n[bold]Projects Worked On:[/bold]")
        for project in rich_summary["projects"]:
            console.print(f"- {project}")


@cli.command('search-tasks')
@click.argument('query')
@click.option('-l', '--limit', default=5, help='Number of results to return')
@click.option('--category', type=click.Choice([c.value for c in Category]),
              help='Filter by category')
@click.option('--project', help='Filter by project')
@click.option('--type', 'search_type', type=click.Choice(['tasks', 'activities', 'both']),
              default='both', help='What to search')
def search_tasks(query, limit, category, project, search_type):
    """Search tasks and activities using semantic search."""
    from patcha.db.retrieval.search import TaskAwareSearchService
    from patcha.db.tasks import TaskStore
    from patcha.db.models import Category as CategoryEnum

    task_store = TaskStore()
    vector_store = _get_vector_store()
    preprocessor = _get_preprocessor()
    search_service = TaskAwareSearchService(task_store, vector_store, preprocessor)

    category_filter = None
    if category:
        category_filter = CategoryEnum(category)

    with console.status("[bold green]Searching..."):
        results = search_service.search(
            query=query,
            search_type=search_type,
            limit=limit,
            category_filter=category_filter,
            project_filter=project
        )

    if results["total_results"] == 0:
        console.print(f"[yellow]No results found for: {query}[/yellow]")
        return

    console.print(f"Found {results['total_results']} results for: [cyan]{query}[/cyan]\n")

    if search_type == "both" and results["combined_results"]:
        for i, result in enumerate(results["combined_results"], 1):
            _display_search_result(result, i)
    else:
        if results["task_results"]:
            console.print("[bold blue]Task Results:[/bold blue]")
            for i, result in enumerate(results["task_results"], 1):
                _display_search_result(result, i)

        if results["activity_results"]:
            if results["task_results"]:
                console.print()
            console.print("[bold green]Activity Results:[/bold green]")
            for i, result in enumerate(results["activity_results"], 1):
                _display_search_result(result, i)


def _display_search_result(result, index):
    result_type = result["type"].upper()
    score = result["score"]

    if result["type"] == "task":
        title = result["title"]
        category = result.get("category", "None")
        status = result["status"]
        console.print(f"{index}. [{result_type}] {title}")
        console.print(f"   Score: {score:.3f} | Status: {status} | Category: {category}")
        if result.get("description"):
            console.print(f"   {result['description'][:100]}...")
    else:
        summary = result.get("summary", "No summary")
        category = result.get("category", "None")
        timestamp = result.get("timestamp", "")
        console.print(f"{index}. [{result_type}] {summary[:60]}...")
        console.print(f"   Score: {score:.3f} | Category: {category} | Time: {timestamp}")

    console.print()


@cli.command('complete-task')
@click.argument('task_id')
def complete_task(task_id):
    """Mark a task as completed."""
    from patcha.db.tasks import TaskStore

    task_store = TaskStore()
    task = task_store.get_task(task_id)

    if not task:
        console.print(f"[red]Task not found: {task_id}[/red]")
        return

    task.complete_task()

    if task_store.update_task(task):
        console.print(f"[green]Task completed: {task.title}[/green]")
        console.print(f"Duration: {task.duration_minutes} minutes")
    else:
        console.print(f"[red]Error updating task[/red]")


@cli.command('rag-summary')
@click.option('-d', '--date', type=click.DateTime(formats=['%Y-%m-%d']),
              default=datetime.now().strftime('%Y-%m-%d'),
              help='Date to generate RAG-enhanced summary for (default: today)')
@click.option('--save', is_flag=True, help='Save summary to file')
def rag_enhanced_summary(date, save):
    """Generate RAG-enhanced daily summary with historical context and insights."""
    from patcha.summary import RAGTaskSummarizer
    from patcha.db.tasks import TaskStore

    target_date = date.date()

    task_store = TaskStore()
    vector_store = _get_vector_store()
    rag_summarizer = RAGTaskSummarizer(task_store, vector_store)

    console.print(f"Generating RAG-enhanced summary for {target_date}...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Analyzing with historical context...")

        summary = rag_summarizer.generate_rich_daily_summary_with_rag(
            target_date, save_to_file=save, use_ai=True
        )

        progress.update(task, description="Complete!")

    if summary.get("rag_enhanced"):
        console.print(Panel("[bold green]RAG-Enhanced Daily Summary[/bold green]"))

        if summary.get("contextual_overview"):
            console.print(f"\n[bold]Contextual Overview:[/bold]")
            console.print(summary["contextual_overview"])

        if summary.get("productivity_patterns"):
            patterns = summary["productivity_patterns"]
            console.print(f"\n[bold]Productivity Insights:[/bold]")
            console.print(f"Focus Score: {patterns.get('focus_score', 0):.1f}/1.0")
            if patterns.get("productivity_indicators"):
                for indicator in patterns["productivity_indicators"]:
                    console.print(f"- {indicator}")

    else:
        console.print(Panel(f"[bold cyan]Daily Summary - {target_date.strftime('%B %d, %Y')}[/bold cyan]"))

        if summary.get("overview"):
            console.print(f"\n[bold]Overview:[/bold]")
            console.print(summary["overview"])

    if summary.get("tasks"):
        console.print(f"\n[dim]Generated from {len(summary['tasks'])} tasks[/dim]")


@cli.command('project-analysis')
@click.option('-p', '--project', required=True, help='Project name to analyze')
@click.option('--days', default=30, help='Number of days to look back (default: 30)')
def rag_project_analysis(project, days):
    """Generate RAG-enhanced project analysis with progression insights."""
    from patcha.summary import RAGTaskSummarizer
    from patcha.db.tasks import TaskStore

    task_store = TaskStore()
    vector_store = _get_vector_store()
    rag_summarizer = RAGTaskSummarizer(task_store, vector_store)

    console.print(f"Analyzing project '{project}' over the last {days} days...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Gathering project data...")

        analysis = rag_summarizer.generate_contextual_project_summary(project, days)

        progress.update(task, description="Complete!")

    if "error" in analysis:
        console.print(f"[red]{analysis['error']}[/red]")
        return

    console.print(Panel(f"[bold cyan]Project Analysis: {project}[/bold cyan]"))

    console.print(f"\n[bold]Project Overview:[/bold]")
    console.print(f"Timeframe: {analysis['timeframe']}")
    console.print(f"Total Activities: {analysis['total_activities']}")
    console.print(f"Active Days: {analysis['active_days']}")
    console.print(f"Date Range: {analysis['date_range']}")

    if analysis.get("patterns"):
        console.print(f"\n[bold]Common Patterns:[/bold]")
        for pattern in analysis["patterns"]:
            console.print(f"- {pattern}")

    if analysis.get("productivity_trends"):
        trends = analysis["productivity_trends"]
        console.print(f"\n[bold]Productivity Trends:[/bold]")
        console.print(f"Activities per day: {trends.get('activities_per_day', 0)}")
        console.print(f"Intensity: {trends.get('intensity', 'Unknown')}")

        if trends.get("category_distribution"):
            console.print(f"Category distribution:")
            for cat, pct in trends["category_distribution"].items():
                console.print(f"  - {cat}: {pct}%")

    if analysis.get("recommendations"):
        console.print(f"\n[bold]Recommendations:[/bold]")
        for rec in analysis["recommendations"]:
            console.print(f"- {rec}")


@cli.command('contextual-search')
@click.argument('query')
@click.option('-d', '--date', type=click.DateTime(formats=['%Y-%m-%d']), help='Filter by specific date')
@click.option('-l', '--limit', default=10, help='Maximum number of results (default: 10)')
def contextual_search(query, date, limit):
    """Perform RAG-enhanced contextual search with insights."""
    from patcha.summary import RAGTaskSummarizer
    from patcha.db.tasks import TaskStore

    task_store = TaskStore()
    vector_store = _get_vector_store()
    rag_summarizer = RAGTaskSummarizer(task_store, vector_store)

    target_date = date.date() if date else None

    console.print(f"Searching: [bold]'{query}'[/bold]")
    if target_date:
        console.print(f"Date filter: {target_date}")

    results = rag_summarizer.contextual_search_with_insights(
        query, date_filter=target_date, limit=limit
    )

    if not results.get("results"):
        console.print("[yellow]No results found[/yellow]")
        return

    console.print(f"\n[bold]Found {results['result_count']} results:[/bold]")

    for i, result in enumerate(results["results"], 1):
        payload = result["payload"]
        timestamp = datetime.fromisoformat(payload["timestamp"])

        console.print(f"\n[bold]{i}. {payload['type']} - {timestamp.strftime('%Y-%m-%d %H:%M')}[/bold]")

        if payload.get("project"):
            console.print(f"   Project: {payload['project']}")
        if payload.get("category"):
            console.print(f"   Category: {payload['category']}")

        content = payload.get("summary") or payload.get("raw_content", "")[:200]
        if content:
            console.print(f"   {content}")

    if results.get("contextual_insights"):
        insights = results["contextual_insights"]
        console.print(f"\n[bold]Contextual Insights:[/bold]")

        if insights.get("patterns_found"):
            console.print("Patterns found:")
            for pattern in insights["patterns_found"]:
                console.print(f"  - {pattern}")

        if insights.get("related_projects"):
            console.print("Related projects:")
            for project in insights["related_projects"]:
                console.print(f"  - {project}")


@cli.command('identify-tasks')
@click.option('-d', '--date', type=click.DateTime(formats=['%Y-%m-%d']),
              default=datetime.now().strftime('%Y-%m-%d'),
              help='Date to identify tasks for (default: today)')
@click.option('--batch-size', default=50, help='Batch size for processing')
@click.option('--no-ai', is_flag=True, help='Skip AI enhancement to avoid timeouts')
@click.option('--similarity', default=0.91, help='Similarity threshold for clustering (default: 0.91)')
@click.option('--workers', default=6, help='Number of parallel workers for LLM analysis (default: 6)')
@click.option('--max-activities', default=30, help='Maximum activities per task (default: 30)')
@click.option('--use-rag', is_flag=True, help='Use RAG for enhanced contextual analysis')
def identify_tasks(date, batch_size, no_ai, similarity, workers, max_activities, use_rag):
    """Identify tasks from existing activities for a specific date."""
    from patcha.compaction import TaskIdentifier
    from patcha.db.tasks import TaskStore
    from patcha.db.models import Event, EventType

    target_date = date.date()

    vector_store = _get_vector_store()
    preprocessor = _get_preprocessor()

    task_identifier = TaskIdentifier(
        preprocessor,
        vector_store=vector_store if use_rag else None
    )
    task_store = TaskStore()

    console.print(f"Identifying tasks for {target_date}...")

    with Progress(
        SpinnerColumn(),
        TextColumn("[progress.description]{task.description}"),
        console=console
    ) as progress:
        task = progress.add_task("Loading activities...")

        activities_data = vector_store.get_events_by_date(target_date)

        if not activities_data:
            console.print(f"[yellow]No activities found for {target_date}[/yellow]")
            return

        progress.update(task, description=f"Processing {len(activities_data)} activities...")

        events = []
        for activity_data in activities_data:
            try:
                payload = activity_data["payload"]
                event = Event(
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
                print(f"Error converting activity: {e}")
                continue

        progress.update(task, description="Identifying task patterns...")

        identified_tasks = task_identifier.identify_tasks_from_activities(
            events,
            similarity_threshold=similarity,
            use_ai_enhancement=not no_ai,
            max_workers=workers,
            max_activities_per_task=max_activities
        )

        progress.update(task, description="Storing identified tasks...")

        stored_count = 0
        for task_obj in identified_tasks:
            if task_store.store_task(task_obj):
                stored_count += 1

    console.print(f"\n[green]Successfully identified and stored {stored_count} tasks from {len(events)} activities[/green]")

    if identified_tasks:
        console.print("\n[bold]Identified Tasks:[/bold]")
        for i, task_obj in enumerate(identified_tasks, 1):
            console.print(f"{i}. {task_obj.title} ({task_obj.activity_count} activities, {task_obj.duration_minutes}m)")


@cli.command('migrate-to-tasks')
@click.option('--start-date', type=click.DateTime(formats=['%Y-%m-%d']),
              required=True, help='Start date for migration')
@click.option('--end-date', type=click.DateTime(formats=['%Y-%m-%d']),
              required=True, help='End date for migration')
@click.option('--batch-size', default=50, help='Batch size for processing')
@click.option('--dry-run', is_flag=True, help='Analyze migration without storing data')
def migrate_to_tasks(start_date, end_date, batch_size, dry_run):
    """Migrate existing activities to task-first architecture."""
    from patcha.db.migrations.migrate import DataMigration

    migration = DataMigration()

    start = start_date.date()
    end = end_date.date()

    if not dry_run:
        console.print(f"[bold red]This will migrate activities from {start} to {end} to task format.[/bold red]")
        console.print("[yellow]This process may take a while and will create new task records.[/yellow]")

        if not click.confirm("Do you want to continue?"):
            console.print("Migration cancelled.")
            return

    report = migration.migrate_date_range(start, end, batch_size, dry_run)

    if click.confirm("Save migration report to file?"):
        import json
        report_file = config.data_dir / f"migration_report_{start}_{end}.json"
        with open(report_file, 'w') as f:
            json.dump(report, f, indent=2, default=str)
        console.print(f"[green]Report saved to {report_file}[/green]")


@cli.command('analyze-migration')
@click.option('--start-date', type=click.DateTime(formats=['%Y-%m-%d']),
              required=True, help='Start date for analysis')
@click.option('--end-date', type=click.DateTime(formats=['%Y-%m-%d']),
              required=True, help='End date for analysis')
def analyze_migration(start_date, end_date):
    """Analyze migration feasibility for a date range."""
    from patcha.db.migrations.migrate import DataMigration

    migration = DataMigration()

    start = start_date.date()
    end = end_date.date()

    analysis = migration.analyze_migration_feasibility(start, end)

    if click.confirm("Save analysis report to file?"):
        import json
        report_file = config.data_dir / f"migration_analysis_{start}_{end}.json"
        with open(report_file, 'w') as f:
            json.dump(analysis, f, indent=2, default=str)
        console.print(f"[green]Analysis saved to {report_file}[/green]")


@cli.command('cleanup-duplicate-tasks')
@click.option('-d', '--date', type=click.DateTime(formats=['%Y-%m-%d']),
              help='Date to clean up (default: last 30 days)')
def cleanup_duplicate_tasks(date):
    """Clean up duplicate tasks created during migration."""
    from patcha.db.migrations.migrate import DataMigration

    migration = DataMigration()
    target_date = date.date() if date else None

    if target_date:
        console.print(f"[yellow]This will analyze and remove duplicate tasks for {target_date}[/yellow]")
    else:
        console.print("[yellow]This will analyze and remove duplicate tasks from the last 30 days[/yellow]")

    if not click.confirm("Do you want to continue?"):
        console.print("Cleanup cancelled.")
        return

    report = migration.cleanup_duplicate_tasks(target_date)

    console.print(f"\n[bold]Cleanup Summary:[/bold]")
    console.print(f"Tasks analyzed: {report['analyzed_tasks']}")
    console.print(f"Duplicate pairs found: {report['duplicates_found']}")
    console.print(f"Duplicate tasks removed: {report['duplicates_removed']}")

    if report['duplicate_pairs']:
        console.print(f"\n[bold]Sample duplicate pairs removed:[/bold]")
        for i, pair in enumerate(report['duplicate_pairs'][:5], 1):
            console.print(f"{i}. '{pair['task1']['title']}' vs '{pair['task2']['title']}' (similarity: {pair['similarity']:.2f})")


@cli.command('task-stats')
@click.option('--days', default=7, help='Number of days to analyze')
def task_stats(days):
    """Show task-based productivity statistics."""
    from patcha.summary import TaskSummarizer
    from patcha.db.tasks import TaskStore

    task_store = TaskStore()
    summarizer = TaskSummarizer(task_store)

    console.print(f"[bold cyan]Task Productivity Analysis - Last {days} days[/bold cyan]")

    trends = summarizer.get_productivity_trends(days)

    if "error" in trends:
        console.print(f"[red]{trends['error']}[/red]")
        return

    period = trends["period"]
    averages = trends["averages"]

    console.print(f"\n[bold]Period:[/bold] {period['start_date']} to {period['end_date']}")
    console.print(f"[bold]Total Tasks:[/bold] {trends['total_tasks']}")
    console.print(f"[bold]Total Completed:[/bold] {trends['total_completed']} ({trends['total_completed']/trends['total_tasks']*100:.1f}%)")

    console.print(f"\n[bold]Daily Averages:[/bold]")
    console.print(f"Tasks per day: {averages['daily_tasks']:.1f}")
    console.print(f"Completion rate: {averages['completion_rate']:.1%}")
    console.print(f"Time per day: {averages['daily_time_minutes']:.1f} minutes ({averages['daily_time_minutes']/60:.1f} hours)")

    if trends.get('most_productive_day'):
        console.print(f"Most productive day: {trends['most_productive_day']}")

    recent_completion_rates = trends['trends']['completion_rates'][-7:]
    recent_task_counts = trends['trends']['daily_task_counts'][-7:]

    console.print(f"\n[bold]Recent Trends (Last 7 days):[/bold]")
    console.print(f"Average completion rate: {sum(recent_completion_rates)/len(recent_completion_rates):.1%}")
    console.print(f"Average daily tasks: {sum(recent_task_counts)/len(recent_task_counts):.1f}")

    if len(recent_completion_rates) >= 4:
        early_avg = sum(recent_completion_rates[:3]) / 3
        late_avg = sum(recent_completion_rates[-3:]) / 3
        if late_avg > early_avg + 0.1:
            console.print("[green]Completion rate trending up[/green]")
        elif late_avg < early_avg - 0.1:
            console.print("[red]Completion rate trending down[/red]")
        else:
            console.print("[yellow]Completion rate stable[/yellow]")


@cli.command()
@click.option("--date", "-d", type=click.DateTime(formats=["%Y-%m-%d"]),
              default=None, help="Date to analyze (default: today)")
@click.option("--use-graph", "-g", is_flag=True,
              help="Use graph RAG for enhanced context")
def analyze_graph(date: Optional[datetime], use_graph: bool):
    """Analyze activities using graph RAG for enhanced insights."""
    target_date = date.date() if date else datetime.now().date()

    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Analyzing activities with graph RAG...", total=None)

            vector_store = _get_vector_store()
            preprocessor = _get_preprocessor()
            knowledge_graph = KnowledgeGraph()
            graph_rag = GraphRAGSystem(vector_store, preprocessor, knowledge_graph)

            activities_data = vector_store.get_events_by_date(target_date)

            if not activities_data:
                console.print(f"[yellow]No activities found for {target_date}[/yellow]")
                return

            progress.update(task, description="Converting activities to Event objects...")

            from patcha.db.models import Event, EventType
            activities = []
            for activity_data in activities_data:
                try:
                    payload = activity_data.get('payload', {})
                    event = Event(
                        type=EventType(payload.get('type', 'browser')),
                        timestamp=datetime.fromisoformat(payload.get('timestamp', datetime.now().isoformat())),
                        source=payload.get('source', 'unknown'),
                        raw_content=payload.get('content', ''),
                        summary=payload.get('summary', ''),
                        project=payload.get('project'),
                        metadata=payload
                    )
                    if 'vector' in activity_data:
                        event.embedding = activity_data['vector']
                    activities.append(event)
                except Exception as e:
                    print(f"Error converting activity data: {e}")
                    activities.append(activity_data['payload'])

            progress.update(task, description="Extracting entities and relationships...")

            enhanced_activities = graph_rag.enhance_activity_processing(activities)

            progress.update(task, description="Generating enhanced summary...")

            if use_graph:
                summary = graph_rag.generate_enhanced_summary_with_graph(enhanced_activities, target_date)
            else:
                summaries = [activity.summary or "No summary" for activity in enhanced_activities]
                summary = f"Completed {len(enhanced_activities)} activities: {'; '.join(summaries[:5])}"

            progress.update(task, description="Getting knowledge graph insights...")

            insights = graph_rag.get_knowledge_graph_insights()

        console.print(f"\n[bold]Activity Analysis for {target_date}[/bold]")
        console.print("=" * 50)

        console.print(f"\n[bold cyan]Enhanced Summary:[/bold cyan]")
        console.print(Panel(summary, expand=False))

        stats = insights["stats"]
        console.print(f"\n[bold cyan]Knowledge Graph Statistics:[/bold cyan]")

        stats_table = Table(show_header=True, header_style="bold magenta")
        stats_table.add_column("Metric", style="cyan")
        stats_table.add_column("Count", style="green")

        stats_table.add_row("Total Entities", str(stats["total_entities"]))
        stats_table.add_row("Total Relationships", str(stats["total_relationships"]))

        for entity_type, count in stats["entity_types"].items():
            stats_table.add_row(f"  {entity_type.title()} Entities", str(count))

        console.print(stats_table)

        if insights["most_connected_entities"]:
            console.print(f"\n[bold cyan]Most Connected Entities:[/bold cyan]")

            entities_table = Table(show_header=True, header_style="bold magenta")
            entities_table.add_column("Entity", style="cyan")
            entities_table.add_column("Type", style="yellow")
            entities_table.add_column("Connections", style="green")

            for entity in insights["most_connected_entities"][:5]:
                entities_table.add_row(
                    entity["name"],
                    entity["type"].title(),
                    str(entity["connections"])
                )

            console.print(entities_table)

        if insights["recent_entities"]:
            console.print(f"\n[bold cyan]Recently Mentioned Entities:[/bold cyan]")

            recent_table = Table(show_header=True, header_style="bold magenta")
            recent_table.add_column("Entity", style="cyan")
            recent_table.add_column("Type", style="yellow")
            recent_table.add_column("Mentions", style="green")
            recent_table.add_column("Last Seen", style="blue")

            for entity in insights["recent_entities"][:5]:
                last_seen = datetime.fromisoformat(entity["last_seen"]).strftime("%Y-%m-%d %H:%M")
                recent_table.add_row(
                    entity["name"],
                    entity["type"].title(),
                    str(entity["mentions"]),
                    last_seen
                )

            console.print(recent_table)

    except Exception as e:
        console.print(f"[red]Error analyzing activities: {e}[/red]")


@cli.command()
@click.argument("query")
@click.option("--limit", "-l", default=10, help="Maximum number of results")
@click.option("--date-range", "-r", default=7, help="Number of days to search back")
def search_graph(query: str, limit: int, date_range: int):
    """Search activities using graph RAG for enhanced context."""
    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Searching with graph RAG...", total=None)

            vector_store = _get_vector_store()
            preprocessor = _get_preprocessor()
            knowledge_graph = KnowledgeGraph()
            graph_rag = GraphRAGSystem(vector_store, preprocessor, knowledge_graph)

            progress.update(task, description="Generating query embedding...")

            query_embedding = preprocessor.generate_embedding(query)
            if not query_embedding:
                console.print("[red]Error generating query embedding[/red]")
                return

            progress.update(task, description="Searching vector database...")

            vector_results = vector_store.search_events(query_embedding, limit=limit * 2)

            if not vector_results:
                console.print(f"[yellow]No results found for query: {query}[/yellow]")
                return

            from patcha.db.models import Event, EventType

            query_activities = []
            for result in vector_results[:5]:
                event = Event(
                    type=EventType(result.get("type", "browser")),
                    timestamp=datetime.fromisoformat(result.get("timestamp", datetime.now().isoformat())),
                    source=result.get("source", "unknown"),
                    raw_content=result.get("content", ""),
                    summary=result.get("summary", ""),
                    project=result.get("project"),
                    metadata=result
                )
                if hasattr(result, 'vector'):
                    event.embedding = result.vector
                query_activities.append(event)

            progress.update(task, description="Retrieving graph context...")

            enhanced_context = graph_rag.retrieve_enhanced_context(
                query_activities, context_limit=limit
            )

        console.print(f"\n[bold]Graph RAG Search Results for: '{query}'[/bold]")
        console.print("=" * 60)

        activities = enhanced_context.get("activities", [])
        graph_context = enhanced_context.get("graph_context")

        if activities:
            results_table = Table(show_header=True, header_style="bold magenta")
            results_table.add_column("Date", style="cyan")
            results_table.add_column("Type", style="yellow")
            results_table.add_column("Summary", style="white")
            results_table.add_column("Project", style="green")

            for activity in activities[:limit]:
                timestamp_str = activity.get("timestamp", "Unknown")
                if timestamp_str != "Unknown":
                    try:
                        timestamp = datetime.fromisoformat(timestamp_str)
                        date_str = timestamp.strftime("%Y-%m-%d %H:%M")
                    except Exception:
                        date_str = timestamp_str
                else:
                    date_str = "Unknown"

                results_table.add_row(
                    date_str,
                    activity.get("type", "Unknown"),
                    activity.get("summary", "No summary")[:60] + ("..." if len(activity.get("summary", "")) > 60 else ""),
                    activity.get("project", "None") or "None"
                )

            console.print(results_table)

            console.print(f"\n[bold cyan]Context Information:[/bold cyan]")
            context_info = Table(show_header=False)
            context_info.add_column("Metric", style="cyan")
            context_info.add_column("Value", style="green")

            context_info.add_row("Vector Results", str(len(enhanced_context.get("activities", []))))
            context_info.add_row("Graph Entities", str(len(graph_context.related_entities) if graph_context else 0))
            context_info.add_row("Graph Relationships", str(len(graph_context.relationships) if graph_context else 0))
            context_info.add_row("Combined Score", f"{enhanced_context.get('combined_score', 0.0):.3f}")

            source_breakdown = enhanced_context.get("source_breakdown", {})
            context_info.add_row("Vector Sources", str(source_breakdown.get("vector", 0)))
            context_info.add_row("Graph Sources", str(source_breakdown.get("graph", 0)))

            console.print(context_info)

            if graph_context and graph_context.related_entities:
                console.print(f"\n[bold cyan]Related Entities Found:[/bold cyan]")
                entity_text = ", ".join([e.name for e in graph_context.related_entities[:10]])
                console.print(Panel(entity_text, expand=False))
        else:
            console.print("[yellow]No activities found matching the query.[/yellow]")

    except Exception as e:
        console.print(f"[red]Error searching with graph RAG: {e}[/red]")


@cli.command()
def graph_stats():
    """Display knowledge graph statistics and insights."""
    try:
        with Progress(SpinnerColumn(), TextColumn("[progress.description]{task.description}")) as progress:
            task = progress.add_task("Loading knowledge graph...", total=None)

            knowledge_graph = KnowledgeGraph()

            progress.update(task, description="Calculating statistics...")

            stats = knowledge_graph.get_graph_stats()

        console.print("\n[bold]Knowledge Graph Statistics[/bold]")
        console.print("=" * 40)

        overview_table = Table(show_header=True, header_style="bold magenta")
        overview_table.add_column("Metric", style="cyan")
        overview_table.add_column("Count", style="green")

        overview_table.add_row("Total Entities", str(stats["total_entities"]))
        overview_table.add_row("Total Relationships", str(stats["total_relationships"]))

        console.print(overview_table)

        if stats["entity_types"]:
            console.print(f"\n[bold cyan]Entity Types:[/bold cyan]")

            entity_table = Table(show_header=True, header_style="bold magenta")
            entity_table.add_column("Type", style="cyan")
            entity_table.add_column("Count", style="green")
            entity_table.add_column("Percentage", style="yellow")

            total_entities = stats["total_entities"]
            for entity_type, count in sorted(stats["entity_types"].items(), key=lambda x: x[1], reverse=True):
                percentage = (count / total_entities * 100) if total_entities > 0 else 0
                entity_table.add_row(
                    entity_type.title(),
                    str(count),
                    f"{percentage:.1f}%"
                )

            console.print(entity_table)

        if stats["relationship_types"]:
            console.print(f"\n[bold cyan]Relationship Types:[/bold cyan]")

            rel_table = Table(show_header=True, header_style="bold magenta")
            rel_table.add_column("Type", style="cyan")
            rel_table.add_column("Count", style="green")
            rel_table.add_column("Percentage", style="yellow")

            total_relationships = stats["total_relationships"]
            for rel_type, count in sorted(stats["relationship_types"].items(), key=lambda x: x[1], reverse=True):
                percentage = (count / total_relationships * 100) if total_relationships > 0 else 0
                rel_table.add_row(
                    rel_type.title().replace("_", " "),
                    str(count),
                    f"{percentage:.1f}%"
                )

            console.print(rel_table)

        console.print(f"\n[bold cyan]Graph Health:[/bold cyan]")

        avg_connections = (stats["total_relationships"] * 2) / stats["total_entities"] if stats["total_entities"] > 0 else 0

        health_table = Table(show_header=False)
        health_table.add_column("Metric", style="cyan")
        health_table.add_column("Value", style="green")

        health_table.add_row("Average Connections per Entity", f"{avg_connections:.2f}")

        if avg_connections > 3:
            health_table.add_row("Graph Connectivity", "[green]High[/green]")
        elif avg_connections > 1.5:
            health_table.add_row("Graph Connectivity", "[yellow]Medium[/yellow]")
        else:
            health_table.add_row("Graph Connectivity", "[red]Low[/red]")

        console.print(health_table)

    except Exception as e:
        console.print(f"[red]Error getting graph statistics: {e}[/red]")


if __name__ == "__main__":
    cli()
