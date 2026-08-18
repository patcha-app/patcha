use crate::{
    config::Config,
    embedding::{chunk_text, Embedder},
    models::{Event, EventType},
};
use anyhow::Result;
use std::path::{Path, PathBuf};
use std::sync::Arc;

// ---------------------------------------------------------------------------
// PendingEventStore
// ---------------------------------------------------------------------------

/// JSONL file of events whose embedding failed — retried on next daemon cycle.
pub struct PendingEventStore {
    path: PathBuf,
}

impl PendingEventStore {
    pub fn new(data_dir: &Path) -> Self {
        Self {
            path: data_dir.join("pending_events.jsonl"),
        }
    }

    pub fn save(&self, event: &Event) -> Result<()> {
        if let Some(parent) = self.path.parent() {
            std::fs::create_dir_all(parent)?;
        }
        use std::io::Write;
        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.path)?;
        writeln!(f, "{}", serde_json::to_string(event)?)?;
        Ok(())
    }

    pub fn load_all(&self) -> Result<Vec<Event>> {
        if !self.path.exists() {
            return Ok(Vec::new());
        }
        let content = std::fs::read_to_string(&self.path)?;
        let mut events = Vec::new();
        for line in content.lines() {
            let line = line.trim();
            if line.is_empty() {
                continue;
            }
            match serde_json::from_str::<Event>(line) {
                Ok(e) => events.push(e),
                Err(e) => tracing::warn!(error=%e, "skipping corrupt pending event"),
            }
        }
        Ok(events)
    }

    pub fn clear(&self) -> Result<()> {
        if self.path.exists() {
            std::fs::remove_file(&self.path)?;
        }
        Ok(())
    }

    /// Atomically replace the file with `events` (or delete if empty).
    pub fn replace(&self, events: &[Event]) -> Result<()> {
        if events.is_empty() {
            return self.clear();
        }
        let tmp = self.path.with_extension("jsonl.tmp");
        {
            use std::io::Write;
            let mut f = std::fs::File::create(&tmp)?;
            for event in events {
                writeln!(f, "{}", serde_json::to_string(event)?)?;
            }
        }
        std::fs::rename(tmp, &self.path)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Semantic text extraction
// ---------------------------------------------------------------------------

/// Build the text that will be embedded for a given event.
/// Mirrors Python's `_build_embedding_text`.
pub fn build_embedding_text(event: &Event) -> String {
    let text = match event.event_type {
        EventType::Browser => {
            if let Ok(data) = serde_json::from_str::<serde_json::Value>(&event.raw_content) {
                let parts: Vec<&str> = [
                    data.get("title").and_then(|v| v.as_str()),
                    data.get("domain").and_then(|v| v.as_str()),
                ]
                .into_iter()
                .flatten()
                .collect();
                if parts.is_empty() {
                    event.raw_content.clone()
                } else {
                    parts.join(" | ")
                }
            } else {
                event.raw_content.clone()
            }
        }

        EventType::Terminal => {
            if let Ok(data) = serde_json::from_str::<serde_json::Value>(&event.raw_content) {
                data.get("command")
                    .and_then(|v| v.as_str())
                    .unwrap_or(&event.raw_content)
                    .to_owned()
            } else {
                event.raw_content.clone()
            }
        }

        EventType::GitCommit | EventType::GitStash => {
            if let Ok(data) = serde_json::from_str::<serde_json::Value>(&event.raw_content) {
                let mut parts: Vec<String> = Vec::new();
                if let Some(msg) = data.get("message").and_then(|v| v.as_str()) {
                    parts.push(msg.to_owned());
                }
                if let Some(files) = data.get("files_changed").and_then(|v| v.as_array()) {
                    let file_list: Vec<&str> =
                        files.iter().filter_map(|v| v.as_str()).take(20).collect();
                    if !file_list.is_empty() {
                        parts.push(format!("files: {}", file_list.join(", ")));
                    }
                }
                if let Some(diff) = data.get("diff").and_then(|v| v.as_str()) {
                    parts.push(diff.to_owned());
                }
                if parts.is_empty() {
                    event.raw_content.clone()
                } else {
                    parts.join(" | ")
                }
            } else {
                event.raw_content.clone()
            }
        }

        EventType::Screen => {
            // Prepend the VLM gist so retrieval works on what the user was doing,
            // not just literal on-screen text: gist | app — window: ocr_text.
            match event.metadata.get("gist").and_then(|v| v.as_str()) {
                Some(gist) if !gist.is_empty() => format!("{gist} | {}", event.raw_content),
                _ => event.raw_content.clone(),
            }
        }

        _ => event.raw_content.clone(),
    };

    let text = text.trim().to_owned();
    if let Some(proj) = &event.project {
        if !proj.is_empty() {
            return format!("{text} [{proj}]");
        }
    }
    text
}

// ---------------------------------------------------------------------------
// EventPreprocessor
// ---------------------------------------------------------------------------

pub struct EventPreprocessor {
    embedder: Arc<Embedder>,
    pending_store: PendingEventStore,
    max_pending_per_cycle: usize,
    chunk_overlap: usize,
}

impl EventPreprocessor {
    pub fn new(cfg: &Config, embedder: Arc<Embedder>) -> Self {
        Self {
            embedder,
            pending_store: PendingEventStore::new(&cfg.data_dir),
            max_pending_per_cycle: cfg.max_pending_per_cycle,
            chunk_overlap: cfg.embedding_chunk_overlap,
        }
    }

    // -----------------------------------------------------------------------
    // Process a batch of fresh events
    // -----------------------------------------------------------------------

    /// Embed `events`, returning the embedded events.
    /// Events that fail to embed are saved to the pending store for retry.
    pub fn process_events(&self, events: Vec<Event>) -> Vec<Event> {
        let mut processed = Vec::new();
        for event in events {
            match self.process_one(event) {
                Ok(chunks) => processed.extend(chunks),
                Err(e) => {
                    tracing::warn!(error=%e, "embedding failed — queuing for retry");
                }
            }
        }
        processed
    }

    fn process_one(&self, event: Event) -> Result<Vec<Event>> {
        let text = build_embedding_text(&event);
        if text.is_empty() {
            return Ok(Vec::new());
        }

        let max_tokens = self.embedder.effective_max_tokens;
        let chunks = chunk_text(&text, max_tokens, self.chunk_overlap)?;

        if chunks.len() == 1 {
            let mut e = event;
            e.embedding = Some(self.embedder.embed_one(&chunks[0])?);
            return Ok(vec![e]);
        }

        tracing::debug!(
            chunks = chunks.len(),
            event_type = %event.event_type,
            "event split into multiple chunks"
        );

        let total = chunks.len();
        let mut results = Vec::with_capacity(total);
        for (i, chunk) in chunks.iter().enumerate() {
            let mut chunk_event = event.clone();
            chunk_event
                .metadata
                .insert("chunk_index".into(), serde_json::json!(i));
            chunk_event
                .metadata
                .insert("total_chunks".into(), serde_json::json!(total));
            if let Some(ref doc_id) = event.source_doc_id {
                chunk_event.source_doc_id = Some(format!("{doc_id}::chunk::{i}"));
            }
            chunk_event.embedding = Some(self.embedder.embed_one(chunk)?);
            results.push(chunk_event);
        }

        Ok(results)
    }

    // -----------------------------------------------------------------------
    // Retry pending events
    // -----------------------------------------------------------------------

    /// Re-embed up to `max_pending_per_cycle` events from the pending queue.
    /// Successfully embedded events are returned; the rest stay on disk.
    pub fn process_pending(&self) -> Result<Vec<Event>> {
        let pending = self.pending_store.load_all()?;
        if pending.is_empty() {
            return Ok(Vec::new());
        }

        let limit = self.max_pending_per_cycle;
        let batch = &pending[..limit.min(pending.len())];
        let remainder = &pending[limit.min(pending.len())..];

        // Build (event, chunk_text) pairs
        let mut pairs: Vec<(Event, String)> = Vec::new();
        let max_tokens = self.embedder.effective_max_tokens;

        for event in batch {
            let text = build_embedding_text(event);
            if text.is_empty() {
                continue;
            }
            let chunks = chunk_text(&text, max_tokens, self.chunk_overlap)?;
            let total = chunks.len();
            for (i, chunk) in chunks.into_iter().enumerate() {
                let mut chunk_event = event.clone();
                if total > 1 {
                    chunk_event
                        .metadata
                        .insert("chunk_index".into(), serde_json::json!(i));
                    chunk_event
                        .metadata
                        .insert("total_chunks".into(), serde_json::json!(total));
                    if let Some(ref doc_id) = event.source_doc_id {
                        chunk_event.source_doc_id = Some(format!("{doc_id}::chunk::{i}"));
                    }
                }
                pairs.push((chunk_event, chunk));
            }
        }

        if pairs.is_empty() {
            // Nothing embeddable — drop the batch so it can't wedge the queue.
            self.pending_store.replace(remainder)?;
            return Ok(Vec::new());
        }

        let texts: Vec<String> = pairs.iter().map(|(_, t)| t.clone()).collect();

        match self.embedder.embed_many(texts) {
            Ok(vectors) => {
                let results: Vec<Event> = pairs
                    .into_iter()
                    .zip(vectors)
                    .map(|((mut event, _), vec)| {
                        event.embedding = Some(vec);
                        event
                    })
                    .collect();

                self.pending_store.replace(remainder)?;
                tracing::info!(
                    embedded = results.len(),
                    batch_size = batch.len(),
                    remaining = remainder.len(),
                    "bulk embedded pending events"
                );
                Ok(results)
            }
            Err(e) => {
                // Leave the file untouched — retry next cycle.
                tracing::error!(error=%e, "bulk embedding of pending events failed");
                Ok(Vec::new())
            }
        }
    }

    pub fn save_pending(&self, event: &Event) -> Result<()> {
        self.pending_store.save(event)
    }

    pub fn clear_pending(&self) -> Result<()> {
        self.pending_store.clear()
    }
}
