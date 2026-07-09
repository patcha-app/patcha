use crate::{
    config::Config,
    db::store::{EventFilter, VectorStore},
    embedding::{cosine_similarity, Embedder},
    models::{Event, EventType},
    rerank::CrossEncoderReranker,
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use std::collections::HashMap;

const RRF_K: f64 = 60.0;

/// Tunable parameters for hybrid activity search.
pub struct RetrievalParams {
    pub limit: usize,
    /// How many candidates each arm contributes before fusion/reranking.
    pub candidate_pool: usize,
    /// Metadata/time scoping (app, source, date range, ...).
    pub filter: EventFilter,
    /// Drop vector hits whose cosine similarity is below this floor.
    pub similarity_floor: f32,
    /// Weight of the recency term added to the fused score (0 disables it).
    pub recency_weight: f64,
    pub recency_lambda_days: f64,
}

impl RetrievalParams {
    pub fn from_config(cfg: &Config, limit: usize, app_filter: Option<&str>) -> Self {
        Self {
            limit,
            candidate_pool: cfg.rerank_candidate_pool.max(limit),
            filter: EventFilter {
                app: app_filter.map(|s| s.to_owned()),
                ..Default::default()
            },
            similarity_floor: cfg.similarity_floor,
            recency_weight: cfg.recency_weight,
            recency_lambda_days: cfg.recency_lambda_days.max(0.001),
        }
    }
}

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

pub async fn get_working_memory(
    store: &VectorStore,
    minutes: u32,
    dedup_threshold: f32,
) -> Result<String> {
    let since = Utc::now() - chrono::Duration::minutes(minutes as i64);
    let events = store.get_events_since_with_embeddings(since, 500)?;

    let deduped = dedup_by_similarity(events, dedup_threshold);
    let lines: Vec<String> = deduped.iter().map(format_line).collect();

    if lines.is_empty() {
        return Ok(format!("# Working memory (last {minutes}m)\nNo activity recorded."));
    }

    Ok(format!(
        "# Working memory (last {minutes}m)\n{}",
        lines.join("\n")
    ))
}

pub async fn get_recent_activity(
    store: &VectorStore,
    hours: u32,
    app_filter: Option<&str>,
    dedup_threshold: f32,
) -> Result<String> {
    let since = Utc::now() - chrono::Duration::hours(hours as i64);
    let mut events = store.get_events_since_with_embeddings(since, 1000)?;

    if let Some(app) = app_filter {
        events.retain(|e| {
            e.metadata
                .get("app_name")
                .and_then(|v| v.as_str())
                == Some(app)
        });
    }

    let deduped = dedup_by_similarity(events, dedup_threshold);
    let lines: Vec<String> = deduped.iter().map(format_line).collect();

    let app_tag = app_filter.map(|a| format!(" (app={a})")).unwrap_or_default();
    if lines.is_empty() {
        return Ok(format!("# Recent activity (last {hours}h){app_tag}\nNo activity recorded."));
    }

    Ok(format!(
        "# Recent activity (last {hours}h){app_tag}\n{}",
        lines.join("\n")
    ))
}

pub async fn search_activity(
    store: &VectorStore,
    embedder: &Embedder,
    reranker: Option<&CrossEncoderReranker>,
    query: &str,
    params: &RetrievalParams,
) -> Result<String> {
    let filter = (!params.filter.is_empty()).then(|| params.filter.clone());
    let pool = params.candidate_pool;

    // 1. Vector search (filtered), dropping hits below the similarity floor.
    let embedding = embedder.embed_query(query)?;
    let mut vector_results = store.search_events(&embedding, pool, filter.as_ref())?;
    if params.similarity_floor > 0.0 {
        vector_results.retain(|r| r.score as f32 >= params.similarity_floor);
    }

    // 2. Full-text keyword search (BM25-ranked by the FTS5 index).
    let ft_candidates = store.fetch_fulltext_candidates(query, pool, filter.as_ref())?;

    // 3. Fuse the two arms into a candidate pool (RRF + mild recency boost).
    let mut merged = if !ft_candidates.is_empty() {
        rrf_merge(&vector_results, &ft_candidates, params)
    } else {
        let now = Utc::now();
        let mut m: Vec<MergedResult> = vector_results
            .iter()
            .map(|r| MergedResult {
                id: r.event.id.clone(),
                score: r.score + recency_boost(&r.event.timestamp, now, params),
                event: r.event.clone(),
            })
            .collect();
        m.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap());
        m.truncate(pool);
        m
    };

    // 4. Cross-encoder rerank the pool for precision, then keep the top `limit`.
    rerank_in_place(reranker, query, &mut merged);
    merged.truncate(params.limit);

    let app_tag = params
        .filter
        .app
        .as_deref()
        .map(|a| format!(" (app={a})"))
        .unwrap_or_default();

    if merged.is_empty() {
        return Ok(format!("# Search results for \"{query}\"{app_tag}\nNo results found."));
    }

    let lines: Vec<String> = merged
        .iter()
        .map(|r| {
            let score = (r.score * 1000.0).round() / 1000.0;
            let ts = r.event.timestamp.format("%Y-%m-%d %H:%M").to_string();
            let detail = format_detail(&r.event);
            format!("[score={score} | {ts}] {}", strip_timestamp_prefix(&detail))
        })
        .collect();

    Ok(format!(
        "# Search results for \"{query}\"{app_tag}\n{}",
        lines.join("\n\n")
    ))
}

