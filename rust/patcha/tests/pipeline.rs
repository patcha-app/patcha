//! Full-pipeline tests: drive real production code from raw signal to retrieval.
//!
//! Covered paths:
//!   * OCR (mock app window -> `ocr` helper) -> Screen event -> embed -> store
//!     -> vector search retrieves it by meaning.
//!   * Git staging snapshots -> `GitCollector::collect_staging_events` -> event
//!     -> embed -> store.
//!   * `build_embedding_text` extraction for screen/git events (deterministic).
//!
//! The embedding stage uses the real `fastembed` BGE model. Tests that need it
//! skip cleanly when the model can't be loaded (offline + no cache).

mod common;

use common::{load_font, run_ocr, MockWindow};
use patcha::{
    collectors::git::GitCollector,
    config::Config,
    db::{store::VectorStore, Db},
    embedding::Embedder,
    models::{Event, EventType},
    process::{build_embedding_text, EventPreprocessor},
};
use std::sync::Arc;

/// A Config pointed at a throwaway data dir / db. The embedding model cache is
/// left at its default (`~/.patcha/models`) so an already-downloaded model is
/// reused instead of re-fetched.
fn test_config(tmp: &std::path::Path) -> Config {
    let mut cfg = Config::default();
    cfg.data_dir = tmp.join("data");
    cfg.db_path = tmp.join("patcha.db");
    std::fs::create_dir_all(&cfg.data_dir).unwrap();
    cfg
}

/// Try to build the real embedder; `None` (with a printed note) if unavailable.
fn try_embedder(cfg: &Config) -> Option<Arc<Embedder>> {
    match Embedder::new(cfg) {
        Ok(e) => Some(Arc::new(e)),
        Err(e) => {
            eprintln!("skipping: embedding model unavailable ({e})");
            None
        }
    }
}

// ---------------------------------------------------------------------------
// build_embedding_text — deterministic, always runs
// ---------------------------------------------------------------------------

#[test]
fn screen_event_embedding_text_prepends_gist() {
    let mut e = Event::new(EventType::Screen, "Visual Studio Code — main.rs: fn main() {}");
    e.metadata
        .insert("gist".into(), serde_json::json!("Editing the Rust entrypoint"));
    e.project = Some("patcha".into());

    let text = build_embedding_text(&e);
    assert!(text.starts_with("Editing the Rust entrypoint | "), "got: {text}");
    assert!(text.contains("fn main"), "got: {text}");
    assert!(text.ends_with("[patcha]"), "project tag missing: {text}");
}

#[test]
fn git_commit_embedding_text_extracts_message_and_files() {
    let raw = serde_json::json!({
        "hash": "abc123",
        "message": "feat: rust rewrite of the daemon",
        "files_changed": ["src/main.rs", "src/lib.rs"],
        "diff": "diff --git a/src/main.rs b/src/main.rs",
    })
    .to_string();
    let e = Event::new(EventType::GitCommit, raw);

    let text = build_embedding_text(&e);
    assert!(text.contains("feat: rust rewrite of the daemon"), "got: {text}");
    assert!(text.contains("src/main.rs"), "got: {text}");
    assert!(text.contains("files:"), "got: {text}");
}

// ---------------------------------------------------------------------------
// Git staging collector — deterministic, reads synthetic snapshots from data_dir
// ---------------------------------------------------------------------------

#[test]
fn git_staging_collector_emits_event_for_newly_staged_file() {
    let tmp = tempfile::tempdir().unwrap();
    let data_dir = tmp.path().join("data");
    std::fs::create_dir_all(&data_dir).unwrap();

    let t0 = chrono::Utc::now() - chrono::Duration::minutes(10);
    let t1 = chrono::Utc::now() - chrono::Duration::minutes(9);
    let repo = "/Users/dev/projects/patcha";

    let snapshots = format!(
        "{}\n{}\n",
        serde_json::json!({
            "timestamp": t0.to_rfc3339(),
            "repo": repo,
            "staged": [],
            "unstaged": [],
            "untracked": [],
            "staged_diff": "",
        }),
        serde_json::json!({
            "timestamp": t1.to_rfc3339(),
            "repo": repo,
            "staged": ["src/perception/captioner.rs"],
            "unstaged": [],
            "untracked": [],
            "staged_diff": "+ fn caption()",
        }),
    );
    std::fs::write(data_dir.join("git_stage_snapshots.jsonl"), snapshots).unwrap();

    let collector = GitCollector::new(data_dir);
    let since = chrono::Utc::now() - chrono::Duration::hours(1);
    let events = collector.collect_staging_events(since);

    assert_eq!(events.len(), 1, "expected one staging event, got {}", events.len());
    let e = &events[0];
    assert_eq!(e.event_type, EventType::GitStaged);
    assert_eq!(e.project.as_deref(), Some("patcha"));
    assert!(
        e.raw_content.contains("src/perception/captioner.rs"),
        "raw: {}",
        e.raw_content
    );
    assert!(e.raw_content.contains("staged:"), "raw: {}", e.raw_content);
}

