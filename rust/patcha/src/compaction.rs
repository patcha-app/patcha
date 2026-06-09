use crate::{
    categorize::rule_based_category,
    config::Config,
    db::{store::VectorStore, tasks::TaskStore},
    embedding::{cosine_similarity, Embedder},
    llm::{client::PatchaApiClient, prompts},
    models::{Category, Event, EventType, Task, TaskPriority, TaskStatus},
};
use anyhow::Result;
use chrono::{DateTime, NaiveDate, Utc};
use std::collections::HashMap;
use std::path::PathBuf;
use std::sync::Arc;
use tokio::task::JoinSet;

// ---------------------------------------------------------------------------
// LLM Task Analyzer
// ---------------------------------------------------------------------------

struct TaskAnalysis {
    title: String,
    accomplishments: Vec<String>,
    description: String,
    themes: Vec<String>,
    confidence_score: f64,
    start_time: DateTime<Utc>,
    end_time: DateTime<Utc>,
    duration_minutes: i64,
}

pub struct LLMTaskAnalyzer {
    client: Arc<PatchaApiClient>,
    model: String,
}

impl LLMTaskAnalyzer {
    pub fn new(client: Arc<PatchaApiClient>) -> Self {
        Self {
            client,
            model: "gpt-4o-mini".to_owned(),
        }
    }

    pub async fn analyze_task_chunk(&self, activities: &[Event]) -> Result<TaskAnalysis> {
        if activities.is_empty() {
            return Ok(Self::empty_analysis());
        }

        let content = aggregate_activity_content(activities);
        match self.generate_llm_analysis(&content, activities).await {
            Ok(a) => Ok(a),
            Err(_) => Ok(fallback_analysis(activities)),
        }
    }

    async fn generate_llm_analysis(
        &self,
        content: &str,
        activities: &[Event],
    ) -> Result<TaskAnalysis> {
        let start = activities.iter().map(|a| a.timestamp).min().unwrap();
        let end = activities.iter().map(|a| a.timestamp).max().unwrap();
        let duration_minutes = (end - start).num_minutes();

        let cats: Vec<String> = activities
            .iter()
            .filter_map(|a| a.category.as_ref())
            .map(|c| c.to_string())
            .collect();
        let primary_category = mode_string(&cats).unwrap_or_else(|| "Other".into());

        let projects: Vec<String> = activities
            .iter()
            .filter_map(|a| a.project.clone())
            .collect();
        let primary_project = mode_string(&projects).unwrap_or_else(|| "Personal".into());

        let user_prompt = prompts::render_user(
            "task_analysis",
            serde_json::json!({
                "activities": content,
                "duration_minutes": duration_minutes,
                "activity_count": activities.len(),
                "start_time": start.format("%H:%M").to_string(),
                "end_time": end.format("%H:%M").to_string(),
                "primary_category": primary_category,
                "primary_project": primary_project,
                "accomplishment_count": activities.len().min(8),
            }),
        )?;

        let system = prompts::render_system()?;
        let response = self
            .client
            .chat_completion(&system, &user_prompt, &self.model)
            .await?;

        Ok(parse_llm_response(&response, start, end))
    }

    fn empty_analysis() -> TaskAnalysis {
        let now = Utc::now();
        TaskAnalysis {
            title: "Empty Task".into(),
            accomplishments: Vec::new(),
            description: "No activities found".into(),
            themes: Vec::new(),
            confidence_score: 0.0,
            start_time: now,
            end_time: now,
            duration_minutes: 0,
        }
    }
}

// ---------------------------------------------------------------------------
// Activity content aggregation (mirrors Python _aggregate_activity_content)
// ---------------------------------------------------------------------------

