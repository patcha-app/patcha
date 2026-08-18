use anyhow::Result;
use dotenvy::from_path_override as dotenv_override;
use serde::{Deserialize, Serialize};
use std::path::PathBuf;

fn patcha_dir() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join(".patcha")
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Config {
    pub data_dir: PathBuf,
    pub db_path: PathBuf,
    pub log_level: String,
    pub collection_name: String,
    pub vector_size: usize,
    pub embedding_model_name: String,
    pub embedding_cache_dir: PathBuf,
    /// Instruction prefixed to user queries before embedding (BGE is trained
    /// asymmetrically: queries get this prefix, documents do not).
    pub query_instruction_prefix: String,
    pub max_events_per_day: u32,
    pub max_embedding_tokens: usize,
    pub embedding_chunk_overlap: usize,

    // Retrieval ranking
    pub similarity_floor: f32,
    pub recency_weight: f64,
    pub recency_lambda_days: f64,

    // Reranking (Phase D)
    pub enable_reranker: bool,
    pub reranker_model_name: String,
    pub rerank_candidate_pool: usize,
    pub max_pending_per_cycle: usize,
    pub working_memory_dedup_threshold: f32,
    pub daily_compaction_min_activities: usize,
    pub enable_hourly_summary: bool,
    pub session_gap_seconds: u64,
    pub poll_interval_seconds: u64,
    pub ax_poll_interval_seconds: u64,

    // Collector toggles
    pub enable_git_collector: bool,
    pub enable_browser_collector: bool,
    pub enable_terminal_collector: bool,
    pub enable_window_collector: bool,
    pub enable_accessibility_collector: bool,
    pub enable_activity_graph: bool,

    // Visual perception (Phase 1)
    pub enable_visual_prefilter: bool,
    pub visual_drop_threshold: f32,
    pub visual_cache_ttl_seconds: u64,
    pub visual_vector_size: usize,

    // Event-driven triggers (Phase 2)
    pub enable_event_triggers: bool,
    pub idle_timeout_seconds: u64,
    pub trigger_debounce_ms: u64,
    pub background_poll_interval_seconds: u64,

    // Gist captioning (Phase 3) — model resolved from resources_dir/models/fastvlm.
    pub enable_captioner: bool,
    pub caption_max_new_tokens: usize,

    // Patcha cloud API
    pub patcha_api_url: String,
    pub patcha_access_token: String,
    pub patcha_refresh_token: String,

    // LLM backend for background AI features: "auto" | "api" | "claude".
    // "auto" uses the local claude CLI when there is no access token.
    pub llm_backend: String,
}

impl Default for Config {
    fn default() -> Self {
        let base = patcha_dir();
        Self {
            data_dir: base.join("data"),
            db_path: base.join("patcha.db"),
            log_level: "INFO".into(),
            collection_name: "mem".into(),
            vector_size: 768,
            embedding_model_name: "BAAI/bge-base-en-v1.5".into(),
            embedding_cache_dir: base.join("models"),
            query_instruction_prefix: "Represent this sentence for searching relevant passages: "
                .into(),
            max_events_per_day: 10_000,
            max_embedding_tokens: 8191,
            embedding_chunk_overlap: 100,
            similarity_floor: 0.0,
            recency_weight: 0.02,
            recency_lambda_days: 30.0,
            enable_reranker: true,
            reranker_model_name: "BAAI/bge-reranker-base".into(),
            rerank_candidate_pool: 40,
            max_pending_per_cycle: 500,
            working_memory_dedup_threshold: 0.95,
            daily_compaction_min_activities: 2,
            enable_hourly_summary: true,
            session_gap_seconds: 600,
            poll_interval_seconds: 60,
            ax_poll_interval_seconds: 5,
            enable_git_collector: true,
            enable_browser_collector: true,
            enable_terminal_collector: true,
            enable_window_collector: true,
            enable_accessibility_collector: true,
            enable_activity_graph: true,
            enable_visual_prefilter: true,
            visual_drop_threshold: 0.97,
            visual_cache_ttl_seconds: 7200,
            visual_vector_size: 512,
            enable_event_triggers: true,
            idle_timeout_seconds: 120,
            trigger_debounce_ms: 500,
            background_poll_interval_seconds: 15,
            enable_captioner: true,
            caption_max_new_tokens: 56,
            patcha_api_url: "https://api.patcha.app".into(),
            patcha_access_token: String::new(),
            patcha_refresh_token: String::new(),
            llm_backend: "auto".into(),
        }
    }
}

