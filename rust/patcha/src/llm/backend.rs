use crate::{config::Config, llm::client::PatchaApiClient};
use anyhow::{bail, Context, Result};
use async_trait::async_trait;
use std::sync::Arc;
use tokio::process::Command;

/// A single-shot text-completion backend used by the daemon's background AI
/// features (categorization, summaries, compaction). Two implementations exist:
/// `PatchaApiClient` routes through patcha-api (needs a login token), and
/// `ClaudeCliBackend` shells out to the local `claude` CLI (no login required).
#[async_trait]
pub trait LlmBackend: Send + Sync {
    async fn chat_completion(&self, system: &str, user: &str, model: &str) -> Result<String>;
}

#[async_trait]
impl LlmBackend for PatchaApiClient {
    async fn chat_completion(&self, system: &str, user: &str, model: &str) -> Result<String> {
        PatchaApiClient::chat_completion(self, system, user, model).await
    }
}

/// Runs completions through the locally installed Claude Code CLI (`claude -p`).
/// Requires the CLI to be installed and logged in, but needs no patcha login.
pub struct ClaudeCliBackend;

#[async_trait]
impl LlmBackend for ClaudeCliBackend {
    async fn chat_completion(&self, system: &str, user: &str, _model: &str) -> Result<String> {
        let mut cmd = Command::new("claude");
        cmd.arg("-p")
            .arg(user)
            .arg("--append-system-prompt")
            .arg(system)
            .arg("--output-format")
            .arg("json")
            // These are single-shot text completions: no tools, skills, or slash
            // commands should ever run.
            .arg("--settings")
            .arg(r#"{"toolSearchEnabled":false}"#)
            .arg("--disable-slash-commands")
            .arg("--disallowed-tools")
            .arg("Bash,Edit,Write,Read,Glob,Grep,WebFetch,WebSearch,Task")
            .stdout(std::process::Stdio::piped())
            .stderr(std::process::Stdio::null());
        if let Some(home) = dirs::home_dir() {
            cmd.current_dir(home);
        }

        let output = cmd.output().await.map_err(|e| {
            anyhow::anyhow!(
                "couldn't launch the Claude Code CLI (`claude`): {e}. Make sure it's installed and logged in."
            )
        })?;

        if !output.status.success() {
            bail!("claude CLI exited with status {}", output.status);
        }

        let parsed: serde_json::Value = serde_json::from_slice(&output.stdout)
            .context("failed to parse claude CLI JSON output")?;

        if parsed.get("is_error").and_then(|v| v.as_bool()) == Some(true) {
            let msg = parsed
                .get("result")
                .and_then(|v| v.as_str())
                .unwrap_or("unknown error");
            bail!("claude CLI returned an error: {msg}");
        }

        parsed
            .get("result")
            .and_then(|v| v.as_str())
            .map(|s| s.to_owned())
            .context("claude CLI response missing `result` field")
    }
}

/// Build the LLM backend selected by config. `auto` (the default) uses the local
/// `claude` CLI when there is no patcha access token, and patcha-api otherwise.
pub fn build(cfg: &Config) -> Arc<dyn LlmBackend> {
    let use_claude = match cfg.llm_backend.as_str() {
        "claude" => true,
        "api" => false,
        _ => cfg.patcha_access_token.trim().is_empty(),
    };

    if use_claude {
        Arc::new(ClaudeCliBackend)
    } else {
        Arc::new(PatchaApiClient::new(cfg))
    }
}
