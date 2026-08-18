use crate::{
    db::{vec_to_bytes, Db},
    models::{Category, Event, EventType, TimelineHourSummary, TimelineTreeNode},
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use rusqlite::params;
use serde_json::Value;
use std::collections::HashMap;
use std::sync::Arc;

pub struct VectorStore {
    db: Arc<Db>,
}

/// Optional filters for event queries.
#[derive(Debug, Default, Clone)]
pub struct EventFilter {
    pub date: Option<String>,
    /// Inclusive lower bound on the event date (YYYY-MM-DD).
    pub date_from: Option<String>,
    /// Inclusive upper bound on the event date (YYYY-MM-DD).
    pub date_to: Option<String>,
    pub event_type: Option<EventType>,
    pub category: Option<Category>,
    pub project: Option<String>,
    pub app: Option<String>,
    pub source: Option<String>,
    pub compacted: Option<bool>,
}

impl EventFilter {
    /// True when no filter field is set (lets callers skip the per-row check).
    pub fn is_empty(&self) -> bool {
        self.date.is_none()
            && self.date_from.is_none()
            && self.date_to.is_none()
            && self.event_type.is_none()
            && self.category.is_none()
            && self.project.is_none()
            && self.app.is_none()
            && self.source.is_none()
    }
}

/// Event returned from a vector search, with similarity score.
#[derive(Debug, Clone)]
pub struct ScoredEvent {
    pub event: Event,
    pub score: f64,
}

impl VectorStore {
    pub fn new(db: Arc<Db>) -> Self {
        Self { db }
    }

    // -----------------------------------------------------------------------
    // Write
    // -----------------------------------------------------------------------

    pub fn store_event(&self, event: &Event) -> Result<()> {
        let conn = self.db.conn();
        let metadata = serde_json::to_string(&event.metadata)?;
        let category = event.category.as_ref().map(|c| c.to_string());
        let event_type = event.event_type.to_string();

        conn.execute(
            "INSERT OR REPLACE INTO events
             (id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata, compacted)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 0)",
            params![
                event.id,
                event.timestamp.to_rfc3339(),
                event.date_str(),
                event_type,
                event.source,
                event.raw_content,
                event.summary,
                category,
                event.project,
                event.task_id,
                metadata,
            ],
        )?;

        if let Some(emb) = &event.embedding {
            let bytes = vec_to_bytes(emb);
            conn.execute(
                "INSERT OR REPLACE INTO vec_events(event_id, embedding) VALUES (?1, ?2)",
                params![event.id, bytes],
            )?;
        }

        Ok(())
    }

    pub fn store_events(&self, events: &[Event]) -> Result<()> {
        let conn = self.db.conn();
        let mut event_stmt = conn.prepare_cached(
            "INSERT OR REPLACE INTO events
             (id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata, compacted)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10, ?11, 0)",
        )?;
        let mut vec_stmt = conn.prepare_cached(
            "INSERT OR REPLACE INTO vec_events(event_id, embedding) VALUES (?1, ?2)",
        )?;

        for event in events {
            let metadata = serde_json::to_string(&event.metadata)?;
            let category = event.category.as_ref().map(|c| c.to_string());

            event_stmt.execute(params![
                event.id,
                event.timestamp.to_rfc3339(),
                event.date_str(),
                event.event_type.to_string(),
                event.source,
                event.raw_content,
                event.summary,
                category,
                event.project,
                event.task_id,
                metadata,
            ])?;

            if let Some(emb) = &event.embedding {
                vec_stmt.execute(params![event.id, vec_to_bytes(emb)])?;
            }
        }

        Ok(())
    }

    pub fn update_event_summary(
        &self,
        event_id: &str,
        summary: &str,
        category: Option<&Category>,
    ) -> Result<()> {
        let conn = self.db.conn();
        conn.execute(
            "UPDATE events SET summary = ?1, category = ?2 WHERE id = ?3",
            params![summary, category.map(|c| c.to_string()), event_id],
        )?;
        Ok(())
    }

    pub fn update_event_embedding(&self, event_id: &str, embedding: &[f32]) -> Result<()> {
        let conn = self.db.conn();
        conn.execute(
            "INSERT OR REPLACE INTO vec_events(event_id, embedding) VALUES (?1, ?2)",
            params![event_id, vec_to_bytes(embedding)],
        )?;
        Ok(())
    }

    pub fn mark_events_compacted(&self, event_ids: &[String]) -> Result<()> {
        if event_ids.is_empty() {
            return Ok(());
        }
        let conn = self.db.conn();
        for chunk in event_ids.chunks(SQL_ID_CHUNK) {
            let placeholders = (1..=chunk.len())
                .map(|i| format!("?{i}"))
                .collect::<Vec<_>>()
                .join(", ");
            let sql = format!("UPDATE events SET compacted = 1 WHERE id IN ({placeholders})");
            conn.execute(&sql, rusqlite::params_from_iter(chunk.iter()))?;
        }
        Ok(())
    }

    /// Hard-delete a day's events. No longer used by compaction (which now keeps
    /// raw events retrievable); retained for an optional future retention/prune job.
    #[allow(dead_code)]
    pub fn delete_events_by_date(&self, date: &str) -> Result<usize> {
        let conn = self.db.conn();
        // Remove from vec table first (no FK cascade on virtual tables)
        conn.execute(
            "DELETE FROM vec_events WHERE event_id IN (SELECT id FROM events WHERE date = ?1)",
            params![date],
        )?;
        let n = conn.execute("DELETE FROM events WHERE date = ?1", params![date])?;
        Ok(n)
    }

    // -----------------------------------------------------------------------
    // Read: vector search
    // -----------------------------------------------------------------------

    /// KNN search over stored event embeddings.
    pub fn search_events(
        &self,
        embedding: &[f32],
        limit: usize,
        filter: Option<&EventFilter>,
    ) -> Result<Vec<ScoredEvent>> {
        if limit == 0 {
            return Ok(Vec::new());
        }
        let conn = self.db.conn();
        // Stored embeddings are unit-length (fastembed normalizes); queries may
        // not be (e.g. composite averages), so normalize here to keep the
        // L2-to-cosine conversion below exact.
        let bytes = vec_to_bytes(&normalized(embedding));

        // Selective filters are applied post-KNN, so over-fetch harder when one
        // is active to avoid starving the result set.
        let has_filter = filter.is_some_and(|f| !f.is_empty());
        let k = if has_filter { limit * 20 } else { limit * 4 };

        let mut vec_stmt = conn.prepare_cached(
            "SELECT event_id, distance
             FROM vec_events
             WHERE embedding MATCH ?1 AND k = ?2
             ORDER BY distance ASC",
        )?;

        let candidates: Vec<(String, f64)> = vec_stmt
            .query_map(params![bytes, k as i64], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, f64>(1)?))
            })?
            .collect::<std::result::Result<_, _>>()?;

        if candidates.is_empty() {
            return Ok(Vec::new());
        }

        let ids: Vec<String> = candidates.iter().map(|(id, _)| id.clone()).collect();
        let mut events_by_id = fetch_events_by_ids(&conn, &ids)?;

        let mut results = Vec::new();
        for (event_id, distance) in candidates {
            if results.len() >= limit {
                break;
            }
            let Some(event) = events_by_id.remove(&event_id) else {
                continue;
            };
            if has_filter && !event_matches_filter(&event, filter.unwrap()) {
                continue;
            }
            results.push(ScoredEvent {
                score: l2_distance_to_cosine(distance),
                event,
            });
        }

        Ok(results)
    }

    /// BM25 full-text keyword candidates for RRF (used by search_activity).
    /// Returns events ordered best-first by bm25 relevance. Includes compacted
    /// raw events so historical activity stays keyword-searchable. Applies the
    /// same metadata/time filter as the vector arm.
    pub fn fetch_fulltext_candidates(
        &self,
        query: &str,
        limit: usize,
        filter: Option<&EventFilter>,
    ) -> Result<Vec<Event>> {
        let Some(match_query) = build_fts_match_query(query) else {
            return Ok(Vec::new());
        };

        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT e.id, e.timestamp, e.date, e.event_type, e.source, e.raw_content,
                    e.summary, e.category, e.project, e.task_id, e.metadata
             FROM fts_events f
             JOIN events e ON e.id = f.event_id
             WHERE f.text MATCH ?1
             ORDER BY bm25(fts_events) ASC
             LIMIT ?2",
        )?;
        let events: Vec<Event> = stmt
            .query_map(params![match_query, limit as i64], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;

        Ok(match filter {
            Some(f) if !f.is_empty() => events
                .into_iter()
                .filter(|e| event_matches_filter(e, f))
                .collect(),
            _ => events,
        })
    }

    // -----------------------------------------------------------------------
    // Read: events with their stored embeddings (for dedup in retrieval)
    // -----------------------------------------------------------------------

    /// Fetch recent events joined with their vec_events embeddings.
    pub fn get_events_since_with_embeddings(
        &self,
        since: DateTime<Utc>,
        limit: usize,
    ) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT e.id, e.timestamp, e.date, e.event_type, e.source, e.raw_content,
                    e.summary, e.category, e.project, e.task_id, e.metadata,
                    v.embedding
             FROM events e
             LEFT JOIN vec_events v ON v.event_id = e.id
             WHERE e.timestamp >= ?1
             ORDER BY e.timestamp DESC
             LIMIT ?2",
        )?;

        let events = stmt
            .query_map(params![since.to_rfc3339(), limit as i64], |row| {
                let mut event = row_to_event_cols(row, 0)?;
                // embedding is stored as little-endian f32 bytes
                if let Ok(Some(bytes)) = row.get::<_, Option<Vec<u8>>>(11) {
                    if bytes.len() % 4 == 0 {
                        let emb: Vec<f32> = bytes
                            .chunks_exact(4)
                            .map(|b| f32::from_le_bytes([b[0], b[1], b[2], b[3]]))
                            .collect();
                        event.embedding = Some(emb);
                    }
                }
                Ok(event)
            })?
            .collect::<std::result::Result<_, _>>()?;

        Ok(events)
    }

    // -----------------------------------------------------------------------
    // Read: date/category/recent queries
    // -----------------------------------------------------------------------

    pub fn get_events_by_date(&self, date: &str) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE date = ?1 ORDER BY timestamp ASC",
        )?;
        let events = stmt
            .query_map(params![date], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
    }

    pub fn get_uncompacted_events_by_date(&self, date: &str) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE date = ?1 AND compacted = 0 ORDER BY timestamp ASC",
        )?;
        let events = stmt
            .query_map(params![date], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
    }

    // -----------------------------------------------------------------------
    // Timeline summaries (per-hour LLM narratives)
    // -----------------------------------------------------------------------

    pub fn upsert_timeline_summary(&self, s: &TimelineHourSummary) -> Result<()> {
        let conn = self.db.conn();
        let tree = match &s.tree {
            Some(t) => Some(serde_json::to_string(t)?),
            None => None,
        };
        conn.execute(
            "INSERT INTO timeline_summaries
                (date, hour, title, summary, tree, apps, categories, event_count, generated_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
             ON CONFLICT(date, hour) DO UPDATE SET
                title = excluded.title,
                summary = excluded.summary,
                tree = excluded.tree,
                apps = excluded.apps,
                categories = excluded.categories,
                event_count = excluded.event_count,
                generated_at = excluded.generated_at",
            params![
                s.date,
                s.hour,
                s.title,
                s.summary,
                tree,
                serde_json::to_string(&s.apps)?,
                serde_json::to_string(&s.categories)?,
                s.event_count,
                s.generated_at.to_rfc3339(),
            ],
        )?;
        Ok(())
    }

    pub fn get_timeline_summaries(&self, date: &str) -> Result<Vec<TimelineHourSummary>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT date, hour, title, summary, tree, apps, categories, event_count, generated_at
             FROM timeline_summaries WHERE date = ?1 ORDER BY hour ASC",
        )?;
        let rows = stmt
            .query_map(params![date], row_to_timeline_summary)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(rows)
    }

    /// The set of hours already summarized for a date (cheap existence check
    /// used by the hourly job to skip work).
    pub fn summarized_hours(&self, date: &str) -> Result<std::collections::HashSet<u32>> {
        let conn = self.db.conn();
        let mut stmt =
            conn.prepare_cached("SELECT hour FROM timeline_summaries WHERE date = ?1")?;
        let hours = stmt
            .query_map(params![date], |r| r.get::<_, u32>(0))?
            .collect::<std::result::Result<_, _>>()?;
        Ok(hours)
    }

    pub fn get_events_by_category(&self, category: &Category, limit: usize) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE category = ?1 ORDER BY timestamp DESC LIMIT ?2",
        )?;
        let events = stmt
            .query_map(params![category.to_string(), limit as i64], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
    }

    pub fn get_recent_events(&self, limit: usize) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE compacted = 0 ORDER BY timestamp DESC LIMIT ?1",
        )?;
        let events = stmt
            .query_map(params![limit as i64], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
    }

    pub fn get_events_since(&self, since: DateTime<Utc>, limit: usize) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE timestamp >= ?1 ORDER BY timestamp DESC LIMIT ?2",
        )?;
        let events = stmt
            .query_map(params![since.to_rfc3339(), limit as i64], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
    }

    pub fn get_events_since_for_app(
        &self,
        since: DateTime<Utc>,
        app: &str,
        limit: usize,
    ) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        // app is stored as metadata.app_name
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE timestamp >= ?1 AND json_extract(metadata, '$.app_name') = ?2
             ORDER BY timestamp DESC LIMIT ?3",
        )?;
        let events = stmt
            .query_map(params![since.to_rfc3339(), app, limit as i64], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
    }

    pub fn get_event_by_id(&self, event_id: &str) -> Result<Option<Event>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE id = ?1",
        )?;
        stmt.query_row(params![event_id], row_to_event).optional()
    }

    pub fn get_events_by_ids(&self, event_ids: &[String]) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        let mut by_id = fetch_events_by_ids(&conn, event_ids)?;
        Ok(event_ids.iter().filter_map(|id| by_id.remove(id)).collect())
    }
}