impl Config {
    pub fn load() -> Result<Self> {
        let project_root = std::env::current_exe()
            .ok()
            .and_then(|p| p.parent().map(|p| p.to_path_buf()))
            .unwrap_or_else(|| PathBuf::from("."));

        let env_name = std::env::var("PATCHA_ENV").unwrap_or_else(|_| "development".into());

        // Load env files in priority order (later calls win)
        let _ = dotenv_override(project_root.join(format!(".env.{env_name}")));
        let _ = dotenv_override(project_root.join(".env"));
        let _ = dotenv_override(patcha_dir().join(".env"));

        fn env_bool(key: &str, default: bool) -> bool {
            std::env::var(key)
                .map(|v| !matches!(v.to_lowercase().as_str(), "false" | "0" | "no"))
                .unwrap_or(default)
        }

        fn env_path(key: &str, default: PathBuf) -> PathBuf {
            std::env::var(key).map(PathBuf::from).unwrap_or(default)
        }

        fn env_str(key: &str, default: &str) -> String {
            std::env::var(key).unwrap_or_else(|_| default.to_owned())
        }

        fn env_u64(key: &str, default: u64) -> u64 {
            std::env::var(key)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(default)
        }

        fn env_usize(key: &str, default: usize) -> usize {
            std::env::var(key)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(default)
        }

        fn env_f32(key: &str, default: f32) -> f32 {
            std::env::var(key)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(default)
        }

        fn env_f64(key: &str, default: f64) -> f64 {
            std::env::var(key)
                .ok()
                .and_then(|v| v.parse().ok())
                .unwrap_or(default)
        }

        let base = patcha_dir();
        let data_dir = env_path("DATA_DIR", base.join("data"));

        // Read tokens from their dedicated files if env vars are empty
        let access_token = std::env::var("PATCHA_ACCESS_TOKEN")
            .unwrap_or_default()
            .or_else_if_empty(|| {
                std::fs::read_to_string(base.join("access_token"))
                    .map(|s| s.trim().to_owned())
                    .unwrap_or_default()
            });

        let refresh_token = std::env::var("PATCHA_REFRESH_TOKEN")
            .unwrap_or_default()
            .or_else_if_empty(|| {
                std::fs::read_to_string(base.join(".env"))
                    .ok()
                    .and_then(|s| {
                        s.lines()
                            .find(|l| l.starts_with("PATCHA_REFRESH_TOKEN="))
                            .map(|l| l.split_once('=').map(|x| x.1).unwrap_or("").to_owned())
                    })
                    .unwrap_or_default()
            });

        Ok(Self {
            data_dir: data_dir.clone(),
            db_path: env_path("PATCHA_DB_PATH", base.join("patcha.db")),
            log_level: env_str("LOG_LEVEL", "INFO"),
            collection_name: env_str("COLLECTION_NAME", "mem"),
            vector_size: env_usize("VECTOR_SIZE", 768),
            embedding_model_name: env_str("EMBEDDING_MODEL", "BAAI/bge-base-en-v1.5"),
            embedding_cache_dir: env_path("EMBEDDING_CACHE_DIR", base.join("models")),
            query_instruction_prefix: env_str(
                "QUERY_INSTRUCTION_PREFIX",
                "Represent this sentence for searching relevant passages: ",
            ),
            max_events_per_day: env_u64("MAX_EVENTS_PER_DAY", 10_000) as u32,
            max_embedding_tokens: env_usize("MAX_EMBEDDING_TOKENS", 8191),
            embedding_chunk_overlap: env_usize("EMBEDDING_CHUNK_OVERLAP", 100),
            similarity_floor: env_f32("SIMILARITY_FLOOR", 0.0),
            recency_weight: env_f64("RECENCY_WEIGHT", 0.02),
            recency_lambda_days: env_f64("RECENCY_LAMBDA_DAYS", 30.0),
            enable_reranker: env_bool("ENABLE_RERANKER", true),
            reranker_model_name: env_str("RERANKER_MODEL", "BAAI/bge-reranker-base"),
            rerank_candidate_pool: env_usize("RERANK_CANDIDATE_POOL", 40),
            max_pending_per_cycle: env_usize("MAX_PENDING_PER_CYCLE", 500),
            working_memory_dedup_threshold: env_f32("WORKING_MEMORY_DEDUP_THRESHOLD", 0.95),
            daily_compaction_min_activities: env_usize("DAILY_COMPACTION_MIN_ACTIVITIES", 2),
            enable_hourly_summary: env_bool("ENABLE_HOURLY_SUMMARY", true),
            session_gap_seconds: env_u64("SESSION_GAP_SECONDS", 600),
            poll_interval_seconds: env_u64("POLL_INTERVAL", 60),
            ax_poll_interval_seconds: env_u64("AX_POLL_INTERVAL", 5),
            enable_git_collector: env_bool("ENABLE_GIT_COLLECTOR", true),
            enable_browser_collector: env_bool("ENABLE_BROWSER_COLLECTOR", true),
            enable_terminal_collector: env_bool("ENABLE_TERMINAL_COLLECTOR", true),
            enable_window_collector: env_bool("ENABLE_WINDOW_COLLECTOR", true),
            enable_accessibility_collector: env_bool("ENABLE_ACCESSIBILITY_COLLECTOR", true),
            enable_activity_graph: env_bool("ENABLE_ACTIVITY_GRAPH", true),
            enable_visual_prefilter: env_bool("ENABLE_VISUAL_PREFILTER", true),
            visual_drop_threshold: env_f32("VISUAL_DROP_THRESHOLD", 0.97),
            visual_cache_ttl_seconds: env_u64("VISUAL_CACHE_TTL_SECONDS", 7200),
            visual_vector_size: env_usize("VISUAL_VECTOR_SIZE", 512),
            enable_event_triggers: env_bool("ENABLE_EVENT_TRIGGERS", true),
            idle_timeout_seconds: env_u64("IDLE_TIMEOUT_SECONDS", 120),
            trigger_debounce_ms: env_u64("TRIGGER_DEBOUNCE_MS", 500),
            background_poll_interval_seconds: env_u64("BACKGROUND_POLL_INTERVAL", 15),
            enable_captioner: env_bool("ENABLE_CAPTIONER", true),
            caption_max_new_tokens: env_usize("CAPTION_MAX_NEW_TOKENS", 56),
            patcha_api_url: env_str("PATCHA_API_URL", "https://api.patcha.app"),
            patcha_access_token: access_token,
            patcha_refresh_token: refresh_token,
            llm_backend: env_str("PATCHA_LLM_BACKEND", "auto"),
        })
    }
}

// Helper trait to fall back to a closure when a string is empty
trait OrElseIfEmpty {
    fn or_else_if_empty(self, f: impl FnOnce() -> String) -> String;
}

impl OrElseIfEmpty for String {
    fn or_else_if_empty(self, f: impl FnOnce() -> String) -> String {
        if self.is_empty() {
            f()
        } else {
            self
        }
    }
}
