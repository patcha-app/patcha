use std::{collections::HashMap, future::Future, sync::Arc};

use anyhow::Result;
use axum::{Json, Router, extract::Query, routing::{get, post}};
use chrono::NaiveDate;
use rmcp::{
    ServerHandler,
    handler::server::{router::tool::ToolRouter, tool::Parameters},
    model::{Implementation, ServerInfo},
    schemars::{self, JsonSchema},
    serde::{self, Deserialize},
    tool, tool_handler, tool_router,
};
use tokio::net::TcpListener;

use crate::{
    config::Config,
    db::{
        Db,
        activity_graph::ActivityGraph,
        retrieval,
        store::VectorStore,
    },
    embedding::Embedder,
    llm::client::PatchaApiClient,
    mcp::{McpArgs, chat},
    rerank::CrossEncoderReranker,
};

// ---------------------------------------------------------------------------
// Tool parameter types
// ---------------------------------------------------------------------------

#[derive(Deserialize, JsonSchema)]
#[serde(crate = "self::serde")]
struct GetWorkingMemoryParams {
    #[schemars(description = "How many minutes back to look. Default 15.")]
    minutes: Option<i64>,
}

#[derive(Deserialize, JsonSchema)]
#[serde(crate = "self::serde")]
struct SearchActivityParams {
    #[schemars(description = "What to search for in past activity.")]
    query: String,
    #[schemars(description = "Number of results to return. Default 10.")]
    limit: Option<i64>,
    #[schemars(description = "Filter to a specific app (e.g. 'Arc', 'Zed', 'WezTerm', 'Cursor'). Applies to screen and window events which capture app_name.")]
    app: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
#[serde(crate = "self::serde")]
struct GetRecentActivityParams {
    #[schemars(description = "How many hours back to look. Default 3.")]
    hours: Option<i64>,
    #[schemars(description = "Filter to a specific app (e.g. 'Arc', 'Zed', 'WezTerm', 'Cursor'). Applies to screen and window events which capture app_name.")]
    app: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
#[serde(crate = "self::serde")]
struct GetActivityContextParams {
    #[schemars(description = "Anchor on the most relevant event from this app (e.g. 'Slack').")]
    app: Option<String>,
    #[schemars(description = "Anchor near this ISO 8601 timestamp (e.g. '2026-06-01T14:30:00Z').")]
    time: Option<String>,
    #[schemars(description = "Which side of the anchor to show: 'before', 'after', or 'both'. Default 'both'.")]
    direction: Option<String>,
    #[schemars(description = "How many events on each side. Default 3.")]
    count: Option<i64>,
}

#[derive(Deserialize, JsonSchema)]
#[serde(crate = "self::serde")]
struct GetSessionParams {
    #[schemars(description = "Anchor on the most relevant event from this app.")]
    app: Option<String>,
    #[schemars(description = "Anchor near this ISO 8601 timestamp.")]
    time: Option<String>,
}

#[derive(Deserialize, JsonSchema)]
#[serde(crate = "self::serde")]
struct FindConnectedParams {
    #[schemars(description = "File path, e.g. 'patcha/collectors/accessibility.py'.")]
    file: Option<String>,
    #[schemars(description = "Project name, e.g. 'patcha'.")]
    project: Option<String>,
    #[schemars(description = "Exact URL visited.")]
    url: Option<String>,
    #[schemars(description = "App name, e.g. 'Arc'.")]
    app: Option<String>,
}

// ---------------------------------------------------------------------------
// Server struct
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct PatchaServer {
    store: Arc<VectorStore>,
    graph: Arc<ActivityGraph>,
    embedder: Arc<Embedder>,
    reranker: Option<Arc<CrossEncoderReranker>>,
    cfg: Arc<Config>,
    tool_router: ToolRouter<Self>,
}

#[tool_router]
impl PatchaServer {
    fn new(
        store: Arc<VectorStore>,
        graph: Arc<ActivityGraph>,
        embedder: Arc<Embedder>,
        reranker: Option<Arc<CrossEncoderReranker>>,
        cfg: Arc<Config>,
    ) -> Self {
        Self {
            store,
            graph,
            embedder,
            reranker,
            cfg,
            tool_router: Self::tool_router(),
        }
    }

