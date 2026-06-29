use crate::{
    config::Config,
    db::{Db, store::VectorStore},
    models::{Category, Event, EventType},
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct ReembedArgs {
    #[arg(short, long)]
    pub date: Option<String>,
    #[arg(long)]
    pub all: bool,
}

#[derive(Args)]
pub struct MigrateArgs {
    #[arg(long, default_value = "http://localhost:6333")]
    pub qdrant_url: String,
    #[arg(long, default_value = "mem")]
    pub collection: String,
    #[arg(long)]
    pub dry_run: bool,
}

#[derive(Args)]
pub struct CategorizationAnalysisArgs {
    #[arg(long, default_value = "7")]
    pub days: u32,
}

pub async fn run_reembed(args: ReembedArgs, cfg: Config) -> Result<()> {
    use crate::embedding::Embedder;

    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db));
    let embedder = tokio::task::block_in_place(|| Embedder::new(&cfg))?;

    let events: Vec<Event> = if args.all {
        store.get_recent_events(100_000)?
    } else {
        let date_str = args
            .date
            .clone()
            .unwrap_or_else(|| chrono::Local::now().date_naive().format("%Y-%m-%d").to_string());
        store.get_events_by_date(&date_str)?
    };

    if events.is_empty() {
        println!("No events to re-embed.");
        return Ok(());
    }

    println!("Re-embedding {} events...", events.len());
    let mut updated = 0usize;
    for event in &events {
        let text = event.summary.as_deref().unwrap_or(&event.raw_content);
        if text.is_empty() {
            continue;
        }
        match tokio::task::block_in_place(|| embedder.embed_one(text)) {
            Ok(emb) => {
                store.update_event_embedding(&event.id, &emb)?;
                updated += 1;
            }
            Err(e) => tracing::warn!(error=%e, id=%event.id, "re-embed failed"),
        }
    }
    println!("Updated embeddings for {updated}/{} events.", events.len());
    Ok(())
}

pub async fn run_migrate(args: MigrateArgs, cfg: Config) -> Result<()> {
    println!("Migrating from Qdrant ({}) → sqlite-vec", args.qdrant_url);
    if args.dry_run {
        println!("[dry-run] No data will be written.");
    }

    let client = reqwest::Client::new();

    // Verify Qdrant is reachable
    let health_url = format!("{}/collections/{}", args.qdrant_url, args.collection);
    let resp = client
        .get(&health_url)
        .timeout(std::time::Duration::from_secs(5))
        .send()
        .await
        .map_err(|e| anyhow::anyhow!("Cannot reach Qdrant at {}: {e}", args.qdrant_url))?;

    if !resp.status().is_success() {
        anyhow::bail!(
            "Qdrant collection '{}' not found (status {}). Is Qdrant running?",
            args.collection,
            resp.status()
        );
    }

    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db));

    let scroll_url = format!(
        "{}/collections/{}/points/scroll",
        args.qdrant_url, args.collection
    );

    let mut total = 0usize;
    let mut offset: Option<String> = None;
    let batch_size = 100usize;

    loop {
        let body = serde_json::json!({
            "with_vector": true,
            "with_payload": true,
            "limit": batch_size,
            "offset": offset,
        });

        let resp = client
            .post(&scroll_url)
            .json(&body)
            .timeout(std::time::Duration::from_secs(30))
            .send()
            .await?
            .json::<serde_json::Value>()
            .await?;

        let result = resp
            .get("result")
            .ok_or_else(|| anyhow::anyhow!("unexpected Qdrant response"))?;

        let points = result
            .get("points")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        if points.is_empty() {
            break;
        }

        let next_offset = result
            .get("next_page_offset")
            .and_then(|v| v.as_str())
            .map(|s| s.to_owned());

        let mut batch: Vec<Event> = Vec::new();
        for point in &points {
            if let Some(event) = qdrant_point_to_event(point) {
                batch.push(event);
            }
        }

        println!(
            "  Batch: {} points ({} valid events)",
            points.len(),
            batch.len()
        );

        if !args.dry_run && !batch.is_empty() {
            store.store_events(&batch)?;
        }

        total += batch.len();

        match next_offset {
            Some(off) if !off.is_empty() => offset = Some(off),
            _ => break,
        }
    }

    if args.dry_run {
        println!("[dry-run] Would have migrated {total} events.");
    } else {
        println!("Migration complete: {total} events stored in sqlite-vec.");
    }
    Ok(())
}