/// Exponential recency term: `weight * exp(-age_days / lambda)`, in [0, weight].
fn recency_boost(ts: &DateTime<Utc>, now: DateTime<Utc>, params: &RetrievalParams) -> f64 {
    if params.recency_weight <= 0.0 {
        return 0.0;
    }
    let age_days = (now - *ts).num_seconds().max(0) as f64 / 86_400.0;
    params.recency_weight * (-age_days / params.recency_lambda_days).exp()
}

// ---------------------------------------------------------------------------
// Deduplication by cosine similarity (per event type)
// ---------------------------------------------------------------------------

fn dedup_by_similarity(events: Vec<Event>, threshold: f32) -> Vec<Event> {
    let mut last_vec_by_type: HashMap<String, Vec<f32>> = HashMap::new();
    let mut kept: Vec<Event> = Vec::new();

    for event in events {
        let type_key = event.event_type.to_string();

        if let Some(emb) = &event.embedding {
            if let Some(last) = last_vec_by_type.get(&type_key) {
                let sim = cosine_similarity(last, emb);
                if sim >= threshold {
                    // Replace the most recent event of this type
                    if let Some(pos) = kept
                        .iter()
                        .rposition(|e| e.event_type.to_string() == type_key)
                    {
                        kept[pos] = event.clone();
                    }
                    last_vec_by_type.insert(type_key, emb.clone());
                    continue;
                }
            }
            last_vec_by_type.insert(type_key, emb.clone());
        }

        kept.push(event);
    }

    kept
}

// ---------------------------------------------------------------------------
// Formatting
// ---------------------------------------------------------------------------

fn format_line(event: &Event) -> String {
    let hhmm = event.timestamp.format("%Y-%m-%d %H:%M").to_string();
    let et = event.event_type.to_string();

    let detail = match event.event_type {
        EventType::Terminal => {
            serde_json::from_str::<serde_json::Value>(&event.raw_content)
                .ok()
                .and_then(|d| d.get("command").and_then(|v| v.as_str()).map(|s| s.chars().take(120).collect()))
                .unwrap_or_else(|| event.raw_content.chars().take(120).collect())
        }
        EventType::Browser => {
            if let Ok(data) = serde_json::from_str::<serde_json::Value>(&event.raw_content) {
                let title = data.get("title").and_then(|v| v.as_str()).unwrap_or("");
                let domain = data.get("domain").and_then(|v| v.as_str()).unwrap_or("");
                if !domain.is_empty() {
                    format!("{title} | {domain}")
                } else {
                    title.chars().take(80).collect()
                }
            } else {
                event.raw_content.chars().take(80).collect()
            }
        }
        EventType::GitCommit | EventType::GitStash => {
            if let Ok(data) = serde_json::from_str::<serde_json::Value>(&event.raw_content) {
                let msg = data.get("message").and_then(|v| v.as_str()).unwrap_or("");
                let files: Vec<&str> = data
                    .get("files_changed")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter().filter_map(|v| v.as_str()).take(5).collect())
                    .unwrap_or_default();
                if files.is_empty() {
                    msg.chars().take(120).collect()
                } else {
                    format!("{msg} | {}", files.join(", "))
                }
            } else {
                event.raw_content.chars().take(120).collect()
            }
        }
        EventType::GitStaged => event.raw_content.chars().take(120).collect(),
        EventType::Screen | EventType::Window => {
            let gist = event.metadata.get("gist").and_then(|v| v.as_str());
            if let Some(g) = gist {
                return format!("[{hhmm}] {et}: {g}");
            }
            let app = event.metadata.get("app_name").and_then(|v| v.as_str()).unwrap_or("");
            let title = event.metadata.get("window_title").and_then(|v| v.as_str()).unwrap_or("");
            if !title.is_empty() { format!("{app} — {title}") } else { app.to_owned() }
        }
        _ => event.raw_content.chars().take(120).collect(),
    };

    format!("[{hhmm}] {et}: {detail}")
}

