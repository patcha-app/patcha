use crate::{
    compaction::DailyCompactor,
    config::Config,
    db::{Db, retrieval::search::TaskAwareSearchService, store::VectorStore, tasks::TaskStore},
    embedding::Embedder,
    llm::client::PatchaApiClient,
    models::TaskStatus,
    summary::TaskSummarizer,
};
use anyhow::Result;
use chrono::{Duration, Local};
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct TasksArgs {
    #[arg(short, long, help = "Date (YYYY-MM-DD, default: today)")]
    pub date: Option<String>,
    #[arg(long, help = "Filter by status (active|completed|paused|abandoned)")]
    pub status: Option<String>,
}

#[derive(Args)]
pub struct TaskDetailsArgs {
    pub task_id: String,
}

#[derive(Args)]
pub struct TaskSummaryArgs {
    #[arg(short, long, help = "Date (YYYY-MM-DD, default: today)")]
    pub date: Option<String>,
}

#[derive(Args)]
pub struct SearchTasksArgs {
    pub query: String,
    #[arg(short, long, default_value = "10")]
    pub limit: usize,
}

#[derive(Args)]
pub struct CompleteTaskArgs {
    pub task_id: String,
}

#[derive(Args)]
pub struct TaskStatsArgs {
    #[arg(long, default_value = "7")]
    pub days: u32,
}

#[derive(Args)]
pub struct IdentifyTasksArgs {
    #[arg(short, long, help = "Date (YYYY-MM-DD, default: yesterday)")]
    pub date: Option<String>,
    #[arg(long)]
    pub dry_run: bool,
}

fn parse_date(s: Option<&str>) -> chrono::NaiveDate {
    s.and_then(|d| chrono::NaiveDate::parse_from_str(d, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive())
}

pub async fn run_list(args: TasksArgs, cfg: Config) -> Result<()> {
    let date = parse_date(args.date.as_deref());
    let db = Db::open(&cfg.db_path)?;
    let task_store = TaskStore::new(db, cfg.data_dir.clone());

    let tasks = if args.status.as_deref() == Some("active") {
        task_store.get_active_tasks()?
    } else {
        task_store.get_tasks_by_date(date)?
    };

    let status_filter = args.status.as_deref();
    let tasks: Vec<_> = tasks
        .iter()
        .filter(|t| {
            status_filter
                .map(|s| format!("{:?}", t.status).to_lowercase() == s)
                .unwrap_or(true)
        })
        .collect();

    if tasks.is_empty() {
        println!("No tasks for {}.", date);
        return Ok(());
    }

    println!("{:<10} {:<50} {:<12} {:>8}  {}", "ID", "Title", "Status", "Duration", "Project");
    println!("{}", "─".repeat(100));
    for t in &tasks {
        let id_short = &t.id[..t.id.len().min(8)];
        let title: String = t.title.chars().take(48).collect();
        let dur = t
            .duration_minutes
            .map(|d| format!("{d:.0}min"))
            .unwrap_or_else(|| "-".into());
        let proj = t.project.as_deref().unwrap_or("-");
        println!(
            "{:<10} {:<50} {:<12} {:>8}  {}",
            id_short, title, format!("{:?}", t.status), dur, proj
        );
    }
    println!("\n{} tasks", tasks.len());
    Ok(())
}

pub async fn run_details(args: TaskDetailsArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let task_store = TaskStore::new(db, cfg.data_dir.clone());

    let task = task_store
        .get_task(&args.task_id)?
        .or_else(|| {
            // Try prefix match
            task_store
                .get_recent_tasks(200)
                .ok()?
                .into_iter()
                .find(|t| t.id.starts_with(&args.task_id))
        })
        .ok_or_else(|| anyhow::anyhow!("Task {} not found", args.task_id))?;

    println!("Task: {}", task.title);
    println!("{}", "─".repeat(60));
    println!("ID:          {}", task.id);
    println!("Status:      {:?}", task.status);
    println!("Priority:    {:?}", task.priority);
    if let Some(cat) = &task.category {
        println!("Category:    {cat}");
    }
    if let Some(proj) = &task.project {
        println!("Project:     {proj}");
    }
    if let Some(dur) = task.duration_minutes {
        println!("Duration:    {dur:.0} min");
    }
    println!("Confidence:  {:.0}%", task.confidence_score.unwrap_or(0.0) * 100.0);
    if let Some(desc) = &task.description {
        println!("\nDescription:\n{desc}");
    }
    if !task.accomplishments.is_empty() {
        println!("\nAccomplishments:");
        for a in &task.accomplishments {
            println!("  • {a}");
        }
    }
    if !task.main_themes.is_empty() {
        println!("\nThemes: {}", task.main_themes.join(", "));
    }
    Ok(())
}

pub async fn run_summary(args: TaskSummaryArgs, cfg: Config) -> Result<()> {
    let date = parse_date(args.date.as_deref());
    let db = Db::open(&cfg.db_path)?;
    let task_store = Arc::new(TaskStore::new(db, cfg.data_dir.clone()));
    let llm_client = Arc::new(PatchaApiClient::new(&cfg));
    let summarizer = TaskSummarizer::new(task_store, llm_client, &cfg.data_dir);

    let summary = summarizer.generate_daily_task_summary(date).await?;
    println!("Task summary for {}", summary.date);
    println!("{}", "─".repeat(60));
    println!("{}", summary.overall_summary);
    println!();
    println!(
        "Tasks: {}  |  Completed: {}  |  Duration: {:.0}min",
        summary.total_tasks, summary.completed_tasks, summary.total_duration_minutes
    );
    if !summary.top_projects.is_empty() {
        println!("Projects: {}", summary.top_projects.join(", "));
    }
    Ok(())
}

pub async fn run_search(args: SearchTasksArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db.clone()));
    let task_store = Arc::new(TaskStore::new(db, cfg.data_dir.clone()));
    let embedder = Arc::new(tokio::task::block_in_place(|| Embedder::new(&cfg))?);
    let service = TaskAwareSearchService::new(store, task_store, embedder);

    let results = service.search(&args.query, "tasks", args.limit, None)?;
    let tasks = results
        .get("task_results")
        .and_then(|v| v.as_array())
        .cloned()
        .unwrap_or_default();

    if tasks.is_empty() {
        println!("No tasks found for \"{}\".", args.query);
        return Ok(());
    }
    println!("Tasks matching \"{}\":\n", args.query);
    for t in &tasks {
        let title = t.get("title").and_then(|v| v.as_str()).unwrap_or("?");
        let proj = t.get("project").and_then(|v| v.as_str()).unwrap_or("");
        let dur = t
            .get("duration_minutes")
            .and_then(|v| v.as_f64())
            .map(|d| format!("{d:.0}min"))
            .unwrap_or_default();
        let proj_tag = if proj.is_empty() { String::new() } else { format!(" [{proj}]") };
        println!("  • {title}{proj_tag} {dur}");
        if let Some(desc) = t.get("description").and_then(|v| v.as_str()) {
            let s: String = desc.chars().take(80).collect();
            println!("    {s}");
        }
    }
    Ok(())
}