fn aggregate_activity_content(activities: &[Event]) -> String {
    let mut parts = Vec::new();

    for (i, a) in activities.iter().enumerate() {
        let ts = a.timestamp.format("%H:%M");
        let proj = a.project.as_deref().map(|p| format!(" | project: {p}")).unwrap_or_default();
        let src = a.source.as_deref().map(|s| format!(" ({s})")).unwrap_or_default();
        let header = format!("{}. [{}] {}{}{}", i + 1, ts, a.event_type, proj, src);

        let detail = match a.event_type {
            EventType::Terminal => {
                if let Ok(data) = serde_json::from_str::<serde_json::Value>(&a.raw_content) {
                    let cmd = data.get("command").and_then(|v| v.as_str()).unwrap_or(&a.raw_content);
                    format!("   CMD: {cmd}")
                } else {
                    format!("   {}", a.summary.as_deref().unwrap_or(&a.raw_content))
                }
            }
            EventType::Browser => {
                if let Ok(data) = serde_json::from_str::<serde_json::Value>(&a.raw_content) {
                    let url = data.get("url").and_then(|v| v.as_str()).unwrap_or("");
                    let title = data.get("title").and_then(|v| v.as_str()).unwrap_or("");
                    let mut lines = Vec::new();
                    if !url.is_empty() { lines.push(format!("   URL: {url}")); }
                    if !title.is_empty() { lines.push(format!("   TITLE: {title}")); }
                    if lines.is_empty() { lines.push(format!("   {}", a.raw_content)); }
                    lines.join("\n")
                } else {
                    format!("   {}", a.raw_content)
                }
            }
            EventType::GitCommit | EventType::GitStash => {
                if let Ok(data) = serde_json::from_str::<serde_json::Value>(&a.raw_content) {
                    let msg = data.get("message").and_then(|v| v.as_str()).unwrap_or("");
                    let branch = data.get("branch").and_then(|v| v.as_str()).unwrap_or("");
                    let files: Vec<&str> = data.get("files_changed")
                        .and_then(|v| v.as_array())
                        .map(|arr| arr.iter().filter_map(|v| v.as_str()).take(5).collect())
                        .unwrap_or_default();
                    let mut lines = Vec::new();
                    if !msg.is_empty() {
                        if !branch.is_empty() {
                            lines.push(format!("   BRANCH: {branch} | COMMIT: \"{msg}\""));
                        } else {
                            lines.push(format!("   COMMIT: \"{msg}\""));
                        }
                    }
                    if !files.is_empty() {
                        lines.push(format!("   FILES: {}", files.join(", ")));
                    }
                    if lines.is_empty() { lines.push(format!("   {}", a.raw_content)); }
                    lines.join("\n")
                } else {
                    format!("   {}", a.raw_content)
                }
            }
            EventType::GitStaged => format!("   {}", a.raw_content),
            EventType::Screen | EventType::Window => {
                let app = a.metadata.get("app_name").and_then(|v| v.as_str()).unwrap_or("");
                let win = a.metadata.get("window_title").and_then(|v| v.as_str()).unwrap_or("");
                if !app.is_empty() {
                    if !win.is_empty() {
                        format!("   APP: {app} | WINDOW: {win}")
                    } else {
                        format!("   APP: {app}")
                    }
                } else {
                    format!("   {}", a.summary.as_deref().unwrap_or(&a.raw_content))
                }
            }
            _ => format!("   {}", a.summary.as_deref().unwrap_or(&a.raw_content)),
        };

        parts.push(header);
        parts.push(detail);
    }

    parts.join("\n")
}

// ---------------------------------------------------------------------------
// LLM response parser
// ---------------------------------------------------------------------------

fn parse_llm_response(
    text: &str,
    start: DateTime<Utc>,
    end: DateTime<Utc>,
) -> TaskAnalysis {
    let mut title = "Unknown Task".to_owned();
    let mut accomplishments = Vec::new();
    let mut description = "Task analysis unavailable".to_owned();
    let mut themes = Vec::new();
    let mut in_accomplishments = false;

    for line in text.lines() {
        let line = line.trim();
        if line.is_empty() { continue; }

        if let Some(rest) = line.strip_prefix("TASK_TITLE:") {
            title = rest.trim().to_owned();
            in_accomplishments = false;
        } else if line.starts_with("ACCOMPLISHMENTS:") {
            in_accomplishments = true;
        } else if let Some(rest) = line.strip_prefix("DESCRIPTION:") {
            description = rest.trim().to_owned();
            in_accomplishments = false;
        } else if let Some(rest) = line.strip_prefix("MAIN_THEMES:") {
            themes = rest.split(',').map(|s| s.trim().to_owned()).collect();
            in_accomplishments = false;
        } else if in_accomplishments {
            if let Some(rest) = line.strip_prefix('-') {
                let item = rest.trim().to_owned();
                if !item.is_empty() {
                    accomplishments.push(item);
                }
            }
        }
    }

    TaskAnalysis {
        title,
        accomplishments,
        description,
        themes,
        confidence_score: 0.8,
        start_time: start,
        end_time: end,
        duration_minutes: (end - start).num_minutes(),
    }
}