// ---------------------------------------------------------------------------
// Batch row fetch
// ---------------------------------------------------------------------------

/// SQLite bound-parameter budget per statement; chunk large id lists.
const SQL_ID_CHUNK: usize = 500;

fn fetch_events_by_ids(
    conn: &rusqlite::Connection,
    event_ids: &[String],
) -> Result<HashMap<String, Event>> {
    let mut by_id = HashMap::with_capacity(event_ids.len());
    for chunk in event_ids.chunks(SQL_ID_CHUNK) {
        let placeholders = (1..=chunk.len())
            .map(|i| format!("?{i}"))
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE id IN ({placeholders})"
        );
        let mut stmt = conn.prepare(&sql)?;
        let events = stmt
            .query_map(rusqlite::params_from_iter(chunk.iter()), row_to_event)?
            .collect::<std::result::Result<Vec<_>, _>>()?;
        for event in events {
            by_id.insert(event.id.clone(), event);
        }
    }
    Ok(by_id)
}

// ---------------------------------------------------------------------------
// Vector math
// ---------------------------------------------------------------------------

/// Return a unit-length copy of `v` (or the zero vector unchanged).
fn normalized(v: &[f32]) -> Vec<f32> {
    let norm = v.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm > 0.0 && (norm - 1.0).abs() > 1e-6 {
        v.iter().map(|x| x / norm).collect()
    } else {
        v.to_vec()
    }
}

