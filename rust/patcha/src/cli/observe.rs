use crate::{
    collectors::{
        accessibility::AccessibilityCollector, browser::BrowserCollector, git::GitCollector,
        terminal::TerminalCollector, window::WindowCollector,
    },
    config::Config,
    models::Event,
};
use anyhow::Result;
use chrono::{Local, NaiveDate, NaiveTime, TimeZone, Utc};
use clap::Args;
use std::collections::HashMap;

#[derive(Args)]
pub struct ObserveArgs {
    #[arg(short, long, help = "Date to observe (YYYY-MM-DD, default: today)")]
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

pub async fn run(args: ObserveArgs, cfg: Config) -> Result<()> {
    let date = args
        .date
        .as_deref()
        .and_then(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .unwrap_or_else(|| Local::now().date_naive());

    let since = Utc
        .from_local_datetime(&date.and_time(NaiveTime::MIN))
        .single()
        .unwrap_or_else(Utc::now);

    println!("Observing activities for {} (no LLM)", date);

    std::fs::create_dir_all(&cfg.data_dir)?;
    let script_path = cfg.data_dir.join("window_title.applescript");
    if !script_path.exists() {
        std::fs::write(&script_path, WINDOW_SCRIPT)?;
    }
    let res_dir = resources_dir();

    let mut all_events: Vec<Event> = Vec::new();

    macro_rules! try_collect {
        ($name:expr, $block:expr) => {
            match std::panic::catch_unwind(std::panic::AssertUnwindSafe(|| $block)) {
                Ok(ev) => {
                    println!("  {}: {} events", $name, ev.len());
                    all_events.extend(ev);
                }
                Err(_) => println!("  {}: skipped (error)", $name),
            }
        };
    }

    if cfg.enable_git_collector {
        let data_dir = cfg.data_dir.clone();
        try_collect!("git", {
            let mut gc = GitCollector::new(data_dir);
            let mut v = gc.collect_commits(Some(since));
            v.extend(gc.collect_staging_events(since));
            v
        });
    }
    if cfg.enable_browser_collector {
        try_collect!("browser", BrowserCollector::new().collect_all(Some(since)));
    }
    if cfg.enable_terminal_collector {
        try_collect!(
            "terminal",
            TerminalCollector::new().collect_all(Some(since))
        );
    }
    if cfg.enable_window_collector {
        let sp = script_path.clone();
        let dd = cfg.data_dir.clone();
        try_collect!(
            "window",
            WindowCollector::new(&dd, sp).collect_windows(since)
        );
    }
    if cfg.enable_accessibility_collector {
        let ac = AccessibilityCollector::new(&cfg.data_dir, &res_dir, &cfg);
        try_collect!("accessibility", ac.collect_screen_text(since));
    }

    if all_events.is_empty() {
        println!("No events collected.");
        return Ok(());
    }

    all_events.sort_by_key(|e| e.timestamp);

    // Group into sessions by 10-minute idle gaps
    let gap = chrono::Duration::minutes(10);
    let mut sessions: Vec<Vec<&Event>> = Vec::new();
    let mut current: Vec<&Event> = Vec::new();
    for event in &all_events {
        if let Some(last) = current.last() {
            if event.timestamp - last.timestamp > gap {
                sessions.push(current);
                current = Vec::new();
            }
        }
        current.push(event);
    }
    if !current.is_empty() {
        sessions.push(current);
    }

    println!(
        "\n{} events in {} sessions:\n",
        all_events.len(),
        sessions.len()
    );
    println!(
        "{:<5} {:<20} {:<8} {:<20} Sources",
        "#", "Time range", "Events", "Top source"
    );
    println!("{}", "-".repeat(75));

    for (i, session) in sessions.iter().enumerate() {
        let start = session
            .first()
            .unwrap()
            .timestamp
            .format("%H:%M")
            .to_string();
        let end = session
            .last()
            .unwrap()
            .timestamp
            .format("%H:%M")
            .to_string();
        let mut src_counts: HashMap<String, usize> = HashMap::new();
        for e in session.iter() {
            *src_counts.entry(e.event_type.to_string()).or_insert(0) += 1;
        }
        let top = src_counts
            .iter()
            .max_by_key(|(_, n)| *n)
            .map(|(k, _)| k.as_str())
            .unwrap_or("-");
        let breakdown: Vec<String> = {
            let mut v: Vec<_> = src_counts.iter().collect();
            v.sort_by_key(|(k, _)| k.as_str());
            v.iter().map(|(k, n)| format!("{k}:{n}")).collect()
        };
        println!(
            "{:<5} {:<20} {:<8} {:<20} {}",
            i + 1,
            format!("{start} - {end}"),
            session.len(),
            top,
            breakdown.join("  ")
        );
    }
    Ok(())
}