fn format_detail(event: &Event) -> String {
    match event.event_type {
        EventType::GitCommit | EventType::GitStash => {
            if let Ok(data) = serde_json::from_str::<serde_json::Value>(&event.raw_content) {
                let msg = data.get("message").and_then(|v| v.as_str()).unwrap_or("").trim().to_owned();
                let files: Vec<&str> = data
                    .get("files_changed")
                    .and_then(|v| v.as_array())
                    .map(|arr| arr.iter().filter_map(|v| v.as_str()).collect())
                    .unwrap_or_default();
                let diff = data.get("diff").and_then(|v| v.as_str()).unwrap_or("");
                let proj = event.project.as_deref().map(|p| format!(" [{p}]")).unwrap_or_default();
                let ts = event.timestamp.format("%Y-%m-%d %H:%M").to_string();
                let mut out = format!("[{ts}] {}: {msg}{proj}", event.event_type);
                if !files.is_empty() {
                    out.push_str(&format!("\nFiles: {}", files.join(", ")));
                }
                if !diff.is_empty() {
                    out.push_str(&format!("\n\n{diff}"));
                }
                return out;
            }
            format_line(event)
        }
        EventType::Screen | EventType::Window => {
            let base = format_line(event);
            let raw = &event.raw_content;
            // Extract content after "AppName — WindowTitle: "
            let snippet: String = if let Some(pos) = raw.find(": ") {
                raw[pos + 2..].chars().take(300).collect()
            } else {
                raw.chars().take(300).collect()
            };
            if snippet.is_empty() {
                base
            } else {
                format!("{base}\n{snippet}")
            }
        }
        _ => format_line(event),
    }
}

fn strip_timestamp_prefix(s: &str) -> &str {
    // Strip "[YYYY-MM-DD HH:MM] " prefix if present
    if let Some(end) = s.find("] ") {
        &s[end + 2..]
    } else {
        s
    }
}

// ---------------------------------------------------------------------------
// Reciprocal Rank Fusion
// ---------------------------------------------------------------------------

#[derive(Clone)]
struct MergedResult {
    #[allow(dead_code)]
    id: String,
    score: f64,
    event: Event,
}

fn rrf_merge(
    vector_results: &[crate::db::store::ScoredEvent],
    text_results: &[Event],
    params: &RetrievalParams,
) -> Vec<MergedResult> {
    let mut scores: HashMap<String, (f64, Event)> = HashMap::new();

    for (rank, r) in vector_results.iter().enumerate() {
        let contribution = 1.0 / (RRF_K + rank as f64 + 1.0);
        let entry = scores.entry(r.event.id.clone()).or_insert((0.0, r.event.clone()));
        entry.0 += contribution;
    }

    for (rank, event) in text_results.iter().enumerate() {
        let contribution = 1.0 / (RRF_K + rank as f64 + 1.0);
        let entry = scores.entry(event.id.clone()).or_insert((0.0, event.clone()));
        entry.0 += contribution;
    }

    let now = Utc::now();
    let mut merged: Vec<MergedResult> = scores
        .into_iter()
        .map(|(id, (score, event))| {
            let score = score + recency_boost(&event.timestamp, now, params);
            MergedResult { id, score, event }
        })
        .collect();

    merged.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap());
    merged.truncate(params.candidate_pool);
    merged
}

// ---------------------------------------------------------------------------
// Cross-encoder reranking
// ---------------------------------------------------------------------------

/// Reorder the candidate pool by cross-encoder relevance. No-op when no reranker
/// is configured, the model files are absent, or inference fails (the fused order
/// is kept so a missing/broken model never breaks search).
fn rerank_in_place(
    reranker: Option<&CrossEncoderReranker>,
    query: &str,
    merged: &mut [MergedResult],
) {
    let Some(reranker) = reranker else { return };
    if merged.len() < 2 || !reranker.available() {
        return;
    }

    let docs: Vec<String> = merged.iter().map(|r| rerank_text(&r.event)).collect();
    let scores = match reranker.scores(query, &docs) {
        Ok(s) if s.len() == merged.len() => s,
        Ok(_) => return,
        Err(e) => {
            tracing::warn!(error = %e, "reranker failed; keeping fused order");
            return;
        }
    };

    let mut scored: Vec<(f32, MergedResult)> =
        scores.into_iter().zip(merged.iter().cloned()).collect();
    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());
    for (slot, (s, r)) in merged.iter_mut().zip(scored) {
        *slot = MergedResult { score: s as f64, ..r };
    }
}

