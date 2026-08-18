use crate::{
    config::Config,
    db::{retrieval::search::TaskAwareSearchService, store::VectorStore, tasks::TaskStore, Db},
    embedding::Embedder,
    llm::backend,
    summary::TaskSummarizer,
};
use anyhow::Result;
use chrono::Local;
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct RagSummaryArgs {
    #[arg(short, long, help = "Date (YYYY-MM-DD, default: today)")]
    pub date: Option<String>,
    #[arg(long, help = "Include AI-generated insights")]
    pub ai: bool,
}

#[derive(Args)]
pub struct ProjectAnalysisArgs {
    pub project: String,
    #[arg(long, default_value = "7", help = "Days to look back")]
    pub days: u32,
}

#[derive(Args)]
pub struct ContextualSearchArgs {
    pub query: String,
    #[arg(short, long, default_value = "10")]
    pub limit: usize,
}

pub async fn run_summary(args: RagSummaryArgs, cfg: Config) -> Result<()> {
    let date = args
        .date
        .as_deref()
        .and_then(|s| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive());

    let db = Db::open(&cfg.db_path)?;
    let task_store = Arc::new(TaskStore::new(db, cfg.data_dir.clone()));
    let llm_client = backend::build(&cfg);
    let summarizer = TaskSummarizer::new(task_store, llm_client, &cfg.data_dir);

    let result = summarizer
        .generate_rich_daily_summary(date, args.ai)
        .await?;
    println!(
        "RAG summary for {}:",
        result.get("date").and_then(|v| v.as_str()).unwrap_or("?")
    );
    println!("{}", "─".repeat(60));
    if let Some(overview) = result.get("overview").and_then(|v| v.as_str()) {
        println!("{overview}");
    }
    if let Some(tasks) = result.get("tasks").and_then(|v| v.as_array()) {
        println!("\nTasks ({}):", tasks.len());
        for t in tasks {
            let title = t.get("title").and_then(|v| v.as_str()).unwrap_or("?");
            let dur = t
                .get("duration_minutes")
                .and_then(|v| v.as_f64())
                .unwrap_or(0.0);
            println!("  • {title} ({dur:.0}min)");
        }
    }
    Ok(())
}

pub async fn run_project(args: ProjectAnalysisArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db.clone()));
    let task_store = Arc::new(TaskStore::new(db, cfg.data_dir.clone()));
    let _embedder = Arc::new(tokio::task::block_in_place(|| Embedder::new(&cfg))?);

    let since = chrono::Utc::now() - chrono::Duration::days(args.days as i64);
    let events = store.get_events_since(since, 10_000)?;
    let proj_events: Vec<_> = events
        .iter()
        .filter(|e| e.project.as_deref() == Some(&args.project))
        .collect();

    let end = chrono::Local::now().date_naive();
    let start = end - chrono::Duration::days(args.days as i64 - 1);
    let tasks = task_store.get_tasks_by_date_range(start, end)?;
    let proj_tasks: Vec<_> = tasks
        .iter()
        .filter(|t| t.project.as_deref() == Some(&args.project))
        .collect();

    println!("Project analysis: {}", args.project);
    println!("{}", "─".repeat(60));
    println!("Last {} days:", args.days);
    println!("  Events: {}", proj_events.len());
    println!("  Tasks:  {}", proj_tasks.len());

    let mut type_counts: std::collections::HashMap<String, usize> =
        std::collections::HashMap::new();
    for e in &proj_events {
        *type_counts.entry(e.event_type.to_string()).or_insert(0) += 1;
    }
    if !type_counts.is_empty() {
        println!("\nEvent breakdown:");
        let mut types: Vec<_> = type_counts.iter().collect();
        types.sort_by_key(|(_, n)| std::cmp::Reverse(**n));
        for (t, n) in types {
            println!("  {t}: {n}");
        }
    }

    if !proj_tasks.is_empty() {
        println!("\nTasks:");
        for t in proj_tasks.iter().take(10) {
            let dur = t
                .duration_minutes
                .map(|d| format!("{d:.0}min"))
                .unwrap_or_default();
            println!("  • {} [{}]", t.title, dur);
        }
    }
    Ok(())
}

pub async fn run_search(args: ContextualSearchArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db.clone()));
    let task_store = Arc::new(TaskStore::new(db, cfg.data_dir.clone()));
    let embedder = Arc::new(tokio::task::block_in_place(|| Embedder::new(&cfg))?);
    let service = TaskAwareSearchService::new(store, task_store, embedder);

    let result = service.search(&args.query, "both", args.limit, None)?;

    println!("Contextual search: \"{}\"\n", args.query);

    if let Some(tasks) = result.get("task_results").and_then(|v| v.as_array()) {
        if !tasks.is_empty() {
            println!("Tasks ({}):", tasks.len());
            for t in tasks {
                let title = t.get("title").and_then(|v| v.as_str()).unwrap_or("?");
                let proj = t.get("project").and_then(|v| v.as_str()).unwrap_or("");
                let dur = t
                    .get("duration_minutes")
                    .and_then(|v| v.as_f64())
                    .map(|d| format!("{d:.0}min"))
                    .unwrap_or_default();
                let proj_tag = if proj.is_empty() {
                    String::new()
                } else {
                    format!(" [{proj}]")
                };
                println!("  • {title}{proj_tag} {dur}");
            }
            println!();
        }
    }

    if let Some(acts) = result.get("activity_results").and_then(|v| v.as_array()) {
        if !acts.is_empty() {
            println!("Activities ({}):", acts.len());
            for a in acts {
                let ts = a.get("timestamp").and_then(|v| v.as_str()).unwrap_or("?");
                let etype = a.get("type").and_then(|v| v.as_str()).unwrap_or("?");
                let summary = a.get("summary").and_then(|v| v.as_str()).unwrap_or("");
                let snippet: String = summary.chars().take(80).collect();
                println!("  [{ts:.16}] {etype}: {snippet}");
            }
        }
    }

    if result
        .get("total_results")
        .and_then(|v| v.as_u64())
        .unwrap_or(0)
        == 0
    {
        println!("No results found.");
    }
    Ok(())
}