fn fallback_analysis(activities: &[Event]) -> TaskAnalysis {
    let cats: Vec<String> = activities
        .iter()
        .filter_map(|a| a.category.as_ref())
        .map(|c| c.to_string())
        .collect();
    let primary = mode_string(&cats).unwrap_or_else(|| "General".into());

    let snippets: Vec<String> = activities
        .iter()
        .filter_map(|a| a.summary.clone().or_else(|| Some(a.raw_content.clone())))
        .filter(|s| !s.is_empty())
        .map(|s| s.chars().take(120).collect())
        .take(3)
        .collect();

    let title = snippets.first().map(|s| s.chars().take(50).collect::<String>())
        .unwrap_or_else(|| format!("{primary} Work"));
    let description = if snippets.is_empty() {
        format!("{} {} activities", activities.len(), primary.to_lowercase())
    } else {
        snippets.join("; ")
    };

    let start = activities.iter().map(|a| a.timestamp).min().unwrap_or_else(Utc::now);
    let end = activities.iter().map(|a| a.timestamp).max().unwrap_or_else(Utc::now);

    TaskAnalysis {
        title,
        accomplishments: snippets,
        description,
        themes: vec![primary],
        confidence_score: 0.4,
        start_time: start,
        end_time: end,
        duration_minutes: (end - start).num_minutes(),
    }
}

// ---------------------------------------------------------------------------
// TaskIdentifier
// ---------------------------------------------------------------------------

#[derive(Clone, Copy)]
pub enum ClusteringMethod {
    Hdbscan,
    Dbscan,
}

pub struct TaskIdentifier {
    llm_client: Arc<PatchaApiClient>,
    min_activities_per_task: usize,
    dedup_threshold: f32,
}

impl TaskIdentifier {
    pub fn new(
        llm_client: Arc<PatchaApiClient>,
        min_activities_per_task: usize,
        dedup_threshold: f32,
    ) -> Self {
        Self {
            llm_client,
            min_activities_per_task,
            dedup_threshold,
        }
    }

    pub async fn identify_tasks_from_activities(
        &self,
        activities: Vec<Event>,
        similarity_threshold: f32,
        max_activities_per_task: usize,
        method: ClusteringMethod,
    ) -> Vec<Task> {
        if activities.len() < self.min_activities_per_task {
            return Vec::new();
        }

        let mut sorted = activities;
        sorted.sort_by_key(|a| a.timestamp);

        let chunks = self.create_semantic_chunks(
            &sorted,
            similarity_threshold,
            self.min_activities_per_task,
            method,
        );

        tracing::info!(initial_chunks = chunks.len(), "created semantic chunks");

        let chunks = split_oversized_chunks(chunks, max_activities_per_task);

        let tasks = self.process_chunks_parallel(chunks).await;

        tracing::info!(tasks = tasks.len(), "identified tasks");
        tasks
    }

    fn create_semantic_chunks(
        &self,
        activities: &[Event],
        similarity_threshold: f32,
        min_per_chunk: usize,
        method: ClusteringMethod,
    ) -> Vec<Vec<Event>> {
        // Group by project first (matches Python behavior)
        let mut project_groups: HashMap<String, Vec<Event>> = HashMap::new();
        for a in activities {
            let key = a.project.clone().unwrap_or_else(|| "__none__".into());
            project_groups.entry(key).or_default().push(a.clone());
        }

        let mut all_chunks = Vec::new();
        for group in project_groups.into_values() {
            let chunks = self.cluster_project_group(
                &group,
                similarity_threshold,
                min_per_chunk,
                method,
            );
            all_chunks.extend(chunks);
        }
        all_chunks
    }