/// Concise text representation of an event for the cross-encoder to judge.
fn rerank_text(event: &Event) -> String {
    let detail = format_detail(event);
    strip_timestamp_prefix(&detail).chars().take(2000).collect()
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::store::ScoredEvent;
    use crate::models::EventType;
    use chrono::Duration;

    fn event(id: &str, age_minutes: i64, content: &str) -> Event {
        let mut e = Event::new(EventType::Terminal, content);
        e.id = id.to_owned();
        e.timestamp = Utc::now() - Duration::minutes(age_minutes);
        e
    }

    fn params() -> RetrievalParams {
        RetrievalParams {
            limit: 10,
            candidate_pool: 40,
            filter: EventFilter::default(),
            similarity_floor: 0.0,
            recency_weight: 0.0,
            recency_lambda_days: 7.0,
        }
    }

    #[test]
    fn rrf_merge_rewards_agreement_across_arms() {
        let vector = vec![
            ScoredEvent { event: event("a", 0, "alpha"), score: 0.9 },
            ScoredEvent { event: event("b", 0, "beta"), score: 0.8 },
        ];
        // `b` appears top of the text arm too, so fusion should lift it above `a`.
        let text = vec![event("b", 0, "beta"), event("c", 0, "gamma")];

        let merged = rrf_merge(&vector, &text, &params());

        assert_eq!(merged[0].id, "b", "event in both arms should rank first");
        let ids: Vec<&str> = merged.iter().map(|m| m.id.as_str()).collect();
        assert!(ids.contains(&"a") && ids.contains(&"c"));
        assert_eq!(merged.len(), 3, "union of both arms, deduped by id");
    }

    #[test]
    fn rrf_merge_respects_candidate_pool_cap() {
        let vector: Vec<ScoredEvent> = (0..50)
            .map(|i| ScoredEvent { event: event(&format!("v{i}"), i, "x"), score: 1.0 })
            .collect();
        let mut p = params();
        p.candidate_pool = 5;

        let merged = rrf_merge(&vector, &[], &p);

        assert_eq!(merged.len(), 5);
    }

    #[test]
    fn recency_boost_is_zero_when_weight_disabled() {
        let p = params();
        let boost = recency_boost(&(Utc::now() - Duration::days(1)), Utc::now(), &p);
        assert_eq!(boost, 0.0);
    }

    #[test]
    fn recency_boost_decays_with_age() {
        let mut p = params();
        p.recency_weight = 1.0;
        let now = Utc::now();
        let fresh = recency_boost(&now, now, &p);
        let old = recency_boost(&(now - Duration::days(7)), now, &p);

        assert!((fresh - 1.0).abs() < 1e-9, "zero-age boost equals weight");
        assert!(old < fresh, "older events get a smaller boost");
        assert!((old - (1.0 / std::f64::consts::E)).abs() < 1e-6, "one lambda of age decays by 1/e");
    }

    #[test]
    fn recency_boost_clamps_future_timestamps() {
        let mut p = params();
        p.recency_weight = 1.0;
        let now = Utc::now();
        let future = recency_boost(&(now + Duration::days(3)), now, &p);
        assert!((future - 1.0).abs() < 1e-9, "negative age is clamped to 0");
    }

    #[test]
    fn dedup_collapses_near_identical_consecutive_events() {
        let mut a = event("a", 2, "first");
        a.embedding = Some(vec![1.0, 0.0, 0.0]);
        let mut b = event("b", 1, "near-dup");
        b.embedding = Some(vec![1.0, 0.0, 0.0]);
        let mut c = event("c", 0, "different");
        c.embedding = Some(vec![0.0, 1.0, 0.0]);

        let kept = dedup_by_similarity(vec![a, b, c], 0.95);
        let ids: Vec<&str> = kept.iter().map(|e| e.id.as_str()).collect();

        assert_eq!(ids, vec!["b", "c"], "duplicate replaced by most recent, distinct kept");
    }

    #[test]
    fn dedup_keeps_events_without_embeddings() {
        let kept = dedup_by_similarity(vec![event("a", 1, "x"), event("b", 0, "y")], 0.9);
        assert_eq!(kept.len(), 2);
    }

    #[test]
    fn strip_timestamp_prefix_removes_bracketed_stamp() {
        assert_eq!(
            strip_timestamp_prefix("[2026-06-20 09:30] did a thing"),
            "did a thing"
        );
        assert_eq!(strip_timestamp_prefix("no prefix here"), "no prefix here");
    }

    #[test]
    fn rerank_in_place_is_noop_without_a_reranker() {
        let mut merged = vec![
            MergedResult { id: "a".into(), score: 0.9, event: event("a", 0, "x") },
            MergedResult { id: "b".into(), score: 0.8, event: event("b", 0, "y") },
        ];
        rerank_in_place(None, "query", &mut merged);
        let ids: Vec<&str> = merged.iter().map(|m| m.id.as_str()).collect();
        assert_eq!(ids, vec!["a", "b"], "order preserved when no reranker present");
    }
}
