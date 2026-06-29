use crate::{
    compaction::DailyCompactor,
    config::Config,
    db::{Db, store::VectorStore, tasks::TaskStore},
    llm::client::PatchaApiClient,
};
use anyhow::Result;
use chrono::{Duration, Local};
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct CompactDayArgs {
    #[arg(short, long, help = "Date to compact (YYYY-MM-DD, default: yesterday)")]
    pub date: Option<String>,
    #[arg(long, help = "Show what would be done without writing")]
    pub dry_run: bool,
    #[arg(long, help = "Force recompaction of already-compacted days")]
    pub force: bool,
}

pub async fn run(args: CompactDayArgs, cfg: Config) -> Result<()> {
    let date = args
        .date
        .as_deref()
        .and_then(|s| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive() - Duration::days(1));

    if args.dry_run {
        println!("[dry-run] Would compact {}.", date);
    } else {
        println!("Compacting {}...", date);
    }

    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db.clone()));
    let task_store = Arc::new(TaskStore::new(db, cfg.data_dir.clone()));
    let llm_client = Arc::new(PatchaApiClient::new(&cfg));
    let compactor = DailyCompactor::new(store, task_store, llm_client, &cfg);

    let result = compactor.compact_day(date, args.dry_run, args.force).await?;

    let tasks = result.get("tasks_identified").and_then(|v| v.as_u64()).unwrap_or(0);
    let events = result.get("events_compacted").and_then(|v| v.as_u64()).unwrap_or(0);
    if args.dry_run {
        println!("[dry-run] Would identify {tasks} tasks from {events} events.");
    } else {
        println!("Done: {tasks} tasks identified, {events} events compacted.");
    }
    Ok(())
}