    #[tool(description = "Get a compact summary of the user's recent device activity. Shows open apps (with app-switch transitions noted), browser research, terminal commands, and git activity from the last N minutes. Use this to understand what the user is currently working on before answering questions or making suggestions.")]
    async fn get_working_memory(
        &self,
        Parameters(p): Parameters<GetWorkingMemoryParams>,
    ) -> String {
        let minutes = p.minutes.unwrap_or(15) as u32;
        retrieval::context::get_working_memory(&self.store, minutes, 0.97)
            .await
            .unwrap_or_else(|e| format!("Error: {e}"))
    }

    #[tool(description = "Search the user's full activity history semantically. Use this to find specific past work — e.g. 'qdrant vector search setup', 'authentication bug fix', 'npm install error', 'what changed in the auth fix'. Returns the most relevant past events with similarity scores. For git commits and stashes, the full diff is included in the result. Optionally filter to events from a specific app (e.g. 'Arc', 'Zed', 'WezTerm').")]
    async fn search_activity(
        &self,
        Parameters(p): Parameters<SearchActivityParams>,
    ) -> String {
        let limit = p.limit.unwrap_or(10) as usize;
        let params = retrieval::context::RetrievalParams::from_config(
            &self.cfg,
            limit,
            p.app.as_deref(),
        );
        retrieval::context::search_activity(
            &self.store,
            &self.embedder,
            self.reranker.as_deref(),
            &p.query,
            &params,
        )
        .await
        .unwrap_or_else(|e| format!("Error: {e}"))
    }

    #[tool(description = "Get a deduped log of the user's raw activity over the last N hours. Use this for broader historical context — what the user has been working on over the last few hours, not just the last few minutes. Optionally filter to a specific app.")]
    async fn get_recent_activity(
        &self,
        Parameters(p): Parameters<GetRecentActivityParams>,
    ) -> String {
        let hours = p.hours.unwrap_or(3) as u32;
        retrieval::context::get_recent_activity(&self.store, hours, p.app.as_deref(), 0.97)
            .await
            .unwrap_or_else(|e| format!("Error: {e}"))
    }

    #[tool(description = "Get the temporal context around a moment of activity — what the user was doing right before and after a specific event. Answers questions like 'what was I doing before I opened Slack?' or 'what happened around 2pm?'. Anchor the moment by app name, by time, or both (the app's event nearest that time). Returns the surrounding events in chronological order plus the work session they belonged to. Use this for ordering/adjacency questions that semantic search can't answer.")]
    async fn get_activity_context(
        &self,
        Parameters(p): Parameters<GetActivityContextParams>,
    ) -> String {
        let direction = p.direction.as_deref().unwrap_or("both");
        let count = p.count.unwrap_or(3) as usize;
        retrieval::graph_context::get_activity_context(
            &self.graph,
            p.app.as_deref(),
            p.time.as_deref(),
            direction,
            count,
        )
        .unwrap_or_else(|e| format!("Error: {e}"))
    }

    #[tool(description = "Get the full work session around a moment — every app and event between two idle gaps. Answers 'what apps did I use in the same session as commit X?' or 'show me everything from the session around 3pm'. Anchor by app name, time, or both. Returns the session time span, the apps involved, and the events in chronological order.")]
    async fn get_session(&self, Parameters(p): Parameters<GetSessionParams>) -> String {
        retrieval::graph_context::get_session(
            &self.graph,
            p.app.as_deref(),
            p.time.as_deref(),
        )
        .unwrap_or_else(|e| format!("Error: {e}"))
    }

    #[tool(description = "Find every event structurally connected to a file, project, url, or app — across event types. Answers 'show me all activity touching accessibility.py' or 'everything related to the patcha project'. Provide exactly one of file, project, url, or app. Returns the connected events in chronological order.")]
    async fn find_connected(
        &self,
        Parameters(p): Parameters<FindConnectedParams>,
    ) -> String {
        retrieval::graph_context::find_connected(
            &self.graph,
            p.file.as_deref(),
            p.project.as_deref(),
            p.url.as_deref(),
            p.app.as_deref(),
        )
        .unwrap_or_else(|e| format!("Error: {e}"))
    }
}

#[tool_handler]
impl ServerHandler for PatchaServer {
    fn get_info(&self) -> ServerInfo {
        ServerInfo {
            server_info: Implementation {
                name: "patcha".into(),
                version: env!("CARGO_PKG_VERSION").into(),
            },
            ..ServerInfo::default()
        }
    }
}

// ---------------------------------------------------------------------------
// Entry point
// ---------------------------------------------------------------------------

pub async fn run(args: McpArgs, cfg: Config) -> Result<()> {
    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db.clone()));
    let graph = Arc::new(ActivityGraph::new(db.clone(), 600));
    let embedder = Arc::new(Embedder::new(&cfg)?);

