use crate::{
    db::{vec_to_bytes, Db},
    models::{Category, Event, EventType},
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
#[derive(Debug, Default)]
pub struct EventFilter {
    pub date: Option<String>,
    pub event_type: Option<EventType>,
    pub category: Option<Category>,
    pub project: Option<String>,
    pub app: Option<String>,
    pub compacted: Option<bool>,
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
        let placeholders = event_ids
            .iter()
            .enumerate()
            .map(|(i, _)| format!("?{}", i + 1))
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!("UPDATE events SET compacted = 1 WHERE id IN ({placeholders})");
        let params = rusqlite::params_from_iter(event_ids.iter());
        conn.execute(&sql, params)?;
        Ok(())
    }

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
        let conn = self.db.conn();
        let bytes = vec_to_bytes(embedding);
        let limit = limit as i64;

        // Get top-k candidates from vec table
        let mut vec_stmt = conn.prepare_cached(
            "SELECT event_id, distance
             FROM vec_events
             WHERE embedding MATCH ?1 AND k = ?2
             ORDER BY distance ASC",
        )?;

        let candidates: Vec<(String, f64)> = vec_stmt
            .query_map(params![bytes, limit * 4], |row| {
                Ok((row.get::<_, String>(0)?, row.get::<_, f64>(1)?))
            })?
            .collect::<std::result::Result<_, _>>()?;

        if candidates.is_empty() {
            return Ok(Vec::new());
        }

        // Fetch full event rows and apply filters
        let mut results = Vec::new();
        for (event_id, distance) in candidates {
            if results.len() >= limit as usize {
                break;
            }
            if let Some(event) = self.get_event_by_id_with_filter(&conn, &event_id, filter)? {
                results.push(ScoredEvent {
                    score: 1.0 - distance as f64, // convert distance to similarity
                    event,
                });
            }
        }

        Ok(results)
    }

    /// Full-text keyword candidates for RRF (used by search_activity).
    pub fn fetch_fulltext_candidates(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<Vec<Event>> {
        let conn = self.db.conn();
        let pattern = format!("%{}%", query.to_lowercase());
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events
             WHERE compacted = 0
               AND (lower(raw_content) LIKE ?1 OR lower(summary) LIKE ?1)
             ORDER BY timestamp DESC
             LIMIT ?2",
        )?;
        let events = stmt
            .query_map(params![pattern, limit as i64], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
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

    pub fn get_events_by_category(
        &self,
        category: &Category,
        limit: usize,
    ) -> Result<Vec<Event>> {
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
        let pattern = format!("%\"app_name\":\"{}\"%", app);
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE timestamp >= ?1 AND metadata LIKE ?2 ORDER BY timestamp DESC LIMIT ?3",
        )?;
        let events = stmt
            .query_map(params![since.to_rfc3339(), pattern, limit as i64], row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
    }

    pub fn get_event_by_id(&self, event_id: &str) -> Result<Option<Event>> {
        let conn = self.db.conn();
        self.get_event_by_id_with_filter(&conn, event_id, None)
    }

    pub fn get_events_by_ids(&self, event_ids: &[String]) -> Result<Vec<Event>> {
        if event_ids.is_empty() {
            return Ok(Vec::new());
        }
        let conn = self.db.conn();
        let placeholders = (1..=event_ids.len())
            .map(|i| format!("?{i}"))
            .collect::<Vec<_>>()
            .join(", ");
        let sql = format!(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE id IN ({placeholders})"
        );
        let mut stmt = conn.prepare(&sql)?;
        let events = stmt
            .query_map(rusqlite::params_from_iter(event_ids.iter()), row_to_event)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(events)
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    fn get_event_by_id_with_filter(
        &self,
        conn: &rusqlite::Connection,
        event_id: &str,
        filter: Option<&EventFilter>,
    ) -> Result<Option<Event>> {
        let mut stmt = conn.prepare_cached(
            "SELECT id, timestamp, date, event_type, source, raw_content, summary, category, project, task_id, metadata
             FROM events WHERE id = ?1",
        )?;
        let event = stmt
            .query_row(params![event_id], row_to_event)
            .optional()?;

        if let Some(filter) = filter {
            if let Some(ref event) = event {
                if let Some(ref date) = filter.date {
                    if &event.date_str() != date {
                        return Ok(None);
                    }
                }
                if let Some(ref cat) = filter.category {
                    if event.category.as_ref() != Some(cat) {
                        return Ok(None);
                    }
                }
                if let Some(ref proj) = filter.project {
                    if event.project.as_deref() != Some(proj.as_str()) {
                        return Ok(None);
                    }
                }
                if let Some(compacted) = filter.compacted {
                    // compacted flag not in the in-memory event; would need separate query
                    let _ = compacted;
                }
            }
        }

        Ok(event)
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
    let metadata: HashMap<String, Value> =
        serde_json::from_str(&metadata_str).unwrap_or_default();

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
