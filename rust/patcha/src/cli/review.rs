use crate::{
    config::Config,
    db::{Db, store::VectorStore},
    llm::client::PatchaApiClient,
    summary::DailySummarizer,
};
use anyhow::Result;
use chrono::{Duration, Local, NaiveDate};
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct ReviewArgs {
    #[arg(short, long, help = "Start date (YYYY-MM-DD)")]
    pub date: Option<String>,
    #[arg(long, default_value = "7", help = "Number of days to review")]
    pub days: Option<u32>,
}

pub async fn run(args: ReviewArgs, cfg: Config) -> Result<()> {
    let days = args.days.unwrap_or(7) as i64;
    let end_date = args
        .date
        .as_deref()
        .and_then(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive());
    let start_date = end_date - Duration::days(days - 1);

    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db));
    let llm_client = Arc::new(PatchaApiClient::new(&cfg));
    let summarizer = DailySummarizer::new(store, llm_client, &cfg.data_dir);

    println!("Review: {} to {}\n", start_date, end_date);

    let mut current = start_date;
    while current <= end_date {
        let day_label = current.format("%A, %b %d").to_string();
        match summarizer.load_summary(current)? {
            Some(s) => {
                println!("── {} ──────────────────────────────", day_label);
                println!(
                    "  Events: {}  |  Projects: {}",
                    s.total_events,
                    s.top_projects.len()
                );
                let snippet: String = s.overall_summary.chars().take(200).collect();
                println!("  {snippet}");
            }
            None => {
                let date_str = current.format("%Y-%m-%d").to_string();
                // Try to load raw events if no summary saved
                // Just show event count
                println!("── {} ──────────────────────────────", day_label);
                println!("  No summary (run: patcha summarize -d {date_str})");
            }
        }
        println!();
        current += Duration::days(1);
    }
    Ok(())
}
