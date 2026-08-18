use crate::{
    db::{store::VectorStore, tasks::TaskStore},
    embedding::Embedder,
    models::{Event, Task},
};
use anyhow::Result;
use chrono::NaiveDate;
use std::sync::Arc;

pub struct TaskAwareSearchService {
    pub vector_store: Arc<VectorStore>,
    pub task_store: Arc<TaskStore>,
    pub embedder: Arc<Embedder>,
}

impl TaskAwareSearchService {
    pub fn new(
        vector_store: Arc<VectorStore>,
        task_store: Arc<TaskStore>,
        embedder: Arc<Embedder>,
    ) -> Self {
        Self {
            vector_store,
            task_store,
            embedder,
        }
    }

    /// Search across tasks and/or activities. `mode` is "tasks", "activities", or "both".
    pub fn search(
        &self,
        query: &str,
        mode: &str,
        limit: usize,
        _date_filter: Option<NaiveDate>,
    ) -> Result<serde_json::Value> {
        let embedding = self.embedder.embed_query(query)?;

        let (task_results, activity_results) = match mode {
            "tasks" => {
                let tasks = self
                    .task_store
                    .search_tasks(Some(&embedding), Some(query), limit)?;
                (tasks, Vec::new())
            }
            "activities" => {
                let events = self.vector_store.search_events(&embedding, limit, None)?;
                let events: Vec<Event> = events.into_iter().map(|r| r.event).collect();
                (Vec::new(), events)
            }
            _ => {
                // "both" — search both, deduplicate activities belonging to matched tasks
                let tasks =
                    self.task_store
                        .search_tasks(Some(&embedding), Some(query), limit / 2)?;
                let activity_ids: std::collections::HashSet<String> = tasks
                    .iter()
                    .flat_map(|t| t.activity_ids.iter().cloned())
                    .collect();

                let events = self.vector_store.search_events(&embedding, limit, None)?;
                let events: Vec<Event> = events
                    .into_iter()
                    .map(|r| r.event)
                    .filter(|e| !activity_ids.contains(&e.id))
                    .collect();

                (tasks, events)
            }
        };

        Ok(serde_json::json!({
            "query": query,
            "search_type": mode,
            "task_results": format_tasks(&task_results),
            "activity_results": format_events(&activity_results),
            "total_results": task_results.len() + activity_results.len(),
        }))
    }

    pub fn search_by_date_range(
        &self,
        query: Option<&str>,
        start: NaiveDate,
        end: NaiveDate,
        limit: usize,
    ) -> Result<Vec<Task>> {
        let tasks = self.task_store.get_tasks_by_date_range(start, end)?;
        if let Some(q) = query {
            let q_lower = q.to_lowercase();
            Ok(tasks
                .into_iter()
                .filter(|t| {
                    t.title.to_lowercase().contains(&q_lower)
                        || t.description
                            .as_deref()
                            .map(|d| d.to_lowercase().contains(&q_lower))
                            .unwrap_or(false)
                })
                .take(limit)
                .collect())
        } else {
            Ok(tasks.into_iter().take(limit).collect())
        }
    }
}

fn format_tasks(tasks: &[Task]) -> Vec<serde_json::Value> {
    tasks
        .iter()
        .map(|t| {
            serde_json::json!({
                "id": t.id,
                "title": t.title,
                "description": t.description,
                "category": t.category.as_ref().map(|c| c.to_string()),
                "project": t.project,
                "duration_minutes": t.duration_minutes,
                "confidence_score": t.confidence_score,
                "start_time": t.start_time.map(|dt| dt.to_rfc3339()),
            })
        })
        .collect()
}

fn format_events(events: &[Event]) -> Vec<serde_json::Value> {
    events
        .iter()
        .map(|e| {
            serde_json::json!({
                "id": e.id,
                "type": e.event_type.to_string(),
                "timestamp": e.timestamp.to_rfc3339(),
                "summary": e.summary,
                "project": e.project,
                "category": e.category.as_ref().map(|c| c.to_string()),
            })
        })
        .collect()
}
