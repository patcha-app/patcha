use std::{convert::Infallible, sync::Arc, time::Duration};

use axum::{
    Json,
    response::sse::{Event, KeepAlive, Sse},
};
use serde::Deserialize;
use serde_json::{Value, json};
use tokio::{
    io::{AsyncBufReadExt, BufReader},
    process::Command,
    sync::mpsc,
};
use tokio_stream::wrappers::ReceiverStream;

use crate::{
    config::Config,
    db::{activity_graph::ActivityGraph, retrieval, store::VectorStore},
    embedding::Embedder,
    llm::client::PatchaApiClient,
    rerank::CrossEncoderReranker,
};

const MODEL: &str = "gpt-4o-mini";
const MAX_TOOL_ROUNDS: usize = 6;

const CHAT_SYSTEM: &str = "\
You are patcha, an observability copilot living inside the user's Mac. patcha \
continuously records what the user does on their device — the apps and windows \
they use, the web pages they research, the terminal commands they run, and their \
git activity — into a local, private activity store.

Your job is to answer the user's questions about their own recorded activity. \
You have tools that read this activity store. ALWAYS use the tools to ground your \
answers in real recorded events instead of guessing. Prefer the most specific \
tool for the question:
- get_working_memory / get_recent_activity for 'what am I doing now / lately'.
- search_activity for finding specific past work by topic.
- get_activity_context / get_session for ordering and 'what was I doing around X'.
- find_connected for everything touching a file, project, url, or app.

Cite concrete details from the results — app names, times, commands, files. Be \
concise and direct. If the tools return no relevant activity, say so plainly \
rather than inventing an answer. Today's questions are about the user's own data, \
so it is always appropriate to look it up.";

// ---------------------------------------------------------------------------
// Shared state
// ---------------------------------------------------------------------------

#[derive(Clone)]
pub struct ChatState {
    pub store: Arc<VectorStore>,
    pub graph: Arc<ActivityGraph>,
    pub embedder: Arc<Embedder>,
    pub reranker: Option<Arc<CrossEncoderReranker>>,
    pub cfg: Arc<Config>,
    pub api: Arc<PatchaApiClient>,
    /// The MCP/HTTP server's own port — used to point the headless CLI agents
    /// at this server's `/mcp/` endpoint so they can call the patcha tools.
    pub port: u16,
}

/// Which engine backs the chat: the built-in patcha-api loop, or a headless
/// local agent CLI (Claude Code / Codex) that talks to the patcha MCP server.
#[derive(Clone, Copy, PartialEq)]
enum Backend {
    Api,
    Claude,
    Codex,
}

impl Backend {
    fn parse(s: Option<&str>) -> Self {
        match s {
            Some("claude") => Backend::Claude,
            Some("codex") => Backend::Codex,
            _ => Backend::Api,
        }
    }
}

// ---------------------------------------------------------------------------
// Request
// ---------------------------------------------------------------------------

#[derive(Deserialize)]
pub struct ChatRequest {
    pub messages: Vec<IncomingMessage>,
    /// "api" (default), "claude", or "codex".
    #[serde(default)]
    pub backend: Option<String>,
}

#[derive(Deserialize)]
pub struct IncomingMessage {
    pub role: String,
    pub content: String,
}

// ---------------------------------------------------------------------------
// Tool specs (OpenAI function-calling schema) mirroring the MCP tools
// ---------------------------------------------------------------------------