/// Cosine similarity from the L2 distance between two unit vectors:
/// d^2 = 2 - 2*cos, so cos = 1 - d^2/2.
fn l2_distance_to_cosine(distance: f64) -> f64 {
    (1.0 - distance * distance / 2.0).clamp(-1.0, 1.0)
}

// ---------------------------------------------------------------------------
// Event filtering
// ---------------------------------------------------------------------------

/// Check an event against the optional metadata/time filters. The `compacted`
/// flag is intentionally not enforced here: it isn't carried on the in-memory
/// event and semantic search must include compacted raw events.
fn event_matches_filter(ev: &Event, filter: &EventFilter) -> bool {
    let date = ev.date_str();
    if let Some(ref d) = filter.date {
        if &date != d {
            return false;
        }
    }
    if let Some(ref from) = filter.date_from {
        if date.as_str() < from.as_str() {
            return false;
        }
    }
    if let Some(ref to) = filter.date_to {
        if date.as_str() > to.as_str() {
            return false;
        }
    }
    if let Some(ref et) = filter.event_type {
        if &ev.event_type != et {
            return false;
        }
    }
    if let Some(ref cat) = filter.category {
        if ev.category.as_ref() != Some(cat) {
            return false;
        }
    }
    if let Some(ref proj) = filter.project {
        if ev.project.as_deref() != Some(proj.as_str()) {
            return false;
        }
    }
    if let Some(ref src) = filter.source {
        if ev.source.as_deref() != Some(src.as_str()) {
            return false;
        }
    }
    if let Some(ref app) = filter.app {
        let ev_app = ev.metadata.get("app_name").and_then(|v| v.as_str());
        if ev_app != Some(app.as_str()) {
            return false;
        }
    }
    true
}

