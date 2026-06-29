//! Rust side of the `observer` Swift helper: spawns it and turns its JSON
//! trigger lines into a stream of `Trigger`s on a channel.

use crate::triggers::{Trigger, TriggerKind};
use anyhow::{anyhow, Context, Result};
use chrono::{DateTime, Utc};
use std::io::{BufRead, BufReader};
use std::path::{Path, PathBuf};
use std::process::{Child, Command, Stdio};
use tokio::sync::mpsc::UnboundedSender;

/// Owns the long-lived `observer` process and a thread draining its stdout.
pub struct ObserverHandle {
    child: Child,
}

impl ObserverHandle {
    pub fn available(binary: &Path) -> bool {
        binary.exists()
    }

    /// Spawn the observer, forwarding parsed triggers to `tx`. The reader thread
    /// exits when the process closes its stdout (crash/exit); the caller can fall
    /// back to polling on that signal.
    pub fn spawn(binary: PathBuf, tx: UnboundedSender<Trigger>) -> Result<Self> {
        let mut child = Command::new(&binary)
            .stdin(Stdio::null())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .with_context(|| format!("spawning observer {binary:?}"))?;

        let stdout = child.stdout.take().ok_or_else(|| anyhow!("no stdout"))?;
        std::thread::spawn(move || {
            for line in BufReader::new(stdout).lines() {
                let Ok(line) = line else { break };
                if let Some(trigger) = parse_line(&line) {
                    if tx.send(trigger).is_err() {
                        break; // receiver dropped
                    }
                }
            }
            tracing::warn!("observer stdout closed; event triggers paused");
        });

        Ok(Self { child })
    }
}

impl Drop for ObserverHandle {
    fn drop(&mut self) {
        let _ = self.child.kill();
        let _ = self.child.wait();
    }
}

fn parse_line(line: &str) -> Option<Trigger> {
    let v: serde_json::Value = serde_json::from_str(line.trim()).ok()?;
    let kind = TriggerKind::parse(v.get("type")?.as_str()?)?;
    let app_name = v
        .get("app_name")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string();
    let window_title = v
        .get("window_title")
        .and_then(|x| x.as_str())
        .unwrap_or("")
        .to_string();
    let timestamp = v
        .get("timestamp")
        .and_then(|x| x.as_str())
        .and_then(|s| DateTime::parse_from_rfc3339(s).ok())
        .map(|t| t.with_timezone(&Utc))
        .unwrap_or_else(Utc::now);
    Some(Trigger {
        kind,
        app_name,
        window_title,
        timestamp,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_observer_json() {
        let t = parse_line(
            r#"{"type":"app_switch","app_name":"Arc","window_title":"GitHub","timestamp":"2026-06-05T03:14:25.810Z"}"#,
        )
        .unwrap();
        assert_eq!(t.kind, TriggerKind::AppSwitch);
        assert_eq!(t.app_name, "Arc");
        assert_eq!(t.window_title, "GitHub");
    }

    #[test]
    fn rejects_unknown_and_malformed() {
        assert!(parse_line(r#"{"type":"nope"}"#).is_none());
        assert!(parse_line("not json").is_none());
    }
}
