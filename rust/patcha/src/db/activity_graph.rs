use crate::{
    db::Db,
    models::{Event, EventType, GraphNode},
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use rusqlite::params;
use std::collections::HashMap;
use std::sync::Arc;
use uuid::Uuid;

pub struct ActivityGraph {
    db: Arc<Db>,
    session_gap_seconds: u64,
}

// Node labels (matching Python)
const L_SESSION: &str = "Session";
const L_APP: &str = "App";
const L_WINDOW: &str = "Window";
const L_PROJECT: &str = "Project";
const L_URL: &str = "URL";
const L_FILE: &str = "File";
const L_SCREEN_EVENT: &str = "ScreenEvent";
const L_WINDOW_EVENT: &str = "WindowEvent";
const L_TERMINAL_EVENT: &str = "TerminalEvent";
const L_BROWSER_EVENT: &str = "BrowserEvent";
const L_GIT_COMMIT: &str = "GitCommit";

// Edge types (matching Python)
const E_ON_APP: &str = "ON_APP";
const E_IN_WINDOW: &str = "IN_WINDOW";
#[allow(dead_code)]
const E_BELONGS_TO: &str = "BELONGS_TO";
const E_DURING: &str = "DURING";
const E_FOLLOWED_BY: &str = "FOLLOWED_BY";
#[allow(dead_code)]
const E_SWITCHED_FROM: &str = "SWITCHED_FROM";
const E_IN_PROJECT: &str = "IN_PROJECT";
const E_MODIFIED: &str = "MODIFIED";
const E_VISITED: &str = "VISITED";

impl ActivityGraph {
    pub fn new(db: Arc<Db>, session_gap_seconds: u64) -> Self {
        Self {
            db,
            session_gap_seconds,
        }
    }

    // -----------------------------------------------------------------------
    // Session management
    // -----------------------------------------------------------------------

    pub fn current_session(&self, event_ts: DateTime<Utc>) -> Result<String> {
        let conn = self.db.conn();

        let row = conn
            .query_row(
                "SELECT current_session_id, last_event_ts FROM activity_session_state WHERE id = 1",
                [],
                |row| {
                    Ok((
                        row.get::<_, Option<String>>(0)?,
                        row.get::<_, Option<String>>(1)?,
                    ))
                },
            )
            .unwrap_or((None, None));

        let (session_id, last_ts) = row;

        let new_session = match (session_id.as_deref(), last_ts.as_deref()) {
            (Some(sid), Some(ts)) => {
                let last = DateTime::parse_from_rfc3339(ts)
                    .map(|dt| dt.with_timezone(&Utc))
                    .unwrap_or(Utc::now());
                let gap = (event_ts - last).num_seconds().unsigned_abs();
                if gap > self.session_gap_seconds {
                    None
                } else {
                    Some(sid.to_owned())
                }
            }
            _ => None,
        };

        let session_id = match new_session {
            Some(id) => id,
            None => {
                let id = Uuid::new_v4().to_string();
                self.upsert_node_raw(
                    &conn,
                    &id,
                    L_SESSION,
                    &serde_json::json!({
                        "start_ts": event_ts.to_rfc3339(),
                        "ts_ms": event_ts.timestamp_millis(),
                    }),
                )?;
                id
            }
        };

        conn.execute(
            "UPDATE activity_session_state
             SET current_session_id = ?1, last_event_ts = ?2
             WHERE id = 1",
            params![session_id, event_ts.to_rfc3339()],
        )?;

        Ok(session_id)
    }

    // -----------------------------------------------------------------------
    // Event ingestion
    // -----------------------------------------------------------------------

    pub fn upsert_event(&self, event: &Event) -> Result<()> {
        let conn = self.db.conn();
        let session_id = self.current_session(event.timestamp)?;

        let (event_label, event_props) = self.event_to_node(event);
        let event_node_id = event.id.clone();

        self.upsert_node_raw(&conn, &event_node_id, event_label, &event_props)?;

        // DURING session
        self.upsert_edge(
            &conn,
            &event_node_id,
            &session_id,
            E_DURING,
            &serde_json::json!({}),
        )?;

        // ON_APP + IN_WINDOW
        if let Some(app) = event.metadata.get("app_name").and_then(|v| v.as_str()) {
            let app_id = format!("app:{}", app.to_lowercase().replace(' ', "_"));
            self.upsert_node_raw(&conn, &app_id, L_APP, &serde_json::json!({ "name": app }))?;
            self.upsert_edge(
                &conn,
                &event_node_id,
                &app_id,
                E_ON_APP,
                &serde_json::json!({}),
            )?;

            if let Some(title) = event.metadata.get("window_title").and_then(|v| v.as_str()) {
                let win_id = format!("win:{}:{}", app.to_lowercase(), title.len());
                self.upsert_node_raw(
                    &conn,
                    &win_id,
                    L_WINDOW,
                    &serde_json::json!({ "app": app, "title": title }),
                )?;
                self.upsert_edge(
                    &conn,
                    &event_node_id,
                    &win_id,
                    E_IN_WINDOW,
                    &serde_json::json!({}),
                )?;
            }
        }

        // IN_PROJECT
        if let Some(proj) = &event.project {
            let proj_id = format!("project:{}", proj.to_lowercase().replace(' ', "_"));
            self.upsert_node_raw(
                &conn,
                &proj_id,
                L_PROJECT,
                &serde_json::json!({ "name": proj }),
            )?;
            self.upsert_edge(
                &conn,
                &event_node_id,
                &proj_id,
                E_IN_PROJECT,
                &serde_json::json!({}),
            )?;
        }

        // Event-type-specific edges
        match event.event_type {
            EventType::Browser => {
                if let Some(url) = event.metadata.get("url").and_then(|v| v.as_str()) {
                    let url_id = format!("url:{}", url.len()); // stable but not guessable
                    self.upsert_node_raw(
                        &conn,
                        &url_id,
                        L_URL,
                        &serde_json::json!({ "url": url }),
                    )?;
                    self.upsert_edge(
                        &conn,
                        &event_node_id,
                        &url_id,
                        E_VISITED,
                        &serde_json::json!({}),
                    )?;
                }
            }
            EventType::GitCommit => {
                if let Some(files) = event.metadata.get("files_changed") {
                    if let Some(arr) = files.as_array() {
                        for f in arr.iter().filter_map(|v| v.as_str()).take(10) {
                            let file_id = format!("file:{}", f);
                            self.upsert_node_raw(
                                &conn,
                                &file_id,
                                L_FILE,
                                &serde_json::json!({ "path": f }),
                            )?;
                            self.upsert_edge(
                                &conn,
                                &event_node_id,
                                &file_id,
                                E_MODIFIED,
                                &serde_json::json!({}),
                            )?;
                        }
                    }
                }
            }
            _ => {}
        }

        // FOLLOWED_BY: link to previous event in session
        let prev = conn
            .query_row(
                "SELECT src_id FROM activity_edges
                 WHERE dst_id IN (
                     SELECT id FROM activity_nodes
                     WHERE id IN (
                         SELECT src_id FROM activity_edges WHERE dst_id = ?1 AND type = ?2
                     )
                 )
                 AND type = ?3
                 ORDER BY rowid DESC LIMIT 1",
                params![session_id, E_DURING, E_FOLLOWED_BY],
                |row| row.get::<_, String>(0),
            )
            .ok();

        if let Some(prev_id) = prev {
            self.upsert_edge(
                &conn,
                &prev_id,
                &event_node_id,
                E_FOLLOWED_BY,
                &serde_json::json!({}),
            )?;
        }

        Ok(())
    }

    // -----------------------------------------------------------------------
    // Traversal queries
    // -----------------------------------------------------------------------

    pub fn events_in_session(&self, session_id: &str) -> Result<Vec<String>> {
        let conn = self.db.conn();
        let mut stmt = conn
            .prepare_cached("SELECT src_id FROM activity_edges WHERE dst_id = ?1 AND type = ?2")?;
        let ids = stmt
            .query_map(params![session_id, E_DURING], |row| row.get(0))?
            .collect::<std::result::Result<_, _>>()?;
        Ok(ids)
    }

    pub fn apps_in_session(&self, session_id: &str) -> Result<Vec<String>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT DISTINCT n.props
             FROM activity_nodes n
             JOIN activity_edges e ON e.dst_id = n.id
             WHERE e.type = ?1
               AND e.src_id IN (
                   SELECT src_id FROM activity_edges WHERE dst_id = ?2 AND type = ?3
               )
               AND n.label = ?4",
        )?;
        let apps = stmt
            .query_map(params![E_ON_APP, session_id, E_DURING, L_APP], |row| {
                let props_str: String = row.get(0)?;
                let props: serde_json::Value =
                    serde_json::from_str(&props_str).unwrap_or(serde_json::Value::Null);
                Ok(props
                    .get("name")
                    .and_then(|v| v.as_str())
                    .unwrap_or("")
                    .to_owned())
            })?
            .collect::<std::result::Result<_, _>>()?;
        Ok(apps)
    }

    pub fn events_before(&self, event_id: &str, n: usize) -> Result<Vec<GraphNode>> {
        self.walk_chain(event_id, n, false)
    }

    pub fn events_after(&self, event_id: &str, n: usize) -> Result<Vec<GraphNode>> {
        self.walk_chain(event_id, n, true)
    }

    fn walk_chain(&self, start: &str, n: usize, forward: bool) -> Result<Vec<GraphNode>> {
        let conn = self.db.conn();
        let (from_col, to_col) = if forward {
            ("src_id", "dst_id")
        } else {
            ("dst_id", "src_id")
        };
        let sql = format!(
            "SELECT n.id, n.label, n.props FROM activity_nodes n
             JOIN activity_edges e ON e.{to_col} = n.id
             WHERE e.{from_col} = ?1 AND e.type = ?2
             LIMIT ?3"
        );
        let mut stmt = conn.prepare(&sql)?;
        let mut nodes: Vec<GraphNode> = Vec::new();
        let mut current = start.to_owned();

        for _ in 0..n {
            let row = stmt
                .query_row(params![current, E_FOLLOWED_BY, 1_i64], |row| {
                    Ok((
                        row.get::<_, String>(0)?,
                        row.get::<_, String>(1)?,
                        row.get::<_, String>(2)?,
                    ))
                })
                .ok();
            match row {
                Some((id, label, props_str)) => {
                    current = id.clone();
                    let props: HashMap<String, serde_json::Value> =
                        serde_json::from_str(&props_str).unwrap_or_default();
                    nodes.push(GraphNode { id, label, props });
                }
                None => break,
            }
        }
        Ok(nodes)
    }

    pub fn session_of(&self, event_id: &str) -> Result<Option<String>> {
        let conn = self.db.conn();
        conn.query_row(
            "SELECT dst_id FROM activity_edges WHERE src_id = ?1 AND type = ?2 LIMIT 1",
            params![event_id, E_DURING],
            |row| row.get(0),
        )
        .optional()
    }

    pub fn events_touching(&self, target: &str, target_type: &str) -> Result<Vec<GraphNode>> {
        let conn = self.db.conn();
        // Find the target node id
        let node_id: Option<String> = conn
            .query_row(
                "SELECT id FROM activity_nodes WHERE label = ?1 AND props LIKE ?2 LIMIT 1",
                params![target_type, format!("%{}%", target.replace('"', "\\\""))],
                |row| row.get(0),
            )
            .ok();

        let Some(node_id) = node_id else {
            return Ok(Vec::new());
        };

        let mut stmt = conn.prepare_cached(
            "SELECT n.id, n.label, n.props FROM activity_nodes n
             JOIN activity_edges e ON e.src_id = n.id
             WHERE e.dst_id = ?1",
        )?;
        let nodes = stmt
            .query_map(params![node_id], |row| {
                let id: String = row.get(0)?;
                let label: String = row.get(1)?;
                let props_str: String = row.get(2)?;
                let props: HashMap<String, serde_json::Value> =
                    serde_json::from_str(&props_str).unwrap_or_default();
                Ok(GraphNode { id, label, props })
            })?
            .collect::<std::result::Result<_, _>>()?;
        Ok(nodes)
    }

    pub fn find_event(
        &self,
        app: Option<&str>,
        at: Option<DateTime<Utc>>,
    ) -> Result<Option<GraphNode>> {
        let conn = self.db.conn();
        let event_labels =
            "('ScreenEvent','WindowEvent','TerminalEvent','BrowserEvent','GitCommit')";

        let row: Option<(String, String, String)> = match (app, at) {
            (Some(app_name), Some(ts)) => {
                let ts_unix = ts.timestamp();
                conn.query_row(
                    &format!(
                        "SELECT n.id, n.label, n.props
                         FROM activity_nodes n
                         JOIN activity_edges e ON e.src_id = n.id AND e.type = '{E_ON_APP}'
                         JOIN activity_nodes a ON a.id = e.dst_id AND a.label = '{L_APP}'
                           AND lower(json_extract(a.props, '$.name')) = lower(?1)
                         WHERE n.label IN {event_labels}
                         ORDER BY ABS(CAST(json_extract(n.props, '$.ts_ms') AS INTEGER) / 1000 - ?2) ASC
                         LIMIT 1"
                    ),
                    params![app_name, ts_unix],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .ok()
            }
            (Some(app_name), None) => conn
                .query_row(
                    &format!(
                        "SELECT n.id, n.label, n.props
                         FROM activity_nodes n
                         JOIN activity_edges e ON e.src_id = n.id AND e.type = '{E_ON_APP}'
                         JOIN activity_nodes a ON a.id = e.dst_id AND a.label = '{L_APP}'
                           AND lower(json_extract(a.props, '$.name')) = lower(?1)
                         WHERE n.label IN {event_labels}
                         ORDER BY json_extract(n.props, '$.timestamp') DESC
                         LIMIT 1"
                    ),
                    params![app_name],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .ok(),
            (None, Some(ts)) => {
                let ts_unix = ts.timestamp();
                conn.query_row(
                    &format!(
                        "SELECT id, label, props
                         FROM activity_nodes
                         WHERE label IN {event_labels}
                         ORDER BY ABS(CAST(json_extract(props, '$.ts_ms') AS INTEGER) / 1000 - ?1) ASC
                         LIMIT 1"
                    ),
                    params![ts_unix],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .ok()
            }
            (None, None) => conn
                .query_row(
                    &format!(
                        "SELECT id, label, props
                         FROM activity_nodes
                         WHERE label IN {event_labels}
                         ORDER BY json_extract(props, '$.timestamp') DESC
                         LIMIT 1"
                    ),
                    [],
                    |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
                )
                .ok(),
        };

        Ok(row.map(|(id, label, props_str)| {
            let props: HashMap<String, serde_json::Value> =
                serde_json::from_str(&props_str).unwrap_or_default();
            GraphNode { id, label, props }
        }))
    }

    pub fn get_node(&self, id: &str) -> Result<Option<GraphNode>> {
        let conn = self.db.conn();
        let row: Option<(String, String, String)> = conn
            .query_row(
                "SELECT id, label, props FROM activity_nodes WHERE id = ?1",
                params![id],
                |r| Ok((r.get(0)?, r.get(1)?, r.get(2)?)),
            )
            .ok();
        Ok(row.map(|(id, label, props_str)| {
            let props: HashMap<String, serde_json::Value> =
                serde_json::from_str(&props_str).unwrap_or_default();
            GraphNode { id, label, props }
        }))
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    fn upsert_node_raw(
        &self,
        conn: &rusqlite::Connection,
        id: &str,
        label: &str,
        props: &serde_json::Value,
    ) -> Result<()> {
        let now = Utc::now().to_rfc3339();
        conn.execute(
            "INSERT INTO activity_nodes(id, label, props, created_at, updated_at)
             VALUES (?1, ?2, ?3, ?4, ?4)
             ON CONFLICT(id) DO UPDATE SET props = excluded.props, updated_at = excluded.updated_at",
            params![id, label, serde_json::to_string(props)?, now],
        )?;
        Ok(())
    }

    fn upsert_edge(
        &self,
        conn: &rusqlite::Connection,
        src: &str,
        dst: &str,
        edge_type: &str,
        props: &serde_json::Value,
    ) -> Result<()> {
        let id = Uuid::new_v4().to_string();
        let now = Utc::now().to_rfc3339();
        conn.execute(
            "INSERT OR IGNORE INTO activity_edges(id, src_id, dst_id, type, props, created_at)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6)",
            params![id, src, dst, edge_type, serde_json::to_string(props)?, now],
        )?;
        Ok(())
    }

    fn event_to_node(&self, event: &Event) -> (&'static str, serde_json::Value) {
        let ts = event.timestamp.to_rfc3339();
        let ts_ms = event.timestamp.timestamp_millis();

        let mut props = serde_json::json!({
            "timestamp": ts,
            "ts_ms": ts_ms,
        });

        if let Some(obj) = props.as_object_mut() {
            for (k, v) in &event.metadata {
                obj.insert(k.clone(), v.clone());
            }
            if let Some(proj) = &event.project {
                obj.insert("project".into(), serde_json::json!(proj));
            }
        }

        let label = match event.event_type {
            EventType::Screen => L_SCREEN_EVENT,
            EventType::Window => L_WINDOW_EVENT,
            EventType::Terminal => L_TERMINAL_EVENT,
            EventType::Browser => L_BROWSER_EVENT,
            EventType::GitCommit | EventType::GitStash | EventType::GitStaged => L_GIT_COMMIT,
            EventType::Note => L_SCREEN_EVENT,
        };

        (label, props)
    }
}

// ---------------------------------------------------------------------------
// optional() helper (local)
// ---------------------------------------------------------------------------

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