    fn cluster_project_group(
        &self,
        activities: &[Event],
        similarity_threshold: f32,
        min_per_chunk: usize,
        method: ClusteringMethod,
    ) -> Vec<Vec<Event>> {
        let embedded: Vec<&Event> = activities
            .iter()
            .filter(|a| a.embedding.is_some())
            .collect();

        if embedded.len() < min_per_chunk {
            return Vec::new();
        }

        // L2-normalize embeddings (makes Euclidean distance ≈ cosine distance)
        let normed: Vec<Vec<f64>> = embedded
            .iter()
            .map(|a| {
                let emb = a.embedding.as_ref().unwrap();
                let norm: f32 = emb.iter().map(|x| x * x).sum::<f32>().sqrt();
                let norm = if norm == 0.0 { 1.0 } else { norm };
                emb.iter().map(|x| (*x / norm) as f64).collect()
            })
            .collect();

        let labels = match method {
            ClusteringMethod::Hdbscan => run_hdbscan(&normed, min_per_chunk),
            ClusteringMethod::Dbscan => run_dbscan(&normed, similarity_threshold, min_per_chunk),
        };

        // Group by cluster label (exclude -1 = noise)
        let mut clusters: HashMap<i32, Vec<Event>> = HashMap::new();
        let mut noise = Vec::new();
        for (idx, &label) in labels.iter().enumerate() {
            if label == -1 {
                noise.push(embedded[idx].clone());
            } else {
                clusters.entry(label).or_default().push(embedded[idx].clone());
            }
        }

        tracing::debug!(
            noise_count = noise.len(),
            cluster_count = clusters.len(),
            "clustering done"
        );

        let valid: Vec<Vec<Event>> = clusters
            .into_values()
            .filter(|c| c.len() >= min_per_chunk)
            .collect();

        // If no clusters found but enough events, treat all as one chunk
        if valid.is_empty() && embedded.len() >= min_per_chunk {
            return vec![embedded.iter().map(|e| (*e).clone()).collect()];
        }

        valid
    }

    async fn process_chunks_parallel(&self, chunks: Vec<Vec<Event>>) -> Vec<Task> {
        if chunks.is_empty() {
            return Vec::new();
        }

        let mut join_set = JoinSet::new();

        for (i, chunk) in chunks.into_iter().enumerate() {
            let client = Arc::clone(&self.llm_client);
            join_set.spawn(async move {
                let analyzer = LLMTaskAnalyzer::new(client);
                match analyzer.analyze_task_chunk(&chunk).await {
                    Ok(analysis) => {
                        if analysis.confidence_score >= 0.3 {
                            Some(build_task_from_analysis(&chunk, analysis))
                        } else {
                            None
                        }
                    }
                    Err(e) => {
                        tracing::warn!(chunk = i, error=%e, "LLM analysis failed, using fallback");
                        Some(build_task_from_analysis(&chunk, fallback_analysis(&chunk)))
                    }
                }
            });
        }

        let mut tasks = Vec::new();
        while let Some(result) = join_set.join_next().await {
            if let Ok(Some(task)) = result {
                tasks.push(task);
            }
        }
        tasks
    }
}

// ---------------------------------------------------------------------------
// Clustering backends
// ---------------------------------------------------------------------------

/// HDBSCAN is not available as a Rust crate; run our DBSCAN implementation with
/// tuned parameters to approximate HDBSCAN's variable-density behavior.
fn run_hdbscan(data: &[Vec<f64>], min_cluster_size: usize) -> Vec<i32> {
    run_dbscan(data, 0.7, min_cluster_size)
}

