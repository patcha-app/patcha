use crate::{
    db::store::VectorStore,
    llm::{client::PatchaApiClient, prompts},
    models::{Event, TimelineHourSummary, TimelineTreeNode},
};
use anyhow::{Context, Result};
use chrono::{Timelike, Utc};
use serde::Deserialize;
use std::collections::HashMap;
use std::sync::Arc;

const MODEL: &str = "gpt-4o-mini";
/// How many summaries to generate in a single daemon cycle. In steady state
/// only one new hour elapses per hour, so this is just a backfill ceiling that
/// keeps a cold start from issuing a burst of LLM calls.
const MAX_PER_CYCLE: usize = 4;
/// Cap on raw activity lines fed into the prompt for one hour.
const MAX_LINES: usize = 24;

/// Generates per-hour timeline narratives by calling the completion API, and
/// persists them so the `/api/timeline` endpoint can serve LLM summaries.
pub struct HourlySummarizer {
    vector_store: Arc<VectorStore>,
    llm_client: Arc<PatchaApiClient>,
}

#[derive(Deserialize)]
struct HourlyResponse {
    title: String,
    narrative: String,
    #[serde(default)]
    tree: Option<TimelineTreeNode>,
}

impl HourlySummarizer {
    pub fn new(vector_store: Arc<VectorStore>, llm_client: Arc<PatchaApiClient>) -> Self {
        Self {
            vector_store,
            llm_client,
        }
    }

    /// Summarize any elapsed hours that don't yet have a stored summary. Works
    /// in UTC to match how events are bucketed (date column + hour are both UTC).
    /// Idempotent: hours already present in `timeline_summaries` are skipped.
    pub async fn run_pending(&self) -> Result<usize> {
        let now = Utc::now();
        let today = now.date_naive();
        let yesterday = today - chrono::Duration::days(1);

        let mut written = 0usize;
        // (date, exclusive upper hour bound) — yesterday is fully elapsed.
        let targets = [
            (yesterday.format("%Y-%m-%d").to_string(), 24u32),
            (today.format("%Y-%m-%d").to_string(), now.hour()),
        ];

        for (date_str, max_hour) in targets {
            if written >= MAX_PER_CYCLE {
                break;
            }
            let events = self.vector_store.get_events_by_date(&date_str)?;
            if events.is_empty() {
                continue;
            }
            let existing = self.vector_store.summarized_hours(&date_str)?;
            let buckets = bucket_by_hour(&events);

            let mut hours: Vec<u32> = buckets.keys().copied().collect();
            hours.sort_unstable();

            for hour in hours {
                if hour >= max_hour || existing.contains(&hour) {
                    continue;
                }
                if written >= MAX_PER_CYCLE {
                    break;
                }
                let bucket_events = &buckets[&hour];
                match self.generate(&date_str, hour, bucket_events).await {
                    Ok(summary) => {
                        self.vector_store.upsert_timeline_summary(&summary)?;
                        written += 1;
                        tracing::info!(date = %date_str, hour, "timeline summary generated");
                    }
                    Err(e) => {
                        // Likely auth/network — leave the hour unsummarized so it
                        // retries next cycle, and stop hammering a down API.
                        tracing::warn!(date = %date_str, hour, error = %e, "hourly summary failed");
                        return Ok(written);
                    }
                }
            }
        }

        Ok(written)
    }

    async fn generate(
        &self,
        date: &str,
        hour: u32,
        events: &[&Event],
    ) -> Result<TimelineHourSummary> {
        // Aggregate apps (by frequency), categories, and activity lines.
        let mut app_counts: HashMap<String, u32> = HashMap::new();
        let mut categories: Vec<String> = Vec::new();
        let mut lines: Vec<String> = Vec::new();

        for event in events {
            if let Some(app) = event.metadata.get("app_name").and_then(|v| v.as_str()) {
                *app_counts.entry(app.to_owned()).or_insert(0) += 1;
            }
            if let Some(cat) = &event.category {
                let cs = cat.to_string();
                if !categories.contains(&cs) {
                    categories.push(cs);
                }
            }
            if lines.len() < MAX_LINES {
                let line = event
                    .summary
                    .clone()
                    .unwrap_or_else(|| event.raw_content.chars().take(120).collect());
                if !line.trim().is_empty() {
                    lines.push(line);
                }
            }
        }

        let mut apps: Vec<(String, u32)> = app_counts.into_iter().collect();
        apps.sort_by_key(|(_, c)| std::cmp::Reverse(*c));
        let app_names: Vec<String> = apps.into_iter().map(|(k, _)| k).collect();
        let event_count = events.len() as u32;

        let parsed = self
            .call_llm(hour, &app_names, event_count, &lines)
            .await?;

        Ok(TimelineHourSummary {
            date: date.to_owned(),
            hour,
            title: parsed.title,
            summary: parsed.narrative,
            tree: parsed.tree,
            apps: app_names,
            categories,
            event_count,
            generated_at: Utc::now(),
        })
    }

