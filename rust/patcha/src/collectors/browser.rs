use crate::{
    collectors::{filters::is_banking_domain, guard::CollectorGuard},
    models::{Event, EventType},
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use rusqlite::Connection;
use std::path::PathBuf;

// Chrome/Arc store timestamps as microseconds since Windows epoch (1601-01-01).
const CHROME_EPOCH_OFFSET_US: i64 = 11_644_473_600_000_000;

pub struct BrowserCollector {
    chrome_path: PathBuf,
    arc_path: PathBuf,
    safari_path: PathBuf,
    chrome_guard: CollectorGuard,
    arc_guard: CollectorGuard,
    safari_guard: CollectorGuard,
}

impl BrowserCollector {
    pub fn new() -> Self {
        let home = dirs::home_dir().unwrap_or_default();
        Self {
            chrome_path: home.join("Library/Application Support/Google/Chrome/Default/History"),
            arc_path: home.join("Library/Application Support/Arc/User Data/Default/History"),
            safari_path: home.join("Library/Safari/History.db"),
            chrome_guard: CollectorGuard::new("chrome_history", 3),
            arc_guard: CollectorGuard::new("arc_history", 3),
            safari_guard: CollectorGuard::new("safari_history", 3),
        }
    }

    pub fn collect_all(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        let mut events = Vec::new();
        events.extend(self.collect_chrome(since));
        events.extend(self.collect_arc(since));
        events.extend(self.collect_safari(since));
        events.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));
        events
    }

    // -----------------------------------------------------------------------
    // Chrome / Arc (identical schema)
    // -----------------------------------------------------------------------

    fn collect_chrome(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        if !self.chrome_guard.ok() {
            return Vec::new();
        }
        match self.collect_chromium(&self.chrome_path.clone(), "chrome", since) {
            Ok(events) => {
                self.chrome_guard.success();
                events
            }
            Err(e) => {
                self.chrome_guard.fail(&e);
                Vec::new()
            }
        }
    }

    fn collect_arc(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        if !self.arc_guard.ok() {
            return Vec::new();
        }
        match self.collect_chromium(&self.arc_path.clone(), "arc", since) {
            Ok(events) => {
                self.arc_guard.success();
                events
            }
            Err(e) => {
                self.arc_guard.fail(&e);
                Vec::new()
            }
        }
    }

    fn collect_chromium(
        &self,
        db_path: &std::path::Path,
        browser: &str,
        since: Option<DateTime<Utc>>,
    ) -> Result<Vec<Event>> {
        if !db_path.exists() {
            return Ok(Vec::new());
        }

        let tmp = tempfile::NamedTempFile::new()?;
        std::fs::copy(db_path, tmp.path())?;

        let conn = Connection::open(tmp.path())?;

        // Chrome epoch: microseconds since 1601-01-01. Convert since → Chrome time.
        let since_chrome: i64 = since
            .map(|dt| dt.timestamp_micros() + CHROME_EPOCH_OFFSET_US)
            .unwrap_or_else(|| {
                let today = Utc::now()
                    .date_naive()
                    .and_hms_opt(0, 0, 0)
                    .unwrap()
                    .and_utc();
                today.timestamp_micros() + CHROME_EPOCH_OFFSET_US
            });

        let mut stmt = conn.prepare(
            "SELECT title, url, last_visit_time, visit_count
             FROM urls
             WHERE last_visit_time > ?1
             ORDER BY last_visit_time DESC
             LIMIT 1000",
        )?;

        let mut events = Vec::new();
        let rows = stmt.query_map([since_chrome], |row| {
            Ok((
                row.get::<_, Option<String>>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, i64>(2)?,
                row.get::<_, i64>(3)?,
            ))
        })?;

        for row in rows.flatten() {
            let (title, url, last_visit_time, visit_count) = row;
            let unix_us = last_visit_time - CHROME_EPOCH_OFFSET_US;
            let timestamp = DateTime::from_timestamp(unix_us / 1_000_000, 0)
                .unwrap_or_else(Utc::now);

            let domain = extract_domain(&url);
            if is_banking_domain(&domain) {
                continue;
            }

            let title = enhance_youtube_title(title.unwrap_or_else(|| "Untitled".into()), &url);

            events.push(make_browser_event(
                timestamp, &title, &url, &domain, browser, visit_count,
            ));
        }

        Ok(events)
    }

    // -----------------------------------------------------------------------
    // Safari
    // -----------------------------------------------------------------------

    fn collect_safari(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        if !self.safari_guard.ok() {
            return Vec::new();
        }
        match self.collect_safari_inner(since) {
            Ok(events) => {
                self.safari_guard.success();
                events
            }
            Err(e) => {
                let msg = format!(
                    "{} — grant Full Disk Access in System Settings → Privacy & Security",
                    e
                );
                self.safari_guard
                    .fail(&anyhow::anyhow!(msg));
                Vec::new()
            }
        }
    }

    fn collect_safari_inner(&self, since: Option<DateTime<Utc>>) -> Result<Vec<Event>> {
        if !self.safari_path.exists() {
            return Ok(Vec::new());
        }

        let tmp = tempfile::NamedTempFile::new()?;
        std::fs::copy(&self.safari_path, tmp.path())?;

        let conn = Connection::open(tmp.path())?;

        let since_ts: f64 = since
            .map(|dt| dt.timestamp() as f64)
            .unwrap_or_else(|| {
                Utc::now()
                    .date_naive()
                    .and_hms_opt(0, 0, 0)
                    .unwrap()
                    .and_utc()
                    .timestamp() as f64
            });

        let mut stmt = conn.prepare(
            "SELECT hv.title, hi.url, hv.visit_time
             FROM history_visits hv
             JOIN history_items hi ON hv.history_item = hi.id
             WHERE hv.visit_time > ?1
             ORDER BY hv.visit_time DESC
             LIMIT 1000",
        )?;

        let mut events = Vec::new();
        let rows = stmt.query_map([since_ts], |row| {
            Ok((
                row.get::<_, Option<String>>(0)?,
                row.get::<_, String>(1)?,
                row.get::<_, f64>(2)?,
            ))
        })?;

        for row in rows.flatten() {
            let (title, url, visit_time) = row;
            let timestamp =
                DateTime::from_timestamp(visit_time as i64, 0).unwrap_or_else(Utc::now);

            let domain = extract_domain(&url);
            if is_banking_domain(&domain) {
                continue;
            }

            let title = enhance_youtube_title(title.unwrap_or_else(|| "Untitled".into()), &url);
            events.push(make_browser_event(timestamp, &title, &url, &domain, "safari", 1));
        }

        Ok(events)
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn extract_domain(url: &str) -> String {
    if let Some(after_scheme) = url.split("//").nth(1) {
        after_scheme.split('/').next().unwrap_or("").to_owned()
    } else {
        url.split('/').next().unwrap_or("").to_owned()
    }
}

fn enhance_youtube_title(title: String, url: &str) -> String {
    if !url.contains("youtube.com/watch") && !url.contains("youtu.be/") {
        return title;
    }
    let re = regex::Regex::new(r"(?:v=|youtu\.be/|embed/)([a-zA-Z0-9_-]+)").unwrap();
    let video_id = match re.captures(url) {
        Some(caps) => caps[1].to_owned(),
        None => return title,
    };

    if title.is_empty() || title == "Untitled" || title.contains(&video_id) {
        return format!("YouTube Video (ID: {video_id})");
    }
    if title.to_lowercase().starts_with("youtube") || title.to_lowercase().starts_with("yt:") {
        return title;
    }
    format!("YouTube: {title}")
}

fn make_browser_event(
    timestamp: DateTime<Utc>,
    title: &str,
    url: &str,
    domain: &str,
    browser: &str,
    visit_count: i64,
) -> Event {
    let raw = serde_json::json!({
        "title": title,
        "url": url,
        "domain": domain,
        "timestamp": timestamp.to_rfc3339(),
    });

    let mut e = Event::new(EventType::Browser, raw.to_string());
    e.timestamp = timestamp;
    e.source = Some(browser.to_owned());
    e.metadata.insert("domain".into(), serde_json::json!(domain));
    e.metadata.insert("visit_count".into(), serde_json::json!(visit_count));
    e.metadata.insert("browser".into(), serde_json::json!(browser));
    e
}
