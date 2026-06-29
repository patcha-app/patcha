use crate::{
    config::Config,
    db::{Db, store::VectorStore},
};
use anyhow::Result;
use chrono::{Duration, Local, NaiveDate, TimeZone, Timelike, Utc};
use clap::Args;
use std::collections::HashMap;

#[derive(Args)]
pub struct PatternsArgs {
    #[arg(long, default_value = "30", help = "Number of days to analyze")]
    pub days: u32,
}

pub async fn run(args: PatternsArgs, cfg: Config) -> Result<()> {
    let end = Local::now().date_naive();
    let start = end - Duration::days(args.days as i64 - 1);

    let db = Db::open(&cfg.db_path)?;
    let store = VectorStore::new(db);

    let since = Utc
        .from_local_datetime(&start.and_time(chrono::NaiveTime::MIN))
        .single()
        .unwrap_or_else(Utc::now);
    let events = store.get_events_since(since, 50_000)?;

    if events.is_empty() {
        println!("No events in the last {} days.", args.days);
        return Ok(());
    }

    println!("Activity patterns over the last {} days:\n", args.days);

    // Event type breakdown
    let mut by_type: HashMap<String, usize> = HashMap::new();
    for e in &events {
        *by_type.entry(e.event_type.to_string()).or_insert(0) += 1;
    }
    let total = events.len();
    let mut types: Vec<_> = by_type.iter().collect();
    types.sort_by_key(|(_, n)| std::cmp::Reverse(**n));
    println!("Event type breakdown ({total} total):");
    for (t, n) in &types {
        let pct = *n * 100 / total;
        println!("  {:<18} {:>5}  ({pct}%)", t, n);
    }

    // Top projects
    let mut by_project: HashMap<&str, usize> = HashMap::new();
    for e in &events {
        if let Some(p) = e.project.as_deref() {
            *by_project.entry(p).or_insert(0) += 1;
        }
    }
    if !by_project.is_empty() {
        let mut projects: Vec<_> = by_project.iter().collect();
        projects.sort_by_key(|(_, n)| std::cmp::Reverse(**n));
        println!("\nTop projects:");
        for (p, n) in projects.iter().take(10) {
            println!("  {:<30} {:>5} events", p, n);
        }
    }

    // Most active hours
    let mut by_hour: HashMap<u32, usize> = HashMap::new();
    for e in &events {
        let h = e.timestamp.hour();
        *by_hour.entry(h).or_insert(0) += 1;
    }
    let mut hours: Vec<_> = by_hour.iter().collect();
    hours.sort_by_key(|(_, n)| std::cmp::Reverse(**n));
    if !hours.is_empty() {
        println!("\nMost active hours:");
        for (h, n) in hours.iter().take(5) {
            println!("  {:02}:00  {:>5} events", h, n);
        }
    }

    // Active days
    let mut by_day: HashMap<NaiveDate, usize> = HashMap::new();
    for e in &events {
        let d = e.timestamp.date_naive();
        *by_day.entry(d).or_insert(0) += 1;
    }
    let avg = if by_day.is_empty() {
        0.0
    } else {
        total as f64 / by_day.len() as f64
    };
    println!("\nActive days: {}  |  Avg events/day: {:.0}", by_day.len(), avg);

    Ok(())
}