fn tool_specs() -> Value {
    json!([
        spec(
            "get_working_memory",
            "Get a compact summary of the user's recent device activity (open apps, browser research, terminal commands, git) from the last N minutes. Use this to understand what the user is currently working on.",
            json!({
                "minutes": { "type": "integer", "description": "How many minutes back to look. Default 15." }
            }),
            &[],
        ),
        spec(
            "search_activity",
            "Search the user's full activity history semantically. Use this to find specific past work — e.g. 'qdrant vector search setup', 'auth bug fix'. Returns the most relevant past events; git commits include the full diff.",
            json!({
                "query": { "type": "string", "description": "What to search for in past activity." },
                "limit": { "type": "integer", "description": "Number of results. Default 10." },
                "app": { "type": "string", "description": "Filter to a specific app (e.g. 'Arc', 'Zed', 'WezTerm')." }
            }),
            &["query"],
        ),
        spec(
            "get_recent_activity",
            "Get a deduped log of the user's raw activity over the last N hours. Use for broader historical context. Optionally filter to a specific app.",
            json!({
                "hours": { "type": "integer", "description": "How many hours back to look. Default 3." },
                "app": { "type": "string", "description": "Filter to a specific app." }
            }),
            &[],
        ),
        spec(
            "get_activity_context",
            "Get the temporal context around a moment — what the user was doing right before and after an event. Anchor by app name, by time, or both. Returns surrounding events in chronological order.",
            json!({
                "app": { "type": "string", "description": "Anchor on the most relevant event from this app." },
                "time": { "type": "string", "description": "Anchor near this ISO 8601 timestamp." },
                "direction": { "type": "string", "description": "'before', 'after', or 'both'. Default 'both'." },
                "count": { "type": "integer", "description": "How many events on each side. Default 3." }
            }),
            &[],
        ),
        spec(
            "get_session",
            "Get the full work session around a moment — every app and event between two idle gaps. Anchor by app name, time, or both.",
            json!({
                "app": { "type": "string", "description": "Anchor on the most relevant event from this app." },
                "time": { "type": "string", "description": "Anchor near this ISO 8601 timestamp." }
            }),
            &[],
        ),
        spec(
            "find_connected",
            "Find every event structurally connected to a file, project, url, or app. Provide exactly one of file, project, url, or app.",
            json!({
                "file": { "type": "string", "description": "File path, e.g. 'collectors/accessibility.rs'." },
                "project": { "type": "string", "description": "Project name, e.g. 'patcha'." },
                "url": { "type": "string", "description": "Exact URL visited." },
                "app": { "type": "string", "description": "App name, e.g. 'Arc'." }
            }),
            &[],
        ),
    ])
}

fn spec(name: &str, description: &str, properties: Value, required: &[&str]) -> Value {
    json!({
        "type": "function",
        "function": {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": properties,
                "required": required,
            }
        }
    })
}

// ---------------------------------------------------------------------------
// Tool dispatch — calls the same retrieval functions the MCP server uses
// ---------------------------------------------------------------------------

async fn dispatch_tool(state: &ChatState, name: &str, args: &Value) -> String {
    let s = |key: &str| args.get(key).and_then(|v| v.as_str()).map(|s| s.to_owned());
    let i = |key: &str| args.get(key).and_then(|v| v.as_i64());

    match name {
        "get_working_memory" => {
            let minutes = i("minutes").unwrap_or(15) as u32;
            retrieval::context::get_working_memory(&state.store, minutes, 0.97)
                .await
                .unwrap_or_else(|e| format!("Error: {e}"))
        }
        "search_activity" => {
            let query = s("query").unwrap_or_default();
            let limit = i("limit").unwrap_or(10) as usize;
            let params = retrieval::context::RetrievalParams::from_config(
                &state.cfg,
                limit,
                s("app").as_deref(),
            );
            retrieval::context::search_activity(
                &state.store,
                &state.embedder,
                state.reranker.as_deref(),
                &query,
                &params,
            )
            .await
            .unwrap_or_else(|e| format!("Error: {e}"))
        }
        "get_recent_activity" => {
            let hours = i("hours").unwrap_or(3) as u32;
            retrieval::context::get_recent_activity(&state.store, hours, s("app").as_deref(), 0.97)
                .await
                .unwrap_or_else(|e| format!("Error: {e}"))
        }
        "get_activity_context" => {
            let direction = s("direction").unwrap_or_else(|| "both".into());
            let count = i("count").unwrap_or(3) as usize;
            retrieval::graph_context::get_activity_context(
                &state.graph,
                s("app").as_deref(),
                s("time").as_deref(),
                &direction,
                count,
            )
            .unwrap_or_else(|e| format!("Error: {e}"))
        }
        "get_session" => retrieval::graph_context::get_session(
            &state.graph,
            s("app").as_deref(),
            s("time").as_deref(),
        )
        .unwrap_or_else(|e| format!("Error: {e}")),
        "find_connected" => retrieval::graph_context::find_connected(
            &state.graph,
            s("file").as_deref(),
            s("project").as_deref(),
            s("url").as_deref(),
            s("app").as_deref(),
        )
        .unwrap_or_else(|e| format!("Error: {e}")),
        other => format!("Error: unknown tool '{other}'"),
    }
}

// ---------------------------------------------------------------------------
// SSE handler
// ---------------------------------------------------------------------------