// ---------------------------------------------------------------------------
// FTS5 query builder
// ---------------------------------------------------------------------------

/// Turn a free-text query into a safe FTS5 MATCH expression: extract alphanumeric
/// terms, quote each (so punctuation/operators can't break MATCH syntax), and OR
/// them together. Returns None when the query has no usable terms.
fn build_fts_match_query(query: &str) -> Option<String> {
    let terms: Vec<String> = query
        .split(|c: char| !c.is_alphanumeric())
        .filter(|t| t.len() >= 2)
        .map(|t| format!("\"{}\"", t.to_lowercase()))
        .collect();

    if terms.is_empty() {
        None
    } else {
        Some(terms.join(" OR "))
    }
}

// ---------------------------------------------------------------------------
// Row deserializer
// ---------------------------------------------------------------------------

fn row_to_event(row: &rusqlite::Row) -> rusqlite::Result<Event> {
    row_to_event_cols(row, 0)
}

fn row_to_event_cols(row: &rusqlite::Row, offset: usize) -> rusqlite::Result<Event> {
    use std::str::FromStr;

    let id: String = row.get(offset)?;
    let timestamp_str: String = row.get(offset + 1)?;
    let _date: String = row.get(offset + 2)?;
    let type_str: String = row.get(offset + 3)?;
    let source: Option<String> = row.get(offset + 4)?;
    let raw_content: String = row.get(offset + 5)?;
    let summary: Option<String> = row.get(offset + 6)?;
    let category_str: Option<String> = row.get(offset + 7)?;
    let project: Option<String> = row.get(offset + 8)?;
    let task_id: Option<String> = row.get(offset + 9)?;
    let metadata_str: String = row.get(offset + 10)?;

    let timestamp = DateTime::parse_from_rfc3339(&timestamp_str)
        .map(|dt| dt.with_timezone(&Utc))
        .unwrap_or_else(|_| Utc::now());

    let event_type = parse_event_type(&type_str);
    let category = category_str.and_then(|s| Category::from_str(&s).ok());
    let metadata: HashMap<String, Value> = serde_json::from_str(&metadata_str).unwrap_or_default();

    Ok(Event {
        id,
        timestamp,
        event_type,
        source,
        source_doc_id: None,
        raw_content,
        embedding: None,
        summary,
        category,
        project,
        task_id,
        metadata,
    })
}