    let reranker = if cfg.enable_reranker {
        let dir = cfg
            .embedding_cache_dir
            .join(cfg.reranker_model_name.replace('/', "_"));
        let r = CrossEncoderReranker::new(dir.clone());
        if r.available() {
            tracing::info!(model = %cfg.reranker_model_name, "cross-encoder reranker enabled");
            Some(Arc::new(r))
        } else {
            tracing::info!(dir = %dir.display(), "reranker model not found; skipping rerank stage");
            None
        }
    } else {
        None
    };

    let server = PatchaServer::new(store, graph, embedder, reranker, Arc::new(cfg.clone()));

    if !args.stdio {
        run_http(server, &cfg, args.port).await
    } else {
        run_stdio(server).await
    }
}

async fn run_stdio(server: PatchaServer) -> Result<()> {
    use rmcp::ServiceExt;
    let transport = (tokio::io::stdin(), tokio::io::stdout());
    server.serve(transport).await?.waiting().await?;
    Ok(())
}

async fn run_http(server: PatchaServer, _cfg: &Config, port: u16) -> Result<()> {
    use rmcp::transport::streamable_http_server::{
        StreamableHttpServerConfig, StreamableHttpService,
        session::local::LocalSessionManager,
    };

    write_port(port)?;

    let session_manager = Arc::new(LocalSessionManager::default());
    let mcp_service = {
        let server = server.clone();
        StreamableHttpService::new(
            move || Ok(server.clone()),
            session_manager,
            StreamableHttpServerConfig::default(),
        )
    };

    let store = server.store.clone();
    let chat_state = chat::ChatState {
        store: server.store.clone(),
        graph: server.graph.clone(),
        embedder: server.embedder.clone(),
        reranker: server.reranker.clone(),
        cfg: server.cfg.clone(),
        api: Arc::new(PatchaApiClient::new(_cfg)),
        port,
    };
    let app = Router::new()
        .route(
            "/api/timeline",
            get(move |q: Query<TimelineQuery>| {
                let store = store.clone();
                async move { timeline_handler(store, q).await }
            }),
        )
        .route("/api/chat", post(chat::handle_chat))
        .route("/api/chat/backends", get(chat::backends_handler))
        .with_state(chat_state)
        .nest_service("/mcp", mcp_service);

    let addr = format!("127.0.0.1:{port}");
    tracing::info!(addr, "MCP HTTP server starting");
    let listener = TcpListener::bind(&addr).await?;
    axum::serve(listener, app).await?;

    clear_port()?;
    Ok(())
}

// ---------------------------------------------------------------------------
// /api/timeline endpoint
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
#[serde(crate = "self::serde")]
struct TimelineQuery {
    date: Option<String>,
}

