use crate::{
    db::{vec_to_bytes, Db},
    models::{Category, Task, TaskStatus},
};
use anyhow::Result;
use chrono::{DateTime, NaiveDate, Utc};
use rusqlite::params;
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::sync::Arc;

pub struct TaskStore {
    db: Arc<Db>,
    data_dir: PathBuf,
}

impl TaskStore {
    pub fn new(db: Arc<Db>, data_dir: PathBuf) -> Self {
        Self { db, data_dir }
    }

    // -----------------------------------------------------------------------
    // Write
    // -----------------------------------------------------------------------

    pub fn store_task(&self, task: &Task) -> Result<()> {
        self.upsert_task(task)?;
        self.write_json_file(task)?;
        Ok(())
    }

    pub fn update_task(&self, task: &Task) -> Result<()> {
        self.store_task(task)
    }

    fn upsert_task(&self, task: &Task) -> Result<()> {
        let conn = self.db.conn();

        conn.execute(
            "INSERT OR REPLACE INTO tasks
             (id, title, description, status, priority, category, project,
              start_time, end_time, duration_minutes, activity_count, confidence_score,
              tags, accomplishments, main_themes, activity_ids,
              parent_task_id, subtask_ids, created_at, updated_at)
             VALUES (?1,?2,?3,?4,?5,?6,?7,?8,?9,?10,?11,?12,?13,?14,?15,?16,?17,?18,?19,?20)",
            params![
                task.id,
                task.title,
                task.description,
                status_to_str(&task.status),
                priority_to_str(&task.priority),
                task.category.as_ref().map(|c| c.to_string()),
                task.project,
                task.start_time.map(|t| t.to_rfc3339()),
                task.end_time.map(|t| t.to_rfc3339()),
                task.duration_minutes,
                task.activity_count.map(|n| n as i64),
                task.confidence_score,
                serde_json::to_string(&task.tags)?,
                serde_json::to_string(&task.accomplishments)?,
                serde_json::to_string(&task.main_themes)?,
                serde_json::to_string(&task.activity_ids)?,
                task.parent_task_id,
                serde_json::to_string(&task.subtask_ids)?,
                task.created_at.to_rfc3339(),
                task.updated_at.to_rfc3339(),
            ],
        )?;

        if let Some(emb) = &task.embedding {
            conn.execute(
                "INSERT OR REPLACE INTO vec_tasks(task_id, embedding) VALUES (?1, ?2)",
                params![task.id, vec_to_bytes(emb)],
            )?;
        }

        Ok(())
    }

    // -----------------------------------------------------------------------
    // Read
    // -----------------------------------------------------------------------

    pub fn get_task(&self, task_id: &str) -> Result<Option<Task>> {
        let conn = self.db.conn();
        conn.query_row(
            "SELECT id, title, description, status, priority, category, project,
                    start_time, end_time, duration_minutes, activity_count, confidence_score,
                    tags, accomplishments, main_themes, activity_ids,
                    parent_task_id, subtask_ids, created_at, updated_at
             FROM tasks WHERE id = ?1",
            params![task_id],
            row_to_task,
        )
        .optional()
        .map_err(Into::into)
    }

