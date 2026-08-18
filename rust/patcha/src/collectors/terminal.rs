use crate::{
    collectors::guard::CollectorGuard,
    models::{Event, EventType},
};
use chrono::{DateTime, Utc};

pub struct TerminalCollector {
    bash_guard: CollectorGuard,
    zsh_guard: CollectorGuard,
    fish_guard: CollectorGuard,
}

impl Default for TerminalCollector {
    fn default() -> Self {
        Self::new()
    }
}

impl TerminalCollector {
    pub fn new() -> Self {
        Self {
            bash_guard: CollectorGuard::new("bash_history", 3),
            zsh_guard: CollectorGuard::new("zsh_history", 3),
            fish_guard: CollectorGuard::new("fish_history", 3),
        }
    }

    pub fn collect_all(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        let mut events = Vec::new();
        events.extend(self.collect_bash(since));
        events.extend(self.collect_zsh(since));
        events.extend(self.collect_fish(since));
        events.sort_by(|a, b| b.timestamp.cmp(&a.timestamp));
        events
    }

    // -----------------------------------------------------------------------
    // Bash — no timestamps; use file mtime, take last 10 for incremental runs
    // -----------------------------------------------------------------------

    fn collect_bash(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        if !self.bash_guard.ok() {
            return Vec::new();
        }
        let path = dirs::home_dir().unwrap_or_default().join(".bash_history");
        if !path.exists() {
            return Vec::new();
        }

        match self.collect_bash_inner(&path, since) {
            Ok(events) => {
                self.bash_guard.success();
                events
            }
            Err(e) => {
                self.bash_guard.fail(&e);
                Vec::new()
            }
        }
    }

    fn collect_bash_inner(
        &self,
        path: &std::path::Path,
        since: Option<DateTime<Utc>>,
    ) -> anyhow::Result<Vec<Event>> {
        let meta = std::fs::metadata(path)?;
        let mtime = DateTime::<Utc>::from(meta.modified()?);

        if let Some(since) = since {
            if mtime < since {
                return Ok(Vec::new());
            }
        }

        let content = std::fs::read_to_string(path)?;
        let mut lines: Vec<&str> = content
            .lines()
            .filter(|l| !l.is_empty() && !l.starts_with('#'))
            .collect();

        // Since bash has no timestamps, heuristically take last 10 for incremental
        if since.is_some() && lines.len() > 10 {
            lines = lines[lines.len() - 10..].to_vec();
        }

        let events = lines
            .iter()
            .map(|cmd| make_terminal_event(mtime, cmd.trim(), "bash"))
            .collect();

        Ok(events)
    }

    // -----------------------------------------------------------------------
    // Zsh — `: <unix_ts>:<elapsed>;<command>` format
    // -----------------------------------------------------------------------

    fn collect_zsh(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        if !self.zsh_guard.ok() {
            return Vec::new();
        }
        let path = dirs::home_dir().unwrap_or_default().join(".zsh_history");
        if !path.exists() {
            return Vec::new();
        }

        match self.collect_zsh_inner(&path, since) {
            Ok(events) => {
                self.zsh_guard.success();
                events
            }
            Err(e) => {
                self.zsh_guard.fail(&e);
                Vec::new()
            }
        }
    }

    fn collect_zsh_inner(
        &self,
        path: &std::path::Path,
        since: Option<DateTime<Utc>>,
    ) -> anyhow::Result<Vec<Event>> {
        // zsh history can contain non-UTF-8 bytes (null separators between multiline cmds)
        let bytes = std::fs::read(path)?;
        let content = String::from_utf8_lossy(&bytes);

        let mut events = Vec::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() || !line.starts_with(": ") {
                continue;
            }
            // Format: ": <ts>:<elapsed>;<command>"
            let Some((meta, cmd)) = line[2..].split_once(';') else {
                continue;
            };
            let ts_str = meta.split(':').next().unwrap_or("").trim();
            let Ok(unix_ts) = ts_str.parse::<i64>() else {
                continue;
            };
            let Some(timestamp) = DateTime::from_timestamp(unix_ts, 0) else {
                continue;
            };

            if let Some(since) = since {
                if timestamp < since {
                    continue;
                }
            }

            if !cmd.trim().is_empty() {
                events.push(make_terminal_event(timestamp, cmd.trim(), "zsh"));
            }
        }

        Ok(events)
    }

    // -----------------------------------------------------------------------
    // Fish — `- cmd: <cmd>\n  when: <ts>` blocks
    // -----------------------------------------------------------------------

    fn collect_fish(&mut self, since: Option<DateTime<Utc>>) -> Vec<Event> {
        if !self.fish_guard.ok() {
            return Vec::new();
        }
        let path = dirs::home_dir()
            .unwrap_or_default()
            .join(".local/share/fish/fish_history");
        if !path.exists() {
            return Vec::new();
        }

        match self.collect_fish_inner(&path, since) {
            Ok(events) => {
                self.fish_guard.success();
                events
            }
            Err(e) => {
                self.fish_guard.fail(&e);
                Vec::new()
            }
        }
    }

    fn collect_fish_inner(
        &self,
        path: &std::path::Path,
        since: Option<DateTime<Utc>>,
    ) -> anyhow::Result<Vec<Event>> {
        let content = std::fs::read_to_string(path)?;
        let mut events = Vec::new();

        for block in content.split("- cmd: ").skip(1) {
            let mut lines = block.lines();
            let cmd = lines.next().unwrap_or("").trim();
            if cmd.is_empty() {
                continue;
            }

            let mut timestamp = Utc::now();
            for line in lines {
                let line = line.trim();
                if let Some(ts_str) = line.strip_prefix("when: ") {
                    if let Ok(unix_ts) = ts_str.trim().parse::<i64>() {
                        if let Some(dt) = DateTime::from_timestamp(unix_ts, 0) {
                            timestamp = dt;
                        }
                    }
                    break;
                }
            }

            if let Some(since) = since {
                if timestamp < since {
                    continue;
                }
            }

            events.push(make_terminal_event(timestamp, cmd, "fish"));
        }

        Ok(events)
    }
}

// ---------------------------------------------------------------------------
// Helper
// ---------------------------------------------------------------------------

fn make_terminal_event(timestamp: DateTime<Utc>, command: &str, shell: &str) -> Event {
    let raw = serde_json::json!({
        "command": command,
        "timestamp": timestamp.to_rfc3339(),
        "working_dir": std::env::current_dir()
            .map(|p| p.to_string_lossy().into_owned())
            .unwrap_or_default(),
    });

    let mut e = Event::new(EventType::Terminal, raw.to_string());
    e.timestamp = timestamp;
    e.source = Some(shell.to_owned());
    e.metadata.insert("shell".into(), serde_json::json!(shell));
    e.metadata
        .insert("command_length".into(), serde_json::json!(command.len()));
    e
}