fn run_dbscan(data: &[Vec<f64>], similarity_threshold: f32, min_samples: usize) -> Vec<i32> {
    let n = data.len();
    let eps = (1.0 - similarity_threshold) as f64;

    // Simple DBSCAN with Euclidean distance on L2-normalized vectors
    let mut labels = vec![-1i32; n];
    let mut cluster_id = 0i32;
    let mut visited = vec![false; n];

    for i in 0..n {
        if visited[i] {
            continue;
        }
        visited[i] = true;
        let neighbors = range_query(data, i, eps);
        if neighbors.len() < min_samples {
            // noise — leave as -1
            continue;
        }
        labels[i] = cluster_id;
        let mut seed_set = neighbors;
        let mut si = 0;
        while si < seed_set.len() {
            let q = seed_set[si];
            if !visited[q] {
                visited[q] = true;
                let q_neighbors = range_query(data, q, eps);
                if q_neighbors.len() >= min_samples {
                    for &nb in &q_neighbors {
                        if !seed_set.contains(&nb) {
                            seed_set.push(nb);
                        }
                    }
                }
            }
            if labels[q] == -1 {
                labels[q] = cluster_id;
            }
            si += 1;
        }
        cluster_id += 1;
    }

    labels
}

fn range_query(data: &[Vec<f64>], idx: usize, eps: f64) -> Vec<usize> {
    data.iter()
        .enumerate()
        .filter(|(j, _)| *j != idx && euclidean_dist(&data[idx], &data[*j]) <= eps)
        .map(|(j, _)| j)
        .collect()
}

fn euclidean_dist(a: &[f64], b: &[f64]) -> f64 {
    a.iter()
        .zip(b.iter())
        .map(|(x, y)| (x - y) * (x - y))
        .sum::<f64>()
        .sqrt()
}

// ---------------------------------------------------------------------------
// Task construction
// ---------------------------------------------------------------------------

fn build_task_from_analysis(activities: &[Event], analysis: TaskAnalysis) -> Task {
    let categories: Vec<Category> = activities
        .iter()
        .filter_map(|a| a.category.clone())
        .collect();
    let category = mode_category(&categories);

    let projects: Vec<String> = activities
        .iter()
        .filter_map(|a| a.project.clone())
        .collect();
    let project = mode_string(&projects);

    let activity_ids: Vec<String> = activities
        .iter()
        .map(|a| format!("{}_{}", a.event_type, a.timestamp.to_rfc3339()))
        .collect();

    // Mean embedding across activities
    let embeddings: Vec<&Vec<f32>> = activities.iter().filter_map(|a| a.embedding.as_ref()).collect();
    let mean_embedding = if embeddings.is_empty() {
        None
    } else {
        let dim = embeddings[0].len();
        let mut sum = vec![0.0f32; dim];
        for emb in &embeddings {
            for (s, &v) in sum.iter_mut().zip(emb.iter()) {
                *s += v;
            }
        }
        let n = embeddings.len() as f32;
        Some(sum.into_iter().map(|v| v / n).collect::<Vec<_>>())
    };

    let now = Utc::now();
    let status = if analysis.end_time < now - chrono::Duration::hours(1) {
        TaskStatus::Completed
    } else {
        TaskStatus::Active
    };

    Task {
        id: uuid::Uuid::new_v4().to_string(),
        title: analysis.title,
        description: Some(analysis.description),
        status,
        priority: TaskPriority::Medium,
        category,
        project,
        start_time: Some(analysis.start_time),
        end_time: Some(analysis.end_time),
        duration_minutes: Some(analysis.duration_minutes as f64),
        activity_count: Some(activities.len() as u32),
        confidence_score: Some(analysis.confidence_score),
        tags: analysis.themes,
        accomplishments: analysis.accomplishments,
        main_themes: Vec::new(),
        activity_ids,
        parent_task_id: None,
        subtask_ids: Vec::new(),
        embedding: mean_embedding,
        created_at: now,
        updated_at: now,
    }
}

// ---------------------------------------------------------------------------
// DailyCompactor
// ---------------------------------------------------------------------------

pub struct DailyCompactor {
    vector_store: Arc<VectorStore>,
    task_store: Arc<TaskStore>,
    task_identifier: TaskIdentifier,
    data_dir: PathBuf,
    dedup_threshold: f32,
    min_activities_per_task: usize,
}