pub async fn run_categorization_analysis(args: CategorizationAnalysisArgs, cfg: Config) -> Result<()> {
    use std::collections::HashMap;

    let db = Db::open(&cfg.db_path)?;
    let store = VectorStore::new(db);

    let since = chrono::Utc::now() - chrono::Duration::days(args.days as i64);
    let events = store.get_events_since(since, 50_000)?;

    let total = events.len();
    if total == 0 {
        println!("No events in the last {} days.", args.days);
        return Ok(());
    }

    let mut by_category: HashMap<String, usize> = HashMap::new();
    let mut uncategorized = 0usize;
    for e in &events {
        match &e.category {
            Some(cat) => *by_category.entry(cat.to_string()).or_insert(0) += 1,
            None => uncategorized += 1,
        }
    }

    println!("Categorization analysis (last {} days, {} events):", args.days, total);
    println!("{}", "─".repeat(50));
    println!("{:<20} {:>6}  {:>6}", "Category", "Count", "Pct");
    println!("{}", "─".repeat(50));

    let mut cats: Vec<_> = by_category.iter().collect();
    cats.sort_by_key(|(_, n)| std::cmp::Reverse(**n));
    for (cat, n) in &cats {
        let pct = **n * 100 / total;
        println!("{:<20} {:>6}  {:>5}%", cat, n, pct);
    }
    if uncategorized > 0 {
        let pct = uncategorized * 100 / total;
        println!("{:<20} {:>6}  {:>5}%", "(none)", uncategorized, pct);
    }

    let categorized = total - uncategorized;
    println!(
        "\nCategorized: {}/{total} ({:.0}%)",
        categorized,
        categorized as f64 / total as f64 * 100.0
    );
    Ok(())
}

// ---------------------------------------------------------------------------
// Qdrant → Event conversion
// ---------------------------------------------------------------------------

fn qdrant_point_to_event(point: &serde_json::Value) -> Option<Event> {
    let id = point.get("id")?.as_str()?.to_owned();
    let payload = point.get("payload")?;
    let vector = point
        .get("vector")
        .and_then(|v| v.as_array())
        .map(|arr| arr.iter().filter_map(|v| v.as_f64().map(|f| f as f32)).collect::<Vec<f32>>());

    let timestamp_str = payload.get("timestamp").and_then(|v| v.as_str())?;
    let timestamp = DateTime::parse_from_rfc3339(timestamp_str)
        .ok()?
        .with_timezone(&Utc);

    let event_type_str = payload
        .get("event_type")
        .or_else(|| payload.get("type"))
        .and_then(|v| v.as_str())
        .unwrap_or("window");

    let event_type = match event_type_str {
        "git_commit" => EventType::GitCommit,
        "git_stash" => EventType::GitStash,
        "git_staged" => EventType::GitStaged,
        "browser" => EventType::Browser,
        "terminal" => EventType::Terminal,
        "screen" => EventType::Screen,
        "note" => EventType::Note,
        _ => EventType::Window,
    };

    let category = payload
        .get("category")
        .and_then(|v| v.as_str())
        .and_then(|s| s.parse::<Category>().ok());

    let metadata = payload
        .get("metadata")
        .cloned()
        .and_then(|v| serde_json::from_value(v).ok())
        .unwrap_or_default();

    Some(Event {
        id,
        timestamp,
        event_type,
        source: payload.get("source").and_then(|v| v.as_str()).map(|s| s.to_owned()),
        source_doc_id: payload.get("source_doc_id").and_then(|v| v.as_str()).map(|s| s.to_owned()),
        raw_content: payload.get("raw_content").and_then(|v| v.as_str()).unwrap_or("").to_owned(),
        embedding: vector,
        summary: payload.get("summary").and_then(|v| v.as_str()).map(|s| s.to_owned()),
        category,
        project: payload.get("project").and_then(|v| v.as_str()).map(|s| s.to_owned()),
        task_id: payload.get("task_id").and_then(|v| v.as_str()).map(|s| s.to_owned()),
        metadata,
    })
}
