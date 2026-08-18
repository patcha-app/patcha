use crate::{
    config::Config,
    db::{store::VectorStore, Db},
    embedding::Embedder,
};
use anyhow::Result;
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct SearchArgs {
    pub query: String,
    #[arg(short, long, default_value = "10")]
    pub limit: usize,
    #[arg(long, help = "Filter to events from this app")]
    pub app: Option<String>,
}

pub async fn run(args: SearchArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db));
    let embedder = tokio::task::block_in_place(|| Embedder::new(&cfg))?;

    let embedding = tokio::task::block_in_place(|| embedder.embed_query(&args.query))?;
    let results = store.search_events(&embedding, args.limit, None)?;

    if results.is_empty() {
        println!("No results for \"{}\".", args.query);
        return Ok(());
    }

    let app_filter = args.app.as_deref();
    let filtered: Vec<_> = results
        .iter()
        .filter(|r| {
            app_filter
                .map(|app| r.event.metadata.get("app_name").and_then(|v| v.as_str()) == Some(app))
                .unwrap_or(true)
        })
        .collect();

    if filtered.is_empty() {
        println!(
            "No results matching app filter \"{}\".",
            app_filter.unwrap_or("")
        );
        return Ok(());
    }

    println!(
        "Results for \"{}\"{}:\n",
        args.query,
        app_filter
            .map(|a| format!(" (app={a})"))
            .unwrap_or_default()
    );

    for (i, r) in filtered.iter().enumerate() {
        let ts = r.event.timestamp.format("%Y-%m-%d %H:%M").to_string();
        let etype = r.event.event_type.to_string();
        let content = r.event.summary.as_deref().unwrap_or(&r.event.raw_content);
        let snippet: String = content.chars().take(120).collect();
        let proj = r
            .event
            .project
            .as_deref()
            .map(|p| format!(" [{p}]"))
            .unwrap_or_default();
        println!("[{:>2}] score={:.3}  {ts}  {etype}{proj}", i + 1, r.score);
        println!("     {snippet}");
        println!();
    }
    Ok(())
}
