use crate::config::Config;
use anyhow::{bail, Context, Result};
use serde::{Deserialize, Serialize};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};

#[allow(dead_code)]
const TOKEN_FILE: &str = ".patcha/access_token";
#[allow(dead_code)]
const ENV_FILE: &str = ".patcha/.env";

// ---------------------------------------------------------------------------
// Request / response shapes (OpenAI-compatible)
// ---------------------------------------------------------------------------

#[derive(Serialize)]
#[allow(dead_code)]
struct ChatRequest<'a> {
    model: &'a str,
    messages: Vec<serde_json::Value>,
}

#[derive(Deserialize)]
struct ChatResponse {
    choices: Vec<ChatChoice>,
}

#[derive(Deserialize)]
struct ChatChoice {
    message: ChatMessage,
}

#[derive(Deserialize)]
struct ChatMessage {
    content: String,
}

#[derive(Serialize)]
#[allow(dead_code)]
struct EmbedRequest<'a> {
    model: &'a str,
    input: Vec<String>,
}

#[derive(Deserialize)]
struct EmbedResponse {
    data: Vec<EmbedData>,
}

#[derive(Deserialize)]
struct EmbedData {
    embedding: Vec<f32>,
}

// ---------------------------------------------------------------------------
// PatchaApiClient
// ---------------------------------------------------------------------------

/// Async HTTP client that routes LLM calls through patcha-api.
/// Handles token storage, refresh, and automatic 401 recovery.
pub struct PatchaApiClient {
    base_url: String,
    http: reqwest::Client,
    /// In-memory tokens (shared across clones via Arc<Mutex>)
    tokens: Arc<Mutex<Tokens>>,
}

#[derive(Default, Clone)]
struct Tokens {
    access: String,
    refresh: String,
}

impl PatchaApiClient {
    pub fn new(cfg: &Config) -> Self {
        let access = read_token_file()
            .filter(|t| !t.is_empty())
            .unwrap_or_else(|| cfg.patcha_access_token.clone());

        let refresh = cfg.patcha_refresh_token.clone();

        Self {
            base_url: cfg.patcha_api_url.trim_end_matches('/').to_owned(),
            http: reqwest::Client::builder()
                .timeout(std::time::Duration::from_secs(120))
                .build()
                .expect("reqwest client"),
            tokens: Arc::new(Mutex::new(Tokens { access, refresh })),
        }
    }

    // -----------------------------------------------------------------------
    // Core LLM API
    // -----------------------------------------------------------------------

    /// Send a chat completion request; returns the assistant message content.
    pub async fn chat_completion(
        &self,
        system: &str,
        user: &str,
        model: &str,
    ) -> Result<String> {
        let messages = vec![
            serde_json::json!({"role": "system", "content": system}),
            serde_json::json!({"role": "user", "content": user}),
        ];
        let body = serde_json::json!({ "model": model, "messages": messages });

        let resp = self.post_with_auth("/api/v1/llm/chat/completions", &body).await?;
        let parsed: ChatResponse = resp
            .json()
            .await
            .context("failed to parse chat completion response")?;

        parsed
            .choices
            .into_iter()
            .next()
            .map(|c| c.message.content)
            .context("empty choices in chat response")
    }

    /// Send a raw OpenAI-compatible chat request and return the parsed JSON
    /// response. The caller owns the body shape (`model`, `messages`, `tools`,
    /// `tool_choice`, ...) and reads `choices[0].message` directly — used by the
    /// agentic chat loop which needs access to `tool_calls`.
    pub async fn chat_raw(&self, body: serde_json::Value) -> Result<serde_json::Value> {
        let resp = self
            .post_with_auth("/api/v1/llm/chat/completions", &body)
            .await?;
        resp.json()
            .await
            .context("failed to parse chat completion response")
    }

    /// Embed a batch of texts via the API (fallback; local fastembed is preferred).
    pub async fn embed_remote(&self, texts: Vec<String>, model: &str) -> Result<Vec<Vec<f32>>> {
        let body = serde_json::json!({ "model": model, "input": texts });
        let resp = self.post_with_auth("/api/v1/llm/embeddings", &body).await?;
        let parsed: EmbedResponse = resp
            .json()
            .await
            .context("failed to parse embeddings response")?;
        Ok(parsed.data.into_iter().map(|d| d.embedding).collect())
    }