async fn timeline_handler(
    store: Arc<VectorStore>,
    Query(q): Query<TimelineQuery>,
) -> Json<serde_json::Value> {
    let target_date = q
        .date
        .as_deref()
        .and_then(|s| NaiveDate::parse_from_str(s, "%Y-%m-%d").ok())
        .unwrap_or_else(|| chrono::Local::now().date_naive());

    let date_str = target_date.format("%Y-%m-%d").to_string();
    let events = match store.get_events_by_date(&date_str) {
        Ok(v) => v,
        Err(e) => {
            return Json(serde_json::json!({ "error": e.to_string() }));
        }
    };

    // LLM-generated per-hour narratives produced by the daemon, keyed by hour.
    let stored: HashMap<u32, _> = store
        .get_timeline_summaries(&date_str)
        .unwrap_or_default()
        .into_iter()
        .map(|s| (s.hour, s))
        .collect();

    let mut hours: HashMap<u32, HourBucket> = HashMap::new();

    let mut ordered = events;
    ordered.sort_by_key(|e| e.timestamp);

    for event in &ordered {
        let hour = event.timestamp.format("%H").to_string().parse::<u32>().unwrap_or(0);
        let bucket = hours.entry(hour).or_default();
        bucket.event_count += 1;

        if let Some(app) = event.metadata.get("app_name").and_then(|v| v.as_str()) {
            *bucket.app_counts.entry(app.to_owned()).or_insert(0) += 1;
        }

        if let Some(s) = event.summary.as_deref() {
            if !bucket.summaries.contains(&s.to_owned()) {
                bucket.summaries.push(s.to_owned());
            }
        }

        if let Some(c) = &event.category {
            let cs = c.to_string();
            if !bucket.categories.contains(&cs) {
                bucket.categories.push(cs);
            }
        }
    }

    let mut result: Vec<serde_json::Value> = Vec::new();
    // Union of hours that have events and hours that have a stored summary.
    let mut sorted_hours: Vec<u32> = hours.keys().copied().collect();
    for hour in stored.keys() {
        if !hours.contains_key(hour) {
            sorted_hours.push(*hour);
        }
    }
    sorted_hours.sort_unstable();

    for hour in sorted_hours {
        // Prefer the stored LLM summary; fall back to per-event aggregation.
        if let Some(s) = stored.get(&hour) {
            result.push(serde_json::json!({
                "hour": hour,
                "apps": s.apps,
                "summaries": [s.summary],
                "categories": s.categories,
                "event_count": s.event_count,
                "title": s.title,
                "tree": s.tree,
            }));
            continue;
        }

        let bucket = &hours[&hour];
        let mut apps: Vec<(&String, &u32)> = bucket.app_counts.iter().collect();
        apps.sort_by_key(|(_, count)| std::cmp::Reverse(**count));
        let app_names: Vec<&str> = apps.iter().map(|(k, _)| k.as_str()).collect();

        result.push(serde_json::json!({
            "hour": hour,
            "apps": app_names,
            "summaries": bucket.summaries,
            "categories": bucket.categories,
            "event_count": bucket.event_count,
        }));
    }

    Json(serde_json::json!({
        "date": date_str,
        "hours": result,
    }))
}

#[derive(Default)]
struct HourBucket {
    app_counts: HashMap<String, u32>,
    summaries: Vec<String>,
    categories: Vec<String>,
    event_count: u32,
}

// ---------------------------------------------------------------------------
// Settings DB — port registration
// ---------------------------------------------------------------------------

fn settings_db_path() -> std::path::PathBuf {
    dirs::home_dir()
        .unwrap_or_default()
        .join("Library")
        .join("Application Support")
        .join("patcha")
        .join("settings.db")
}

fn write_port(port: u16) -> Result<()> {
    let path = settings_db_path();
    if let Some(parent) = path.parent() {
        std::fs::create_dir_all(parent)?;
    }
    let conn = rusqlite::Connection::open(&path)?;
    conn.execute_batch(
        "CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)",
    )?;
    conn.execute(
        "INSERT OR REPLACE INTO settings (key, value) VALUES ('mcp_port', ?1)",
        rusqlite::params![port.to_string()],
    )?;
    Ok(())
}