    pub fn get_tasks_by_date(&self, date: NaiveDate) -> Result<Vec<Task>> {
        let date_str = date.format("%Y-%m-%d").to_string();
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, title, description, status, priority, category, project,
                    start_time, end_time, duration_minutes, activity_count, confidence_score,
                    tags, accomplishments, main_themes, activity_ids,
                    parent_task_id, subtask_ids, created_at, updated_at
             FROM tasks
             WHERE date(start_time) = ?1
             ORDER BY start_time ASC",
        )?;
        let tasks = stmt
            .query_map(params![date_str], row_to_task)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(tasks)
    }

    pub fn get_tasks_by_date_range(
        &self,
        start: NaiveDate,
        end: NaiveDate,
    ) -> Result<Vec<Task>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, title, description, status, priority, category, project,
                    start_time, end_time, duration_minutes, activity_count, confidence_score,
                    tags, accomplishments, main_themes, activity_ids,
                    parent_task_id, subtask_ids, created_at, updated_at
             FROM tasks
             WHERE date(start_time) >= ?1 AND date(start_time) <= ?2
             ORDER BY start_time ASC",
        )?;
        let tasks = stmt
            .query_map(
                params![
                    start.format("%Y-%m-%d").to_string(),
                    end.format("%Y-%m-%d").to_string()
                ],
                row_to_task,
            )?
            .collect::<std::result::Result<_, _>>()?;
        Ok(tasks)
    }

    pub fn get_active_tasks(&self) -> Result<Vec<Task>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, title, description, status, priority, category, project,
                    start_time, end_time, duration_minutes, activity_count, confidence_score,
                    tags, accomplishments, main_themes, activity_ids,
                    parent_task_id, subtask_ids, created_at, updated_at
             FROM tasks WHERE status = 'active' ORDER BY start_time DESC",
        )?;
        let tasks = stmt
            .query_map([], row_to_task)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(tasks)
    }

    pub fn get_recent_tasks(&self, limit: usize) -> Result<Vec<Task>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT id, title, description, status, priority, category, project,
                    start_time, end_time, duration_minutes, activity_count, confidence_score,
                    tags, accomplishments, main_themes, activity_ids,
                    parent_task_id, subtask_ids, created_at, updated_at
             FROM tasks ORDER BY start_time DESC LIMIT ?1",
        )?;
        let tasks = stmt
            .query_map(params![limit as i64], row_to_task)?
            .collect::<std::result::Result<_, _>>()?;
        Ok(tasks)
    }

    /// Vector similarity search over task embeddings, with optional keyword re-rank.
    pub fn search_tasks(
        &self,
        embedding: Option<&[f32]>,
        keyword: Option<&str>,
        limit: usize,
    ) -> Result<Vec<Task>> {
        if let Some(emb) = embedding {
            let conn = self.db.conn();
            let bytes = vec_to_bytes(emb);
            let mut vec_stmt = conn.prepare_cached(
                "SELECT task_id FROM vec_tasks
                 WHERE embedding MATCH ?1 AND k = ?2
                 ORDER BY distance ASC",
            )?;
            let task_ids: Vec<String> = vec_stmt
                .query_map(params![bytes, limit as i64 * 3], |row| row.get(0))?
                .collect::<std::result::Result<_, _>>()?;

            let mut tasks = Vec::new();
            for id in task_ids {
                if tasks.len() >= limit {
                    break;
                }
                if let Some(task) = self.get_task(&id)? {
                    if let Some(kw) = keyword {
                        let kw = kw.to_lowercase();
                        if !task.title.to_lowercase().contains(&kw)
                            && task
                                .description
                                .as_deref()
                                .map(|d| d.to_lowercase().contains(&kw))
                                != Some(true)
                        {
                            // include anyway if no keyword match — vector already ranked
                        }
                    }
                    tasks.push(task);
                }
            }
            Ok(tasks)
        } else if let Some(kw) = keyword {
            // Fallback: keyword-only search
            let conn = self.db.conn();
            let pattern = format!("%{}%", kw.to_lowercase());
            let mut stmt = conn.prepare_cached(
                "SELECT id, title, description, status, priority, category, project,
                        start_time, end_time, duration_minutes, activity_count, confidence_score,
                        tags, accomplishments, main_themes, activity_ids,
                        parent_task_id, subtask_ids, created_at, updated_at
                 FROM tasks
                 WHERE lower(title) LIKE ?1 OR lower(description) LIKE ?1
                 ORDER BY start_time DESC LIMIT ?2",
            )?;
            let tasks = stmt
                .query_map(params![pattern, limit as i64], row_to_task)?
                .collect::<std::result::Result<_, _>>()?;
            Ok(tasks)
        } else {
            self.get_recent_tasks(limit)
        }
    }

    pub fn get_task_statistics(&self, days: u32) -> Result<serde_json::Value> {
        let conn = self.db.conn();
        let since = (Utc::now() - chrono::Duration::days(days as i64))
            .format("%Y-%m-%d")
            .to_string();

        let total: i64 = conn.query_row(
            "SELECT COUNT(*) FROM tasks WHERE date(start_time) >= ?1",
            params![since],
            |r| r.get(0),
        )?;

        let completed: i64 = conn.query_row(
            "SELECT COUNT(*) FROM tasks WHERE status = 'completed' AND date(start_time) >= ?1",
            params![since],
            |r| r.get(0),
        )?;

        let total_minutes: f64 = conn
            .query_row(
                "SELECT COALESCE(SUM(duration_minutes), 0) FROM tasks WHERE date(start_time) >= ?1",
                params![since],
                |r| r.get(0),
            )
            .unwrap_or(0.0);

        let mut cat_stmt = conn.prepare(
            "SELECT category, COUNT(*) FROM tasks WHERE date(start_time) >= ?1 GROUP BY category ORDER BY 2 DESC",
        )?;
        let categories: Vec<(String, i64)> = cat_stmt
            .query_map(params![since], |r| Ok((r.get::<_, Option<String>>(0)?.unwrap_or_default(), r.get(1)?)))?
            .collect::<std::result::Result<_, _>>()?;

        Ok(serde_json::json!({
            "period_days": days,
            "total_tasks": total,
            "completed_tasks": completed,
            "total_hours": total_minutes / 60.0,
            "by_category": categories.into_iter().collect::<HashMap<_, _>>(),
        }))
    }

    // -----------------------------------------------------------------------
    // JSON file storage (compatibility with Python format)
    // -----------------------------------------------------------------------

    fn write_json_file(&self, task: &Task) -> Result<()> {
        let date = task.date_str();
        let dir = self.data_dir.join("tasks");
        std::fs::create_dir_all(&dir)?;
        let path = dir.join(format!("{date}_tasks.json"));

        let mut tasks: Vec<serde_json::Value> = if path.exists() {
            let content = std::fs::read_to_string(&path)?;
            serde_json::from_str(&content).unwrap_or_default()
        } else {
            Vec::new()
        };

        let task_val = serde_json::to_value(task)?;
        // Replace existing entry with same id or append
        if let Some(pos) = tasks
            .iter()
            .position(|v| v.get("id").and_then(|i| i.as_str()) == Some(&task.id))
        {
            tasks[pos] = task_val;
        } else {
            tasks.push(task_val);
        }

        std::fs::write(path, serde_json::to_string_pretty(&tasks)?)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Row deserializer
// ---------------------------------------------------------------------------

fn row_to_task(row: &rusqlite::Row) -> rusqlite::Result<Task> {
    use crate::models::TaskPriority;
    use std::str::FromStr;

    let id: String = row.get(0)?;
    let title: String = row.get(1)?;
    let description: Option<String> = row.get(2)?;
    let status_str: String = row.get(3)?;
    let priority_str: String = row.get(4)?;
    let category_str: Option<String> = row.get(5)?;
    let project: Option<String> = row.get(6)?;
    let start_time_str: Option<String> = row.get(7)?;
    let end_time_str: Option<String> = row.get(8)?;
    let duration_minutes: Option<f64> = row.get(9)?;
    let activity_count: Option<i64> = row.get(10)?;
    let confidence_score: Option<f64> = row.get(11)?;
    let tags_str: String = row.get(12)?;
    let accomplishments_str: String = row.get(13)?;
    let main_themes_str: String = row.get(14)?;
    let activity_ids_str: String = row.get(15)?;
    let parent_task_id: Option<String> = row.get(16)?;
    let subtask_ids_str: String = row.get(17)?;
    let created_at_str: String = row.get(18)?;
    let updated_at_str: String = row.get(19)?;

    let parse_dt = |s: String| {
        DateTime::parse_from_rfc3339(&s)
            .map(|dt| dt.with_timezone(&Utc))
            .unwrap_or_else(|_| Utc::now())
    };

    let status = match status_str.as_str() {
        "completed" => TaskStatus::Completed,
        "paused" => TaskStatus::Paused,
        "abandoned" => TaskStatus::Abandoned,
        _ => TaskStatus::Active,
    };

    let priority = match priority_str.as_str() {
        "low" => TaskPriority::Low,
        "high" => TaskPriority::High,
        "urgent" => TaskPriority::Urgent,
        _ => TaskPriority::Medium,
    };

    Ok(Task {
        id,
        title,
        description,
        status,
        priority,
        category: category_str.and_then(|s| Category::from_str(&s).ok()),
        project,
        start_time: start_time_str.map(parse_dt),
        end_time: end_time_str.map(parse_dt),
        duration_minutes,
        activity_count: activity_count.map(|n| n as u32),
        confidence_score,
        tags: serde_json::from_str(&tags_str).unwrap_or_default(),
        accomplishments: serde_json::from_str(&accomplishments_str).unwrap_or_default(),
        main_themes: serde_json::from_str(&main_themes_str).unwrap_or_default(),
        activity_ids: serde_json::from_str(&activity_ids_str).unwrap_or_default(),
        parent_task_id,
        subtask_ids: serde_json::from_str(&subtask_ids_str).unwrap_or_default(),
        embedding: None,
        created_at: parse_dt(created_at_str),
        updated_at: parse_dt(updated_at_str),
    })
}

fn status_to_str(s: &TaskStatus) -> &'static str {
    match s {
        TaskStatus::Active => "active",
        TaskStatus::Completed => "completed",
        TaskStatus::Paused => "paused",
        TaskStatus::Abandoned => "abandoned",
    }
}

fn priority_to_str(p: &crate::models::TaskPriority) -> &'static str {
    use crate::models::TaskPriority;
    match p {
        TaskPriority::Low => "low",
        TaskPriority::Medium => "medium",
        TaskPriority::High => "high",
        TaskPriority::Urgent => "urgent",
    }
}

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
