use crate::models::{Event, EventType};
use anyhow::Result;
use chrono::{DateTime, Utc};
use std::io::Write;
use std::path::PathBuf;

const MAX_LOG_ROWS: usize = 100_000;
const TRIM_EVERY: usize = 1_000;
const MIN_DURATION_SECS: i64 = 30;

pub struct WindowCollector {
    log_file: PathBuf,
    script_path: PathBuf,
    write_count: usize,
}

impl WindowCollector {
    pub fn new(data_dir: &std::path::Path, script_path: PathBuf) -> Self {
        Self {
            log_file: data_dir.join("window_log.jsonl"),
            script_path,
            write_count: 0,
        }
    }

    /// Snapshot the active window and append to the log file.
    /// Called periodically by the daemon (same cadence as ax_content).
    pub fn record_current_window(&mut self) -> Result<()> {
        let (app, title) = self.get_active_window()?;
        if app.is_empty() {
            return Ok(());
        }

        if let Some(parent) = self.log_file.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let entry = serde_json::json!({
            "timestamp": Utc::now().to_rfc3339(),
            "app": app,
            "title": title,
        });

        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_file)?;
        writeln!(f, "{}", entry)?;

        self.write_count += 1;
        if self.write_count.is_multiple_of(TRIM_EVERY) {
            trim_jsonl(&self.log_file, MAX_LOG_ROWS)?;
        }

        Ok(())
    }

    /// Return completed window-focus sessions since `since`.
    /// The currently active (still-open) session is excluded.
    pub fn collect_windows(&self, since: DateTime<Utc>) -> Vec<Event> {
        let Ok(entries) = self.read_log(since) else {
            return Vec::new();
        };
        if entries.len() < 2 {
            return Vec::new();
        }

        let mut events = Vec::new();
        let (mut session_start, mut session_app, mut session_title) = entries[0].clone();

        for (ts, app, title) in &entries[1..] {
            if app != &session_app || title != &session_title {
                let duration = (*ts - session_start).num_seconds();
                if duration >= MIN_DURATION_SECS {
                    events.push(make_window_event(
                        session_start,
                        &session_app,
                        &session_title,
                        duration,
                    ));
                }
                session_start = *ts;
                session_app = app.clone();
                session_title = title.clone();
            }
        }
        // The last session (still active) is intentionally not emitted.

        events
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    fn get_active_window(&self) -> Result<(String, String)> {
        if !self.script_path.exists() {
            return Ok((String::new(), String::new()));
        }

        let output = std::process::Command::new("osascript")
            .arg(&self.script_path)
            .output();

        match output {
            Ok(out) if out.status.success() => {
                let text = String::from_utf8_lossy(&out.stdout);
                let parts: Vec<&str> = text.trim().split("|||").collect();
                let app = parts.first().copied().unwrap_or("").trim().to_owned();
                let title = parts.get(1).copied().unwrap_or("").trim().to_owned();
                Ok((app, title))
            }
            _ => Ok((String::new(), String::new())),
        }
    }

    fn read_log(&self, since: DateTime<Utc>) -> Result<Vec<(DateTime<Utc>, String, String)>> {
        if !self.log_file.exists() {
            return Ok(Vec::new());
        }

        let content = std::fs::read_to_string(&self.log_file)?;
        let mut entries = Vec::new();

        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            let Ok(obj) = serde_json::from_str::<serde_json::Value>(line) else {
                continue;
            };
            let Some(ts_str) = obj.get("timestamp").and_then(|v| v.as_str()) else {
                continue;
            };
            let Ok(ts) = DateTime::parse_from_rfc3339(ts_str) else {
                continue;
            };
            let ts = ts.with_timezone(&Utc);
            if ts < since {
                continue;
            }
            let app = obj
                .get("app")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_owned();
            let title = obj
                .get("title")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_owned();
            entries.push((ts, app, title));
        }

        Ok(entries)
    }
}

fn make_window_event(
    timestamp: DateTime<Utc>,
    app: &str,
    title: &str,
    duration_seconds: i64,
) -> Event {
    let label = if title.is_empty() { app } else { title };
    let raw = format!("{app}: {label} ({duration_seconds}s)");

    let mut e = Event::new(EventType::Window, raw);
    e.timestamp = timestamp;
    e.source = Some("window".to_owned());
    e.metadata.insert("app_name".into(), serde_json::json!(app));
    e.metadata
        .insert("window_title".into(), serde_json::json!(title));
    e.metadata.insert(
        "duration_seconds".into(),
        serde_json::json!(duration_seconds),
    );
    e
}

fn trim_jsonl(path: &std::path::Path, max_rows: usize) -> Result<()> {
    let content = std::fs::read_to_string(path)?;
    let lines: Vec<&str> = content.lines().collect();
    if lines.len() <= max_rows {
        return Ok(());
    }
    let keep = &lines[lines.len() - max_rows..];
    let tmp = path.with_extension("jsonl.tmp");
    std::fs::write(&tmp, keep.join("\n") + "\n")?;
    std::fs::rename(tmp, path)?;
    Ok(())
}
