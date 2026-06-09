use crate::{
    config::Config,
    db::{Db, store::VectorStore, tasks::TaskStore},
    embedding::Embedder,
    llm::client::PatchaApiClient,
    summary::{DailySummarizer, TaskSummarizer},
};
use anyhow::Result;
use chrono::{Local, NaiveDate};
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct SummarizeArgs {
    #[arg(short, long, help = "Date to summarize (YYYY-MM-DD, default: today)")]
    pub date: Option<String>,
    #[arg(long, help = "Generate task-based summary instead of event-based")]
    pub task_based: bool,
}

pub async fn run(args: SummarizeArgs, cfg: Config) -> Result<()> {
    let date = args
        .date
        .as_deref()
        .and_then(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive());

    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db.clone()));
    let llm_client = Arc::new(PatchaApiClient::new(&cfg));

    if args.task_based {
        let task_store = Arc::new(TaskStore::new(db, cfg.data_dir.clone()));
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
        println!();
        for task in &summary.tasks {
            let dur = task
                .duration_minutes
                .map(|d| format!("{d:.0}min"))
                .unwrap_or_else(|| "?".into());
            println!("  • {} [{}]", task.title, dur);
            if let Some(desc) = &task.description {
                let snippet: String = desc.chars().take(100).collect();
                println!("    {snippet}");
            }
        }
    } else {
        let summarizer = DailySummarizer::new(store, llm_client, &cfg.data_dir);
        match summarizer.generate_daily_summary(date).await? {
            None => println!("No activities found for {date}."),
            Some(summary) => {
                println!("Summary for {}", summary.date);
                println!("{}", "─".repeat(60));
                println!("{}", summary.overall_summary);
                println!();
                println!(
                    "Total events: {}  |  Projects: {}",
                    summary.total_events,
                    summary.top_projects.len()
                );
                if !summary.top_projects.is_empty() {
                    println!("Projects: {}", summary.top_projects.join(", "));
                }
                if !summary.category_summaries.is_empty() {
                    println!();
                    println!("{:<18} {:>6}  Summary", "Category", "Events");
                    println!("{}", "─".repeat(60));
                    for cs in &summary.category_summaries {
                        let snippet: String = cs.summary.chars().take(50).collect();
                        println!("{:<18} {:>6}  {snippet}", cs.category.to_string(), cs.event_count);
                    }
                }
            }
        }
    }
    Ok(())
}
