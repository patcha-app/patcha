use crate::{
    collectors::{
        accessibility::AccessibilityCollector,
        browser::BrowserCollector,
        git::GitCollector,
        terminal::TerminalCollector,
        window::WindowCollector,
    },
    config::Config,
    db::{Db, store::VectorStore},
    embedding::Embedder,
    models::Event,
    process::EventPreprocessor,
};
use anyhow::Result;
use chrono::{Local, NaiveDate, NaiveTime, TimeZone, Utc};
use clap::Args;
use std::sync::Arc;

#[derive(Args)]
pub struct CollectArgs {
    #[arg(long, help = "Collect only this source (git|browser|terminal|window|accessibility)")]
    pub source: Option<String>,
    #[arg(short, long, help = "Date to collect for (YYYY-MM-DD, default: today)")]
    pub date: Option<String>,
}

const WINDOW_SCRIPT: &str = r#"tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    try
        set windowTitle to name of front window of frontApp
    on error
        set windowTitle to ""
    end try
    return appName & "|||" & windowTitle
end tell"#;

fn resources_dir() -> std::path::PathBuf {
    if let Ok(d) = std::env::var("PATCHA_RESOURCES") {
        return std::path::PathBuf::from(d);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(p) = exe.parent() {
            if p.join("ax_content").exists() {
                return p.to_path_buf();
            }
        }
    }
    std::path::PathBuf::from("data")
}

fn date_since(s: Option<&str>) -> chrono::DateTime<Utc> {
    let date = s
        .and_then(|d| NaiveDate::parse_from_str(d, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive());
    Utc.from_local_datetime(&date.and_time(NaiveTime::MIN))
        .single()
        .unwrap_or_else(Utc::now)
}

pub async fn run(args: CollectArgs, cfg: Config) -> Result<()> {
    let since = date_since(args.date.as_deref());
    let filter = args.source.as_deref();
    println!(
        "Collecting activities since {}{}...",
        since.format("%Y-%m-%d"),
        filter.map(|s| format!(" (source: {s})")).unwrap_or_default()
    );

    std::fs::create_dir_all(&cfg.data_dir)?;
    let script_path = cfg.data_dir.join("window_title.applescript");
    if !script_path.exists() {
        std::fs::write(&script_path, WINDOW_SCRIPT)?;
    }

    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db));
    let embedder = Arc::new(tokio::task::block_in_place(|| Embedder::new(&cfg))?);
    let preprocessor = EventPreprocessor::new(&cfg, Arc::clone(&embedder));

    let res_dir = resources_dir();
    let mut all_events: Vec<Event> = Vec::new();
    let mut source_counts: Vec<(String, usize)> = Vec::new();

    let want = |name: &str| filter.map(|f| f == name).unwrap_or(true);

    if want("git") && cfg.enable_git_collector {
        let mut gc = GitCollector::new(cfg.data_dir.clone());
        let ev = tokio::task::block_in_place(|| {
            let mut v = gc.collect_commits(Some(since));
            v.extend(gc.collect_staging_events(since));
            v
        });
        source_counts.push(("git".into(), ev.len()));
        all_events.extend(ev);
    }

    if want("browser") && cfg.enable_browser_collector {
        let ev = tokio::task::block_in_place(|| BrowserCollector::new().collect_all(Some(since)));
        source_counts.push(("browser".into(), ev.len()));
        all_events.extend(ev);
    }

    if want("terminal") && cfg.enable_terminal_collector {
        let ev =
            tokio::task::block_in_place(|| TerminalCollector::new().collect_all(Some(since)));
        source_counts.push(("terminal".into(), ev.len()));
        all_events.extend(ev);
    }

    if want("window") && cfg.enable_window_collector {
        let wc = WindowCollector::new(&cfg.data_dir, script_path.clone());
        let ev = tokio::task::block_in_place(|| wc.collect_windows(since));
        source_counts.push(("window".into(), ev.len()));
        all_events.extend(ev);
    }

    if want("accessibility") && cfg.enable_accessibility_collector {
        let ac = AccessibilityCollector::new(&cfg.data_dir, &res_dir, &cfg);
        let ev = tokio::task::block_in_place(|| ac.collect_screen_text(since));
        source_counts.push(("accessibility".into(), ev.len()));
        all_events.extend(ev);
    }

    for (src, n) in &source_counts {
        println!("  {src}: {n} events");
    }

    if all_events.is_empty() {
        println!("No events collected.");
        return Ok(());
    }

    println!("Processing {} events...", all_events.len());
    let processed = tokio::task::block_in_place(|| preprocessor.process_events(all_events));
    store.store_events(&processed)?;
    println!("Stored {} events.", processed.len());
    Ok(())
}