fn clear_port() -> Result<()> {
    let path = settings_db_path();
    if !path.exists() {
        return Ok(());
    }
    let conn = rusqlite::Connection::open(&path)?;
    conn.execute(
        "DELETE FROM settings WHERE key = 'mcp_port'",
        [],
    )?;
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::models::{Category, Event, EventType, TimelineHourSummary};
    use chrono::{TimeZone, Utc};

    const DATE: &str = "2026-06-15";

    fn store() -> Arc<VectorStore> {
        let tmp = tempfile::tempdir().unwrap();
        // Leak the tempdir so the on-disk DB outlives the test body.
        let path = tmp.keep().join("test.db");
        let db = Db::open(&path).expect("open db");
        Arc::new(VectorStore::new(db))
    }

    fn event_at(hour: u32, app: &str, summary: Option<&str>, cat: Option<Category>) -> Event {
        let mut e = Event::new(EventType::Screen, "raw");
        e.timestamp = Utc.with_ymd_and_hms(2026, 6, 15, hour, 0, 0).unwrap();
        e.metadata.insert("app_name".into(), serde_json::json!(app));
        e.summary = summary.map(|s| s.to_owned());
        e.category = cat;
        e
    }

    async fn timeline(store: Arc<VectorStore>, date: Option<&str>) -> serde_json::Value {
        let q = Query(TimelineQuery { date: date.map(|s| s.to_owned()) });
        timeline_handler(store, q).await.0
    }

    fn hour_obj(v: &serde_json::Value, hour: u32) -> serde_json::Value {
        v["hours"]
            .as_array()
            .unwrap()
            .iter()
            .find(|h| h["hour"] == hour)
            .cloned()
            .unwrap_or_else(|| panic!("no bucket for hour {hour}"))
    }

    #[tokio::test]
    async fn aggregates_events_into_hour_buckets() {
        let store = store();
        store
            .store_events(&[
                event_at(9, "Xcode", Some("wrote tests"), Some(Category::Coding)),
                event_at(9, "Xcode", Some("wrote tests"), Some(Category::Coding)),
                event_at(9, "Safari", Some("read docs"), Some(Category::Research)),
                event_at(14, "Slack", Some("standup"), Some(Category::Communication)),
            ])
            .unwrap();

        let out = timeline(store, Some(DATE)).await;
        assert_eq!(out["date"], DATE);
        assert_eq!(out["hours"].as_array().unwrap().len(), 2);

        let nine = hour_obj(&out, 9);
        assert_eq!(nine["event_count"], 3);
        // Apps sorted by frequency: Xcode (2) before Safari (1).
        assert_eq!(nine["apps"][0], "Xcode");
        assert_eq!(nine["apps"][1], "Safari");
        // Duplicate "wrote tests" summary collapsed.
        assert_eq!(nine["summaries"].as_array().unwrap().len(), 2);
        assert_eq!(nine["categories"].as_array().unwrap().len(), 2);
    }

    #[tokio::test]
    async fn stored_summary_takes_precedence_over_raw_aggregation() {
        let store = store();
        store
            .store_events(&[event_at(10, "Xcode", Some("raw summary"), None)])
            .unwrap();
        store
            .upsert_timeline_summary(&TimelineHourSummary {
                date: DATE.into(),
                hour: 10,
                title: "Deep work".into(),
                summary: "LLM narrative".into(),
                tree: None,
                apps: vec!["Xcode".into()],
                categories: vec!["coding".into()],
                event_count: 1,
                generated_at: Utc::now(),
            })
            .unwrap();

        let out = timeline(store, Some(DATE)).await;
        let ten = hour_obj(&out, 10);
        assert_eq!(ten["title"], "Deep work");
        assert_eq!(ten["summaries"][0], "LLM narrative");
    }

    #[tokio::test]
    async fn hours_union_includes_summary_only_hours() {
        let store = store();
        store
            .store_events(&[event_at(8, "Xcode", None, None)])
            .unwrap();
        // A stored summary for hour 20 with no events on that date.
        store
            .upsert_timeline_summary(&TimelineHourSummary {
                date: DATE.into(),
                hour: 20,
                title: "Evening".into(),
                summary: "wind down".into(),
                tree: None,
                apps: vec![],
                categories: vec![],
                event_count: 0,
                generated_at: Utc::now(),
            })
            .unwrap();

        let out = timeline(store, Some(DATE)).await;
        let hours: Vec<u64> = out["hours"]
            .as_array()
            .unwrap()
            .iter()
            .map(|h| h["hour"].as_u64().unwrap())
            .collect();
        // Sorted union of event-hour 8 and summary-only hour 20.
        assert_eq!(hours, vec![8, 20]);
    }

    #[tokio::test]
    async fn empty_date_returns_no_hours() {
        let out = timeline(store(), Some("2020-01-01")).await;
        assert_eq!(out["hours"].as_array().unwrap().len(), 0);
    }

    #[tokio::test]
    async fn invalid_date_falls_back_to_today_without_error() {
        let out = timeline(store(), Some("not-a-date")).await;
        let today = chrono::Local::now().format("%Y-%m-%d").to_string();
        assert_eq!(out["date"], today);
        assert!(out.get("error").is_none());
    }
}