    async fn call_llm(
        &self,
        hour: u32,
        apps: &[String],
        event_count: u32,
        lines: &[String],
    ) -> Result<HourlyResponse> {
        let user_prompt = prompts::render_user(
            "hourly_summary",
            serde_json::json!({
                "hour_label": hour_label(hour),
                "app_list": apps.join(", "),
                "event_count": event_count,
                "summaries": lines,
            }),
        )?;
        let system = prompts::render_system().unwrap_or_default();

        let raw = self
            .llm_client
            .chat_completion(&system, &user_prompt, MODEL)
            .await?;

        parse_response(&raw).context("failed to parse hourly summary JSON")
    }
}

/// Group a date's events by their UTC hour, mirroring the timeline endpoint.
fn bucket_by_hour(events: &[Event]) -> HashMap<u32, Vec<&Event>> {
    let mut buckets: HashMap<u32, Vec<&Event>> = HashMap::new();
    for event in events {
        buckets.entry(event.timestamp.hour()).or_default().push(event);
    }
    buckets
}

fn hour_label(hour: u32) -> String {
    let (period, h12) = match hour {
        0 => ("AM", 12),
        1..=11 => ("AM", hour),
        12 => ("PM", 12),
        _ => ("PM", hour - 12),
    };
    format!("{h12} {period}")
}

/// Parse the model's JSON, tolerating ```json fences or surrounding prose.
fn parse_response(raw: &str) -> Result<HourlyResponse> {
    let trimmed = raw.trim();
    let body = if let Some(start) = trimmed.find('{') {
        let end = trimmed.rfind('}').map(|e| e + 1).unwrap_or(trimmed.len());
        &trimmed[start..end]
    } else {
        trimmed
    };
    Ok(serde_json::from_str(body)?)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_fenced_json_with_tree() {
        let raw = "Here you go:\n```json\n{\"title\":\"Built timeline\",\"narrative\":\"Worked on the timeline view.\",\"tree\":{\"label\":\"Timeline\",\"children\":[{\"label\":\"Rail\"}]}}\n```";
        let parsed = parse_response(raw).unwrap();
        assert_eq!(parsed.title, "Built timeline");
        let tree = parsed.tree.unwrap();
        assert_eq!(tree.label, "Timeline");
        assert_eq!(tree.children.len(), 1);
        assert_eq!(tree.children[0].label, "Rail");
        assert!(tree.children[0].children.is_empty());
    }

    #[test]
    fn parses_plain_json_without_tree() {
        let raw = "{\"title\":\"Inbox\",\"narrative\":\"Cleared email.\"}";
        let parsed = parse_response(raw).unwrap();
        assert_eq!(parsed.narrative, "Cleared email.");
        assert!(parsed.tree.is_none());
    }

    #[test]
    fn hour_label_formats_12h() {
        assert_eq!(hour_label(0), "12 AM");
        assert_eq!(hour_label(9), "9 AM");
        assert_eq!(hour_label(12), "12 PM");
        assert_eq!(hour_label(15), "3 PM");
    }

    #[test]
    fn hourly_summary_prompt_renders() {
        let out = prompts::render_user(
            "hourly_summary",
            serde_json::json!({
                "hour_label": "9 AM",
                "app_list": "Xcode, Safari",
                "event_count": 12,
                "summaries": ["edited TimelinePane.swift", "read docs"],
            }),
        )
        .unwrap();
        assert!(out.contains("9 AM"));
        assert!(out.contains("JSON"));
    }
}