pub async fn run_complete(args: CompleteTaskArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let task_store = TaskStore::new(db, cfg.data_dir.clone());

    let mut task = task_store
        .get_task(&args.task_id)?
        .or_else(|| {
            task_store
                .get_recent_tasks(200)
                .ok()?
                .into_iter()
                .find(|t| t.id.starts_with(&args.task_id))
        })
        .ok_or_else(|| anyhow::anyhow!("Task {} not found", args.task_id))?;

    task.status = TaskStatus::Completed;
    task.end_time = Some(chrono::Utc::now());
    task_store.update_task(&task)?;
    println!("Task \"{}\" marked as completed.", task.title);
    Ok(())
}

pub async fn run_stats(args: TaskStatsArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let task_store = TaskStore::new(db, cfg.data_dir.clone());
    let stats = task_store.get_task_statistics(args.days)?;
    println!("Task statistics (last {} days):", args.days);
    println!("{}", serde_json::to_string_pretty(&stats)?);
    Ok(())
}

pub async fn run_identify(args: IdentifyTasksArgs, cfg: Config) -> Result<()> {
    let date = args
        .date
        .as_deref()
        .and_then(|s| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive() - Duration::days(1));

    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db.clone()));
    let task_store = Arc::new(TaskStore::new(db, cfg.data_dir.clone()));
    let llm_client = Arc::new(PatchaApiClient::new(&cfg));
    let compactor = DailyCompactor::new(store, task_store, llm_client, &cfg);

    println!("Identifying tasks for {}{}...", date, if args.dry_run { " [dry-run]" } else { "" });
    let result = compactor.compact_day(date, args.dry_run, true).await?;
    let tasks = result.get("tasks_identified").and_then(|v| v.as_u64()).unwrap_or(0);
    let events = result.get("events_compacted").and_then(|v| v.as_u64()).unwrap_or(0);
    println!("{tasks} tasks identified from {events} events.");
    Ok(())
}