pub async fn handle_chat(
    state: axum::extract::State<ChatState>,
    Json(req): Json<ChatRequest>,
) -> Sse<ReceiverStream<Result<Event, Infallible>>> {
    let (tx, rx) = mpsc::channel::<Result<Event, Infallible>>(64);
    let state = state.0;
    let backend = Backend::parse(req.backend.as_deref());

    tokio::spawn(async move {
        let result = match backend {
            Backend::Api => run_agent(&state, req.messages, &tx).await,
            Backend::Claude => run_claude(&state, req.messages, &tx).await,
            Backend::Codex => run_codex(&state, req.messages, &tx).await,
        };
        if let Err(e) = result {
            let _ = tx
                .send(Ok(sse("error", json!({ "message": e.to_string() }))))
                .await;
        }
        let _ = tx.send(Ok(sse("done", json!({})))).await;
    });

    Sse::new(ReceiverStream::new(rx)).keep_alive(KeepAlive::new().interval(Duration::from_secs(15)))
}

async fn run_agent(
    state: &ChatState,
    incoming: Vec<IncomingMessage>,
    tx: &mpsc::Sender<Result<Event, Infallible>>,
) -> anyhow::Result<()> {
    let mut messages: Vec<Value> = Vec::with_capacity(incoming.len() + 1);
    messages.push(json!({ "role": "system", "content": CHAT_SYSTEM }));
    for m in incoming {
        messages.push(json!({ "role": m.role, "content": m.content }));
    }

    let tools = tool_specs();

    for _ in 0..MAX_TOOL_ROUNDS {
        let body = json!({
            "model": MODEL,
            "messages": messages,
            "tools": tools,
            "tool_choice": "auto",
        });

        let resp = state.api.chat_raw(body).await?;
        let message = resp
            .pointer("/choices/0/message")
            .cloned()
            .ok_or_else(|| anyhow::anyhow!("no message in chat response"))?;

        let tool_calls = message
            .get("tool_calls")
            .and_then(|v| v.as_array())
            .cloned()
            .unwrap_or_default();

        if tool_calls.is_empty() {
            let content = message
                .get("content")
                .and_then(|v| v.as_str())
                .unwrap_or("")
                .to_owned();
            stream_text(tx, &content).await;
            return Ok(());
        }

        // Record the assistant turn (with its tool_calls) before the tool results.
        messages.push(message.clone());

        for call in &tool_calls {
            let id = call.get("id").and_then(|v| v.as_str()).unwrap_or("");
            let name = call
                .pointer("/function/name")
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let args: Value = call
                .pointer("/function/arguments")
                .and_then(|v| v.as_str())
                .and_then(|s| serde_json::from_str(s).ok())
                .unwrap_or_else(|| json!({}));

            let _ = tx
                .send(Ok(sse(
                    "tool",
                    json!({ "name": name, "status": "running" }),
                )))
                .await;

            let result = dispatch_tool(state, name, &args).await;

            let _ = tx
                .send(Ok(sse("tool", json!({ "name": name, "status": "done" }))))
                .await;

            messages.push(json!({
                "role": "tool",
                "tool_call_id": id,
                "content": result,
            }));
        }
    }

    stream_text(
        tx,
        "I wasn't able to finish answering that — I hit the tool-call limit. Try narrowing the question.",
    )
    .await;
    Ok(())
}

// ---------------------------------------------------------------------------
// Headless local agents (Claude Code / Codex) backed by the patcha MCP server
// ---------------------------------------------------------------------------

const PATCHA_TOOLS: &[&str] = &[
    "get_working_memory",
    "search_activity",
    "get_recent_activity",
    "get_activity_context",
    "get_session",
    "find_connected",
];

fn mcp_url(state: &ChatState) -> String {
    format!("http://127.0.0.1:{}/mcp/", state.port)
}

/// Flatten the transcript into a single prompt string for the headless CLIs,
/// which take one prompt rather than a structured message list.
fn build_transcript(messages: &[IncomingMessage]) -> String {
    if messages.len() == 1 {
        return messages[0].content.clone();
    }
    let mut out = String::new();
    for m in messages {
        let who = match m.role.as_str() {
            "assistant" => "Assistant",
            _ => "User",
        };
        out.push_str(&format!("{who}: {}\n\n", m.content.trim()));
    }
    out.push_str("Assistant:");
    out
}

/// Map a CLI tool name (e.g. `mcp__patcha__search_activity` or `patcha.search`)
/// back to the bare tool name the UI knows how to label.
fn strip_tool_prefix(name: &str) -> String {
    name.rsplit("__")
        .next()
        .unwrap_or(name)
        .rsplit('.')
        .next()
        .unwrap_or(name)
        .to_owned()
}

