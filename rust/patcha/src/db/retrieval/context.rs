use crate::{
    db::store::VectorStore,
    embedding::{cosine_similarity, Embedder},
    models::{Event, EventType},
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use std::collections::HashMap;
use std::sync::Arc;

const RRF_K: f64 = 60.0;

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
    query: &str,
    limit: usize,
    app_filter: Option<&str>,
) -> Result<String> {
    // 1. Vector search
    let embedding = embedder.embed_one(query)?;
    let vector_results = store.search_events(&embedding, limit, None)?;

    // 2. Full-text keyword search
    let ft_candidates = store.fetch_fulltext_candidates(query, (limit * 5).min(100))?;
    let ft_ranked = tfidf_rank(query, ft_candidates);
    let ft_trimmed = ft_ranked.into_iter().take(limit).collect::<Vec<_>>();

    // 3. Reciprocal Rank Fusion
    let merged = if !ft_trimmed.is_empty() {
        rrf_merge(&vector_results, &ft_trimmed, limit)
    } else {
        vector_results
            .iter()
            .map(|r| MergedResult {
                id: r.event.id.clone(),
                score: r.score,
                event: r.event.clone(),
            })
            .collect()
    };

    if merged.is_empty() {
        let app_tag = app_filter.map(|a| format!(" (app={a})")).unwrap_or_default();
        return Ok(format!("# Search results for \"{query}\"{app_tag}\nNo results found."));
    }

    // Filter by app if requested
    let filtered: Vec<&MergedResult> = if let Some(app) = app_filter {
        merged
            .iter()
            .filter(|r| {
                r.event
                    .metadata
                    .get("app_name")
                    .and_then(|v| v.as_str())
                    == Some(app)
            })
            .collect()
    } else {
        merged.iter().collect()
    };

    let lines: Vec<String> = filtered
        .iter()
        .map(|r| {
            let score = (r.score * 1000.0).round() / 1000.0;
            let ts = r.event.timestamp.format("%Y-%m-%d %H:%M").to_string();
            let detail = format_detail(&r.event);
            format!("[score={score} | {ts}] {}", strip_timestamp_prefix(&detail))
        })
        .collect();

    let app_tag = app_filter.map(|a| format!(" (app={a})")).unwrap_or_default();
    Ok(format!(
        "# Search results for \"{query}\"{app_tag}\n{}",
        lines.join("\n\n")
    ))
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
// TF-IDF ranking
// ---------------------------------------------------------------------------

fn tfidf_rank(query: &str, mut candidates: Vec<Event>) -> Vec<Event> {
    if candidates.is_empty() {
        return Vec::new();
    }

    let corpus: Vec<String> = candidates
        .iter()
        .map(|e| e.raw_content.to_lowercase())
        .collect();

    if corpus.iter().all(|s| s.is_empty()) {
        return candidates;
    }

    // Build vocabulary from all documents + query
    let query_lower = query.to_lowercase();
    let all_docs: Vec<&str> = corpus.iter().map(|s| s.as_str()).chain(std::iter::once(query_lower.as_str())).collect();
    let n = all_docs.len(); // includes query as last doc

    // Tokenize (unigrams only for simplicity)
    let tokenize = |text: &str| -> Vec<String> {
        text.split_whitespace()
            .map(|w| w.trim_matches(|c: char| !c.is_alphanumeric()).to_lowercase())
            .filter(|w| w.len() >= 2)
            .collect()
    };

    let tokenized: Vec<Vec<String>> = all_docs.iter().map(|d| tokenize(d)).collect();

    // Document frequency
    let mut df: HashMap<String, usize> = HashMap::new();
    for tokens in &tokenized {
        let unique: std::collections::HashSet<&String> = tokens.iter().collect();
        for t in unique {
            *df.entry(t.clone()).or_insert(0) += 1;
        }
    }

    // TF-IDF for each doc and the query (last)
    let tfidf = |tokens: &[String]| -> HashMap<String, f64> {
        let mut tf: HashMap<String, usize> = HashMap::new();
        for t in tokens { *tf.entry(t.clone()).or_insert(0) += 1; }
        let total = tokens.len().max(1) as f64;
        tf.into_iter()
            .map(|(term, count)| {
                let idf = ((n as f64 + 1.0) / (*df.get(&term).unwrap_or(&1) as f64 + 1.0)).ln() + 1.0;
                let tf_val = (count as f64 / total).ln_1p(); // sublinear TF
                (term, tf_val * idf)
            })
            .collect()
    };

    let query_vec = tfidf(&tokenized[n - 1]);
    let doc_vecs: Vec<HashMap<String, f64>> = tokenized[..n - 1].iter().map(|t| tfidf(t)).collect();

    // Cosine similarity between query and each doc
    let query_norm: f64 = query_vec.values().map(|v| v * v).sum::<f64>().sqrt();

    let mut scored: Vec<(f64, usize)> = doc_vecs
        .iter()
        .enumerate()
        .map(|(i, dv)| {
            let dot: f64 = query_vec.iter()
                .filter_map(|(t, qv)| dv.get(t).map(|dv| qv * dv))
                .sum();
            let doc_norm: f64 = dv.values().map(|v| v * v).sum::<f64>().sqrt();
            let sim = if query_norm > 0.0 && doc_norm > 0.0 {
                dot / (query_norm * doc_norm)
            } else {
                0.0
            };
            (sim, i)
        })
        .collect();

    scored.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap());

    // Reorder candidates by score
    let mut result = Vec::with_capacity(candidates.len());
    let mut candidates_indexed: Vec<Option<Event>> = candidates.into_iter().map(Some).collect();
    for (_, idx) in scored {
        if let Some(e) = candidates_indexed[idx].take() {
            result.push(e);
        }
    }
    result
}

// ---------------------------------------------------------------------------
// Reciprocal Rank Fusion
// ---------------------------------------------------------------------------

struct MergedResult {
    id: String,
    score: f64,
    event: Event,
}

fn rrf_merge(
    vector_results: &[crate::db::store::ScoredEvent],
    text_results: &[Event],
    limit: usize,
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

    let mut merged: Vec<MergedResult> = scores
        .into_iter()
        .map(|(id, (score, event))| MergedResult { id, score, event })
        .collect();

    merged.sort_by(|a, b| b.score.partial_cmp(&a.score).unwrap());
    merged.truncate(limit);
    merged
}