    // -----------------------------------------------------------------------
    // Auth
    // -----------------------------------------------------------------------

    pub async fn login(&self, email: &str, password: &str) -> Result<bool> {
        let resp = self
            .http
            .post(format!("{}/api/v1/auth/login", self.base_url))
            .json(&serde_json::json!({"email": email, "password": password}))
            .send()
            .await?;

        if resp.status() != 200 {
            return Ok(false);
        }
        let data: serde_json::Value = resp.json().await?;
        self.store_tokens(
            data["access_token"].as_str().unwrap_or(""),
            data["refresh_token"].as_str().unwrap_or(""),
        );
        Ok(true)
    }

    pub async fn register(&self, email: &str, password: &str) -> Result<bool> {
        let resp = self
            .http
            .post(format!("{}/api/v1/auth/register", self.base_url))
            .json(&serde_json::json!({"email": email, "password": password}))
            .send()
            .await?;

        if !matches!(resp.status().as_u16(), 200 | 201) {
            return Ok(false);
        }
        let data: serde_json::Value = resp.json().await?;
        self.store_tokens(
            data["access_token"].as_str().unwrap_or(""),
            data["refresh_token"].as_str().unwrap_or(""),
        );
        Ok(true)
    }

    pub async fn logout(&self) -> Result<()> {
        let refresh = self.tokens.lock().unwrap().refresh.clone();
        if !refresh.is_empty() {
            let _ = self
                .http
                .post(format!("{}/api/v1/auth/logout", self.base_url))
                .json(&serde_json::json!({"refresh_token": refresh}))
                .bearer_auth(self.access_token())
                .send()
                .await;
        }
        clear_tokens_file();
        let mut t = self.tokens.lock().unwrap();
        t.access = String::new();
        t.refresh = String::new();
        Ok(())
    }

    pub async fn check_auth(&self) -> bool {
        let resp = self
            .http
            .get(format!("{}/api/v1/auth/me", self.base_url))
            .bearer_auth(self.access_token())
            .send()
            .await;

        match resp {
            Ok(r) if r.status() == 200 => true,
            Ok(r) if r.status() == 401 => {
                if self.recover_auth().await {
                    self.http
                        .get(format!("{}/api/v1/auth/me", self.base_url))
                        .bearer_auth(self.access_token())
                        .send()
                        .await
                        .map(|r| r.status() == 200)
                        .unwrap_or(false)
                } else {
                    false
                }
            }
            _ => false,
        }
    }

    pub async fn check_update(&self, version: &str) -> Option<serde_json::Value> {
        self.http
            .get(format!(
                "{}/api/v1/updates/check?version={version}",
                self.base_url
            ))
            .send()
            .await
            .ok()
            .filter(|r| r.status() == 200)
            .and_then(|r| futures_executor_block_on(r.json()).ok())
    }

    pub async fn list_models(&self) -> Vec<serde_json::Value> {
        self.http
            .get(format!("{}/api/v1/models/", self.base_url))
            .send()
            .await
            .ok()
            .filter(|r| r.status() == 200)
            .and_then(|r| futures_executor_block_on(r.json::<Vec<serde_json::Value>>()).ok())
            .unwrap_or_default()
    }

    pub fn is_authenticated(&self) -> bool {
        !self.access_token().is_empty()
    }

    // -----------------------------------------------------------------------
    // Internal helpers
    // -----------------------------------------------------------------------

    fn access_token(&self) -> String {
        self.tokens.lock().unwrap().access.clone()
    }

    async fn post_with_auth(
        &self,
        path: &str,
        body: &serde_json::Value,
    ) -> Result<reqwest::Response> {
        let url = format!("{}{path}", self.base_url);
        let resp = self
            .http
            .post(&url)
            .bearer_auth(self.access_token())
            .json(body)
            .send()
            .await?;

        if resp.status() == 401 {
            if self.recover_auth().await {
                return Ok(self
                    .http
                    .post(&url)
                    .bearer_auth(self.access_token())
                    .json(body)
                    .send()
                    .await?);
            }
            bail!("not authenticated — run: patcha login");
        }

        if !resp.status().is_success() {
            bail!("API error {}: {path}", resp.status());
        }

        Ok(resp)
    }