async fn run_claude(
    state: &ChatState,
    messages: Vec<IncomingMessage>,
    tx: &mpsc::Sender<Result<Event, Infallible>>,
) -> anyhow::Result<()> {
    let mcp_config = json!({
        "mcpServers": { "patcha": { "type": "http", "url": mcp_url(state) } }
    })
    .to_string();
    let allowed = PATCHA_TOOLS
        .iter()
        .map(|t| format!("mcp__patcha__{t}"))
        .collect::<Vec<_>>()
        .join(",");

    let mut cmd = Command::new("claude");
    cmd.arg("-p")
        .arg(build_transcript(&messages))
        .arg("--output-format")
        .arg("stream-json")
        .arg("--verbose")
        // Only the patcha MCP server — ignore the user's other configured servers.
        .arg("--mcp-config")
        .arg(&mcp_config)
        .arg("--strict-mcp-config")
        // Expose the patcha tools directly instead of deferring them behind the
        // ToolSearch tool, and skip the user's skills so the agent goes straight
        // to the activity tools.
        .arg("--settings")
        .arg(r#"{"toolSearchEnabled":false}"#)
        .arg("--disable-slash-commands")
        .arg("--allowed-tools")
        .arg(&allowed)
        .arg("--disallowed-tools")
        .arg("Bash,Edit,Write,WebFetch,WebSearch,Task")
        .arg("--append-system-prompt")
        .arg(CHAT_SYSTEM)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    if let Some(home) = dirs::home_dir() {
        cmd.current_dir(home);
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| anyhow::anyhow!("couldn't launch the Claude Code CLI (`claude`): {e}. Make sure it's installed and logged in."))?;
    let stdout = child.stdout.take().expect("piped stdout");
    let mut lines = BufReader::new(stdout).lines();

    let mut streamed = false;
    let mut tool_names: std::collections::HashMap<String, String> = Default::default();

    while let Some(line) = lines.next_line().await? {
        let Ok(v) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        match v.get("type").and_then(|t| t.as_str()) {
            Some("assistant") => {
                if let Some(content) = v.pointer("/message/content").and_then(|c| c.as_array()) {
                    for block in content {
                        match block.get("type").and_then(|t| t.as_str()) {
                            Some("text") => {
                                if let Some(t) = block.get("text").and_then(|t| t.as_str()) {
                                    if !t.is_empty() {
                                        stream_text(tx, t).await;
                                        streamed = true;
                                    }
                                }
                            }
                            Some("tool_use") => {
                                let id = block.get("id").and_then(|v| v.as_str()).unwrap_or("");
                                let raw = block.get("name").and_then(|v| v.as_str()).unwrap_or("");
                                let name = strip_tool_prefix(raw);
                                tool_names.insert(id.to_owned(), name.clone());
                                emit_tool(tx, &name, false).await;
                            }
                            _ => {}
                        }
                    }
                }
            }
            Some("user") => {
                if let Some(content) = v.pointer("/message/content").and_then(|c| c.as_array()) {
                    for block in content {
                        if block.get("type").and_then(|t| t.as_str()) == Some("tool_result") {
                            let id = block
                                .get("tool_use_id")
                                .and_then(|v| v.as_str())
                                .unwrap_or("");
                            if let Some(name) = tool_names.get(id) {
                                emit_tool(tx, name, true).await;
                            }
                        }
                    }
                }
            }
            Some("result") => {
                if !streamed {
                    if let Some(t) = v.get("result").and_then(|t| t.as_str()) {
                        stream_text(tx, t).await;
                    }
                }
                break;
            }
            _ => {}
        }
    }

    let _ = child.wait().await;
    Ok(())
}

async fn run_codex(
    state: &ChatState,
    messages: Vec<IncomingMessage>,
    tx: &mpsc::Sender<Result<Event, Infallible>>,
) -> anyhow::Result<()> {
    let last_msg_path = std::env::temp_dir().join(format!("patcha-codex-{}.txt", std::process::id()));
    let prompt = format!("{CHAT_SYSTEM}\n\n{}", build_transcript(&messages));

    let mut cmd = Command::new("codex");
    cmd.arg("exec")
        .arg(&prompt)
        .arg("--json")
        .arg("--sandbox")
        .arg("read-only")
        .arg("--color")
        .arg("never")
        .arg("-c")
        .arg(format!("mcp_servers.patcha.url=\"{}\"", mcp_url(state)))
        .arg("-c")
        .arg("approval_policy=\"never\"")
        .arg("-o")
        .arg(&last_msg_path)
        .stdout(std::process::Stdio::piped())
        .stderr(std::process::Stdio::null());
    if let Some(home) = dirs::home_dir() {
        cmd.current_dir(home);
    }

    let mut child = cmd
        .spawn()
        .map_err(|e| anyhow::anyhow!("couldn't launch the Codex CLI (`codex`): {e}. Make sure it's installed and logged in."))?;
    let stdout = child.stdout.take().expect("piped stdout");
    let mut lines = BufReader::new(stdout).lines();

    let mut streamed = false;

    while let Some(line) = lines.next_line().await? {
        let Ok(v) = serde_json::from_str::<Value>(&line) else {
            continue;
        };
        let event = v.get("type").and_then(|t| t.as_str()).unwrap_or("");
        let item = v.get("item");
        let item_type = item
            .and_then(|i| i.get("item_type").or_else(|| i.get("type")))
            .and_then(|t| t.as_str())
            .unwrap_or("");

        match (event, item_type) {
            ("item.started", "mcp_tool_call") => {
                emit_tool(tx, &codex_tool_name(item), false).await;
            }
            ("item.completed", "mcp_tool_call") => {
                emit_tool(tx, &codex_tool_name(item), true).await;
            }
            ("item.completed", "agent_message") | ("item.completed", "assistant_message") => {
                if let Some(t) = item.and_then(|i| i.get("text")).and_then(|t| t.as_str()) {
                    if !t.is_empty() {
                        stream_text(tx, t).await;
                        streamed = true;
                    }
                }
            }
            _ => {}
        }
    }

    let _ = child.wait().await;

    // Reliable fallback: codex writes the final message to the -o file.
    if !streamed {
        if let Ok(text) = tokio::fs::read_to_string(&last_msg_path).await {
            stream_text(tx, text.trim()).await;
        }
    }
    let _ = tokio::fs::remove_file(&last_msg_path).await;
    Ok(())
}

fn codex_tool_name(item: Option<&Value>) -> String {
    let raw = item
        .and_then(|i| i.get("tool").or_else(|| i.get("name")))
        .and_then(|t| t.as_str())
        .unwrap_or("");
    strip_tool_prefix(raw)
}

async fn emit_tool(tx: &mpsc::Sender<Result<Event, Infallible>>, name: &str, done: bool) {
    let status = if done { "done" } else { "running" };
    let _ = tx
        .send(Ok(sse("tool", json!({ "name": name, "status": status }))))
        .await;
}

// ---------------------------------------------------------------------------
// Backend availability
// ---------------------------------------------------------------------------

fn cli_available(name: &str) -> bool {
    let Ok(path) = std::env::var("PATH") else {
        return false;
    };
    std::env::split_paths(&path).any(|dir| {
        let candidate = dir.join(name);
        std::fs::metadata(&candidate).map(|m| m.is_file()).unwrap_or(false)
    })
}

pub async fn backends_handler() -> Json<Value> {
    Json(json!({
        "api": true,
        "claude": cli_available("claude"),
        "codex": cli_available("codex"),
    }))
}

/// Stream a final answer to the client in small chunks so it renders as it
/// arrives (the upstream call is non-streaming).
async fn stream_text(tx: &mpsc::Sender<Result<Event, Infallible>>, text: &str) {
    if text.is_empty() {
        return;
    }
    let mut buf = String::new();
    for word in text.split_inclusive(char::is_whitespace) {
        buf.push_str(word);
        if buf.len() >= 24 {
            let _ = tx.send(Ok(sse("token", json!({ "text": buf })))).await;
            buf = String::new();
            tokio::time::sleep(Duration::from_millis(12)).await;
        }
    }
    if !buf.is_empty() {
        let _ = tx.send(Ok(sse("token", json!({ "text": buf })))).await;
    }
}

fn sse(event: &str, data: Value) -> Event {
    Event::default().event(event).data(data.to_string())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn tool_specs_lists_all_six_tools() {
        let specs = tool_specs();
        let arr = specs.as_array().unwrap();
        assert_eq!(arr.len(), 6);
        let names: Vec<&str> = arr
            .iter()
            .map(|s| s.pointer("/function/name").and_then(|v| v.as_str()).unwrap())
            .collect();
        for expected in [
            "get_working_memory",
            "search_activity",
            "get_recent_activity",
            "get_activity_context",
            "get_session",
            "find_connected",
        ] {
            assert!(names.contains(&expected), "missing tool {expected}");
        }
    }

    #[test]
    fn search_activity_marks_query_required() {
        let specs = tool_specs();
        let search = specs
            .as_array()
            .unwrap()
            .iter()
            .find(|s| s.pointer("/function/name").and_then(|v| v.as_str()) == Some("search_activity"))
            .unwrap();
        let required = search
            .pointer("/function/parameters/required")
            .and_then(|v| v.as_array())
            .unwrap();
        assert_eq!(required, &vec![json!("query")]);
    }
}
