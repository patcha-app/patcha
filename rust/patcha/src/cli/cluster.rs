use crate::{
    config::Config,
    db::{store::VectorStore, Db},
};
use anyhow::Result;
use chrono::Local;
use clap::Args;
use std::collections::HashMap;

#[derive(Args)]
pub struct ClusterArgs {
    #[arg(short, long, help = "Date to cluster (YYYY-MM-DD, default: today)")]
    pub date: Option<String>,
    #[arg(long, default_value = "dbscan")]
    pub method: String,
}

pub async fn run(args: ClusterArgs, cfg: Config) -> Result<()> {
    let date = args
        .date
        .as_deref()
        .and_then(|s| chrono::NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive());
    let date_str = date.format("%Y-%m-%d").to_string();

    let db = Db::open(&cfg.db_path)?;
    let store = VectorStore::new(db);
    let events = store.get_events_by_date(&date_str)?;

    if events.is_empty() {
        println!("No events for {date_str}.");
        return Ok(());
    }

    // Group by project, then by event type
    let mut by_project: HashMap<String, Vec<&crate::models::Event>> = HashMap::new();
    for e in &events {
        let key = e.project.clone().unwrap_or_else(|| "unassigned".into());
        by_project.entry(key).or_default().push(e);
    }

    println!("Clusters for {date_str} ({} events):\n", events.len());
    println!("{:<30} {:>6}  Types", "Project", "Events");
    println!("{}", "─".repeat(65));

    let mut projects: Vec<_> = by_project.iter().collect();
    projects.sort_by_key(|(_, v)| std::cmp::Reverse(v.len()));

    for (proj, evs) in &projects {
        let mut type_counts: HashMap<String, usize> = HashMap::new();
        for e in evs.iter() {
            *type_counts.entry(e.event_type.to_string()).or_insert(0) += 1;
        }
        let mut types: Vec<_> = type_counts.iter().collect();
        types.sort_by_key(|(_, n)| std::cmp::Reverse(**n));
        let type_str: Vec<String> = types.iter().map(|(t, n)| format!("{t}:{n}")).collect();
        println!("{:<30} {:>6}  {}", proj, evs.len(), type_str.join("  "));
    }
    Ok(())
}