fn row_to_timeline_summary(row: &rusqlite::Row) -> rusqlite::Result<TimelineHourSummary> {
    let date: String = row.get(0)?;
    let hour: u32 = row.get(1)?;
    let title: String = row.get(2)?;
    let summary: String = row.get(3)?;
    let tree_str: Option<String> = row.get(4)?;
    let apps_str: String = row.get(5)?;
    let categories_str: String = row.get(6)?;
    let event_count: u32 = row.get(7)?;
    let generated_at_str: String = row.get(8)?;

    let tree = tree_str
        .as_deref()
        .and_then(|s| serde_json::from_str::<TimelineTreeNode>(s).ok());
    let apps: Vec<String> = serde_json::from_str(&apps_str).unwrap_or_default();
    let categories: Vec<String> = serde_json::from_str(&categories_str).unwrap_or_default();
    let generated_at = DateTime::parse_from_rfc3339(&generated_at_str)
        .map(|dt| dt.with_timezone(&Utc))
        .unwrap_or_else(|_| Utc::now());

    Ok(TimelineHourSummary {
        date,
        hour,
        title,
        summary,
        tree,
        apps,
        categories,
        event_count,
        generated_at,
    })
}

fn parse_event_type(s: &str) -> EventType {
    match s {
        "git_commit" => EventType::GitCommit,
        "git_stash" => EventType::GitStash,
        "git_staged" => EventType::GitStaged,
        "browser" => EventType::Browser,
        "terminal" => EventType::Terminal,
        "window" => EventType::Window,
        "screen" => EventType::Screen,
        "note" => EventType::Note,
        _ => EventType::Window,
    }
}