impl DailyCompactor {
    pub fn new(
        vector_store: Arc<VectorStore>,
        task_store: Arc<TaskStore>,
        llm_client: Arc<PatchaApiClient>,
        cfg: &Config,
    ) -> Self {
        let task_identifier = TaskIdentifier::new(
            llm_client,
            cfg.daily_compaction_min_activities,
            cfg.working_memory_dedup_threshold,
        );

        Self {
            vector_store,
            task_store,
            task_identifier,
            data_dir: cfg.data_dir.clone(),
            dedup_threshold: cfg.working_memory_dedup_threshold,
            min_activities_per_task: cfg.daily_compaction_min_activities,
        }
    }

    pub async fn compact_day(
        &self,
        target_date: NaiveDate,
        dry_run: bool,
        force: bool,
    ) -> Result<serde_json::Value> {
        let today = Utc::now().date_naive();
        if target_date >= today {
            return Ok(serde_json::json!({"skipped": true, "reason": "cannot_compact_today_or_future"}));
        }

        let date_str = target_date.format("%Y-%m-%d").to_string();
        let mut state = load_state(&self.data_dir);

        if state.compacted_dates.contains(&date_str) && !force {
            return Ok(serde_json::json!({"skipped": true, "reason": "already_compacted", "date": date_str}));
        }

        let events = self.vector_store.get_uncompacted_events_by_date(&date_str)?;
        tracing::info!(date = %date_str, events = events.len(), "compacting day");

        if events.is_empty() {
            if !dry_run {
                if !state.compacted_dates.contains(&date_str) {
                    state.compacted_dates.push(date_str.clone());
                }
                save_state(&self.data_dir, &state);
            }
            return Ok(serde_json::json!({
                "date": date_str, "event_count": 0,
                "deduped_count": 0, "task_count": 0, "dry_run": dry_run,
            }));
        }

        let deduped = self.dedup_events(events);
        tracing::info!(date = %date_str, after_dedup = deduped.len(), "deduped");

        let tasks = self
            .task_identifier
            .identify_tasks_from_activities(
                deduped.clone(),
                0.7,
                30,
                ClusteringMethod::Hdbscan,
            )
            .await;

        tracing::info!(date = %date_str, tasks = tasks.len(), "identified tasks");

        if !dry_run {
            for task in &tasks {
                self.task_store.store_task(task)?;
            }
            self.vector_store.delete_events_by_date(&date_str)?;
            if !state.compacted_dates.contains(&date_str) {
                state.compacted_dates.push(date_str.clone());
            }
            save_state(&self.data_dir, &state);
        }

        Ok(serde_json::json!({
            "date": date_str,
            "event_count": deduped.len(),
            "task_count": tasks.len(),
            "dry_run": dry_run,
        }))
    }