    /// Try to recover from a 401: reload token file first, then try refresh exchange.
    async fn recover_auth(&self) -> bool {
        self.reload_token_file() || self.refresh_tokens().await
    }

    /// Re-read the access token written by the macOS app.
    fn reload_token_file(&self) -> bool {
        if let Some(token) = read_token_file().filter(|t| !t.is_empty()) {
            let mut t = self.tokens.lock().unwrap();
            if token != t.access {
                t.access = token;
                return true;
            }
        }
        false
    }

    async fn refresh_tokens(&self) -> bool {
        let refresh = self.tokens.lock().unwrap().refresh.clone();
        if refresh.is_empty() {
            return false;
        }

        let resp = self
            .http
            .post(format!("{}/api/v1/auth/refresh", self.base_url))
            .json(&serde_json::json!({"refresh_token": refresh}))
            .send()
            .await;

        match resp {
            Ok(r) if r.status() == 200 => {
                if let Ok(data) = r.json::<serde_json::Value>().await {
                    self.store_tokens(
                        data["access_token"].as_str().unwrap_or(""),
                        data["refresh_token"].as_str().unwrap_or(""),
                    );
                    return true;
                }
                false
            }
            _ => false,
        }
    }

    fn store_tokens(&self, access: &str, refresh: &str) {
        let mut t = self.tokens.lock().unwrap();
        t.access = access.to_owned();
        t.refresh = refresh.to_owned();
        drop(t);
        save_tokens_file(access, refresh);
    }
}

// ---------------------------------------------------------------------------
// Token file helpers
// ---------------------------------------------------------------------------

fn patcha_dir() -> PathBuf {
    dirs::home_dir().unwrap_or_default().join(".patcha")
}

fn read_token_file() -> Option<String> {
    std::fs::read_to_string(patcha_dir().join("access_token"))
        .ok()
        .map(|s| s.trim().to_owned())
        .filter(|s| !s.is_empty())
}

fn save_tokens_file(access: &str, refresh: &str) {
    let env_path = patcha_dir().join(".env");
    let _ = std::fs::create_dir_all(patcha_dir());

    let existing = std::fs::read_to_string(&env_path).unwrap_or_default();
    let mut map: std::collections::HashMap<String, String> = existing
        .lines()
        .filter(|l| l.contains('='))
        .map(|l| {
            let (k, v) = l.split_once('=').unwrap();
            (k.to_owned(), v.to_owned())
        })
        .collect();

    map.insert("PATCHA_ACCESS_TOKEN".into(), access.to_owned());
    map.insert("PATCHA_REFRESH_TOKEN".into(), refresh.to_owned());

    let content = map
        .iter()
        .map(|(k, v)| format!("{k}={v}"))
        .collect::<Vec<_>>()
        .join("\n")
        + "\n";

    let _ = std::fs::write(&env_path, content);

    // Also write the short-lived access token file
    let _ = std::fs::write(patcha_dir().join("access_token"), access);
}

fn clear_tokens_file() {
    let env_path = patcha_dir().join(".env");
    if !env_path.exists() {
        return;
    }
    let existing = std::fs::read_to_string(&env_path).unwrap_or_default();
    let filtered: String = existing
        .lines()
        .filter(|l| {
            !l.starts_with("PATCHA_ACCESS_TOKEN=") && !l.starts_with("PATCHA_REFRESH_TOKEN=")
        })
        .map(|l| format!("{l}\n"))
        .collect();
    let _ = std::fs::write(&env_path, filtered);
}

// Tiny synchronous executor for one-off async calls in non-async contexts.
// Only used for the check_update / list_models helpers that return futures
// but are called from sync contexts. In the daemon they'd be awaited normally.
fn futures_executor_block_on<F: std::future::Future>(f: F) -> F::Output {
    tokio::task::block_in_place(|| tokio::runtime::Handle::current().block_on(f))
}