// rusqlite `optional()` helper
trait OptionalExt<T> {
    fn optional(self) -> Result<Option<T>>;
}

impl<T> OptionalExt<T> for rusqlite::Result<T> {
    fn optional(self) -> Result<Option<T>> {
        match self {
            Ok(v) => Ok(Some(v)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(e.into()),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::db::Db;
    use crate::models::{Event, EventType};

    fn temp_store() -> VectorStore {
        let path = std::env::temp_dir().join(format!("patcha_test_{}.db", uuid::Uuid::new_v4()));
        let db = Db::open(&path).expect("open db");
        VectorStore::new(db)
    }

    #[test]
    fn fts_match_query_extracts_terms() {
        assert_eq!(
            build_fts_match_query("slack message about deploy rollback!"),
            Some("\"slack\" OR \"message\" OR \"about\" OR \"deploy\" OR \"rollback\"".to_string())
        );
        assert_eq!(build_fts_match_query("  ?? "), None);
    }

    #[test]
    fn fulltext_finds_multiword_query() {
        let store = temp_store();
        let mut hit = Event::new(
            EventType::Terminal,
            "{\"command\":\"git revert the deploy rollback\"}",
        );
        hit.source = Some("terminal".into());
        let miss = Event::new(EventType::Terminal, "{\"command\":\"ls -la\"}");
        store.store_event(&hit).unwrap();
        store.store_event(&miss).unwrap();

        // A natural-language query the old LIKE '%whole query%' arm would miss.
        let results = store
            .fetch_fulltext_candidates("deploy rollback message", 10, None)
            .unwrap();
        assert!(
            results.iter().any(|e| e.id == hit.id),
            "expected the matching event"
        );
        assert!(
            !results.iter().any(|e| e.id == miss.id),
            "unrelated event should not match"
        );
    }

    fn unit_vec(x: f32, y: f32) -> Vec<f32> {
        let mut v = vec![0.0f32; 768];
        v[0] = x;
        v[1] = y;
        v
    }

    fn embedded_event(content: &str, emb: Vec<f32>) -> Event {
        let mut e = Event::new(EventType::Terminal, content);
        e.embedding = Some(emb);
        e
    }

    #[test]
    fn knn_scores_are_cosine_similarity() {
        let store = temp_store();
        let a = embedded_event("exact", unit_vec(1.0, 0.0));
        let b = embedded_event("close", unit_vec(0.8, 0.6));
        let c = embedded_event("orthogonal", unit_vec(0.0, 1.0));
        store
            .store_events(&[a.clone(), b.clone(), c.clone()])
            .unwrap();

        let results = store.search_events(&unit_vec(1.0, 0.0), 3, None).unwrap();

        assert_eq!(results.len(), 3);
        assert_eq!(results[0].event.id, a.id);
        assert_eq!(results[1].event.id, b.id);
        assert_eq!(results[2].event.id, c.id);
        assert!(
            (results[0].score - 1.0).abs() < 1e-3,
            "identical vector: {}",
            results[0].score
        );
        assert!(
            (results[1].score - 0.8).abs() < 1e-3,
            "cos 0.8 vector: {}",
            results[1].score
        );
        assert!(
            results[2].score.abs() < 1e-3,
            "orthogonal vector: {}",
            results[2].score
        );
    }

    #[test]
    fn knn_normalizes_query_vectors() {
        let store = temp_store();
        let a = embedded_event("doc", unit_vec(1.0, 0.0));
        store.store_event(&a).unwrap();

        let results = store.search_events(&unit_vec(5.0, 0.0), 1, None).unwrap();

        assert_eq!(results.len(), 1);
        assert!(
            (results[0].score - 1.0).abs() < 1e-3,
            "unnormalized query must still yield cosine 1.0, got {}",
            results[0].score
        );
    }

    #[test]
    fn filtered_knn_search_scans_past_candidate_window() {
        let store = temp_store();
        let mut events = Vec::new();
        // 30 near-perfect matches for the wrong app dominate the KNN head.
        for i in 0..30 {
            let mut e = embedded_event(&format!("noise {i}"), unit_vec(1.0, 0.0));
            e.metadata
                .insert("app_name".into(), serde_json::json!("Xcode"));
            events.push(e);
        }
        for i in 0..5 {
            let mut e = embedded_event(&format!("target {i}"), unit_vec(0.6, 0.8));
            e.metadata
                .insert("app_name".into(), serde_json::json!("Terminal"));
            events.push(e);
        }
        store.store_events(&events).unwrap();

        let filter = EventFilter {
            app: Some("Terminal".into()),
            ..Default::default()
        };
        let results = store
            .search_events(&unit_vec(1.0, 0.0), 5, Some(&filter))
            .unwrap();

        assert_eq!(
            results.len(),
            5,
            "filter must not starve results when the KNN head is all noise"
        );
        assert!(results.iter().all(|r| {
            r.event.metadata.get("app_name").and_then(|v| v.as_str()) == Some("Terminal")
        }));
    }

    #[test]
    fn get_events_by_ids_preserves_request_order() {
        let store = temp_store();
        let a = Event::new(EventType::Terminal, "a");
        let b = Event::new(EventType::Terminal, "b");
        store.store_events(&[a.clone(), b.clone()]).unwrap();

        let events = store
            .get_events_by_ids(&[b.id.clone(), "missing".into(), a.id.clone()])
            .unwrap();
        let ids: Vec<&str> = events.iter().map(|e| e.id.as_str()).collect();
        assert_eq!(ids, vec![b.id.as_str(), a.id.as_str()]);
    }

    #[test]
    fn app_filter_rejects_like_wildcards() {
        let store = temp_store();
        let mut e = Event::new(EventType::Window, "window event");
        e.metadata
            .insert("app_name".into(), serde_json::json!("MyApp"));
        store.store_event(&e).unwrap();
        let since = Utc::now() - chrono::Duration::hours(1);

        let exact = store.get_events_since_for_app(since, "MyApp", 10).unwrap();
        assert_eq!(exact.len(), 1);

        let wildcard = store.get_events_since_for_app(since, "My%", 10).unwrap();
        assert!(wildcard.is_empty(), "LIKE wildcards must not match");

        let underscore = store.get_events_since_for_app(since, "My_pp", 10).unwrap();
        assert!(underscore.is_empty(), "underscore wildcard must not match");
    }

    #[test]
    fn compacted_events_stay_searchable() {
        let store = temp_store();
        let mut e = Event::new(
            EventType::Browser,
            "{\"title\":\"Postgres replication tuning\",\"domain\":\"docs.example.com\"}",
        );
        e.source = Some("browser".into());
        store.store_event(&e).unwrap();
        store.mark_events_compacted(&[e.id.clone()]).unwrap();

        let results = store
            .fetch_fulltext_candidates("postgres replication", 10, None)
            .unwrap();
        assert!(
            results.iter().any(|r| r.id == e.id),
            "compacted raw events must remain keyword-searchable"
        );
    }
}