    pub async fn maybe_compact_previous_days(&self) -> Result<()> {
        let today = Utc::now().date_naive();
        let today_str = today.format("%Y-%m-%d").to_string();
        let mut state = load_state(&self.data_dir);

        if state.last_trigger_date.as_deref() == Some(&today_str) {
            return Ok(());
        }

        for days_ago in 1..8 {
            let target = today - chrono::Duration::days(days_ago);
            let target_str = target.format("%Y-%m-%d").to_string();
            if state.compacted_dates.contains(&target_str) {
                continue;
            }
            if let Err(e) = self.compact_day(target, false, false).await {
                tracing::error!(date=%target_str, error=%e, "compaction failed");
            }
        }

        state.last_trigger_date = Some(today_str);
        save_state(&self.data_dir, &state);
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Deduplication
    // -----------------------------------------------------------------------

    fn dedup_events(&self, mut events: Vec<Event>) -> Vec<Event> {
        events.sort_by_key(|e| e.timestamp);
        let before = events.len();
        events = dedup_by_content(events);
        let after_content = events.len();
        tracing::debug!(before, after_content, "content dedup");

        if events.len() > 50 {
            events = self.dedup_by_vector(events);
            tracing::debug!(after_vector = events.len(), "vector dedup");
        }

        events
    }

    fn dedup_by_vector(&self, events: Vec<Event>) -> Vec<Event> {
        let threshold = self.dedup_threshold;
        let mut kept: Vec<Event> = Vec::new();
        let mut kept_normed: Vec<Vec<f32>> = Vec::new();

        for event in events {
            if let Some(ref emb) = event.embedding {
                let norm: f32 = emb.iter().map(|x| x * x).sum::<f32>().sqrt();
                if norm > 0.0 {
                    let normed: Vec<f32> = emb.iter().map(|x| x / norm).collect();
                    let too_similar = kept_normed
                        .iter()
                        .any(|k| cosine_similarity(k, &normed) >= threshold);
                    if too_similar {
                        continue;
                    }
                    kept_normed.push(normed);
                }
            }
            kept.push(event);
        }

        kept
    }
}

// ---------------------------------------------------------------------------
// Content deduplication (mirrors Python _dedup_by_content)
// ---------------------------------------------------------------------------

fn dedup_by_content(events: Vec<Event>) -> Vec<Event> {
    let mut seen: HashMap<String, usize> = HashMap::new();
    let mut kept: Vec<Event> = Vec::new();

    for event in events {
        let key = match event.event_type {
            EventType::Terminal => {
                let cmd = serde_json::from_str::<serde_json::Value>(&event.raw_content)
                    .ok()
                    .and_then(|d| d.get("command").and_then(|v| v.as_str()).map(|s| s.to_owned()))
                    .unwrap_or_else(|| event.raw_content.clone());
                Some(format!("terminal:{cmd}"))
            }
            EventType::Browser => {
                let url = serde_json::from_str::<serde_json::Value>(&event.raw_content)
                    .ok()
                    .and_then(|d| d.get("url").and_then(|v| v.as_str()).map(|s| s.to_owned()))
                    .unwrap_or_else(|| event.raw_content.clone());
                Some(format!("browser:{url}"))
            }
            EventType::Window | EventType::Screen => {
                let app = event.metadata.get("app_name").and_then(|v| v.as_str()).unwrap_or("");
                let title = event.metadata.get("window_title").and_then(|v| v.as_str()).unwrap_or("");
                Some(format!("{}:{}:{}", event.event_type, app, title))
            }
            _ => None,
        };

        if let Some(k) = key {
            if let Some(&existing_idx) = seen.get(&k) {
                kept[existing_idx] = event;
            } else {
                seen.insert(k, kept.len());
                kept.push(event);
            }
        } else {
            kept.push(event);
        }
    }

    kept
}

// ---------------------------------------------------------------------------
// State persistence
// ---------------------------------------------------------------------------

#[derive(serde::Serialize, serde::Deserialize, Default)]
struct CompactionState {
    compacted_dates: Vec<String>,
    last_trigger_date: Option<String>,
}

fn load_state(data_dir: &std::path::Path) -> CompactionState {
    let path = data_dir.join("daily_compaction.json");
    if let Ok(content) = std::fs::read_to_string(&path) {
        serde_json::from_str(&content).unwrap_or_default()
    } else {
        CompactionState::default()
    }
}

fn save_state(data_dir: &std::path::Path, state: &CompactionState) {
    let path = data_dir.join("daily_compaction.json");
    let _ = std::fs::create_dir_all(data_dir);
    if let Ok(json) = serde_json::to_string_pretty(state) {
        let _ = std::fs::write(path, json);
    }
}

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------

fn split_oversized_chunks(chunks: Vec<Vec<Event>>, max_size: usize) -> Vec<Vec<Event>> {
    let mut result = Vec::new();
    for chunk in chunks {
        if chunk.len() <= max_size {
            result.push(chunk);
        } else {
            let mut sub: Vec<Event> = chunk;
            sub.sort_by_key(|e| e.timestamp);
            for window in sub.chunks(max_size) {
                if !window.is_empty() {
                    result.push(window.to_vec());
                }
            }
        }
    }
    result
}

fn mode_string(vals: &[String]) -> Option<String> {
    if vals.is_empty() { return None; }
    let mut counts: HashMap<&str, usize> = HashMap::new();
    for v in vals { *counts.entry(v.as_str()).or_insert(0) += 1; }
    counts.into_iter().max_by_key(|(_, c)| *c).map(|(k, _)| k.to_owned())
}

fn mode_category(cats: &[Category]) -> Option<Category> {
    if cats.is_empty() { return None; }
    let strings: Vec<String> = cats.iter().map(|c| c.to_string()).collect();
    mode_string(&strings).and_then(|s| s.parse().ok())
}