// ---------------------------------------------------------------------------
// Full pipeline: OCR -> Screen event -> embed -> store -> semantic retrieval
// ---------------------------------------------------------------------------

#[test]
fn ocr_to_store_to_search_roundtrip() {
    let tmp = tempfile::tempdir().unwrap();
    let cfg = test_config(tmp.path());

    let Some(embedder) = try_embedder(&cfg) else {
        return;
    };

    // 1. OCR a mock VS Code window into text (the real perception input).
    let font = load_font();
    let win = MockWindow::new(
        "Visual Studio Code",
        "embedder.rs — patcha",
        &[
            "struct MobileClipEmbedder drives the core ml helper",
            "embed a screenshot into a vector",
            "cosine similarity against the app cache",
        ],
    );
    let (_dir, path) = win.render(&font);
    let Some(ocr_text) = run_ocr(&path) else {
        eprintln!("skipping: `ocr` helper not built");
        return;
    };
    assert!(!ocr_text.trim().is_empty(), "OCR returned no text");

    // 2. Build the events the way the collectors do.
    let screen = {
        let mut e = Event::new(
            EventType::Screen,
            format!("Visual Studio Code — embedder.rs — patcha: {ocr_text}"),
        );
        e.source = Some("accessibility".into());
        e.metadata.insert("app_name".into(), serde_json::json!("Visual Studio Code"));
        e
    };
    let git = {
        let raw = serde_json::json!({
            "hash": "deadbeef",
            "message": "feat: add hdbscan clustering of activities",
            "files_changed": ["src/cli/cluster.rs"],
            "diff": "",
        })
        .to_string();
        let mut e = Event::new(EventType::GitCommit, raw);
        e.source = Some("git".into());
        e
    };
    let distractor = Event::new(
        EventType::Terminal,
        serde_json::json!({ "command": "brew upgrade --greedy" }).to_string(),
    );

    // 3. Embed (real model) and store.
    let preprocessor = EventPreprocessor::new(&cfg, embedder.clone());
    let processed = preprocessor.process_events(vec![
        screen.clone(),
        git.clone(),
        distractor.clone(),
    ]);
    assert_eq!(processed.len(), 3, "all events should embed");
    assert!(processed.iter().all(|e| e.embedding.is_some()));

    let db = Db::open(&cfg.db_path).expect("open db");
    let store = VectorStore::new(db);
    store.store_events(&processed).expect("store events");

    // 4a. A query about the editor content should retrieve the screen event.
    let q = embedder
        .embed_one("looking at the mobileclip embedder source code in the editor")
        .unwrap();
    let hits = store.search_events(&q, 3, None).unwrap();
    assert!(!hits.is_empty(), "vector search returned nothing");
    let top_ids: Vec<&str> = hits.iter().take(2).map(|h| h.event.id.as_str()).collect();
    assert!(
        top_ids.contains(&screen.id.as_str()),
        "screen event not in top-2 for editor query; got {top_ids:?}"
    );

    // 4b. A query about the commit should retrieve the git event.
    let q = embedder
        .embed_one("the git commit that added clustering of activities")
        .unwrap();
    let hits = store.search_events(&q, 3, None).unwrap();
    let top_ids: Vec<&str> = hits.iter().take(2).map(|h| h.event.id.as_str()).collect();
    assert!(
        top_ids.contains(&git.id.as_str()),
        "git event not in top-2 for commit query; got {top_ids:?}"
    );
}
