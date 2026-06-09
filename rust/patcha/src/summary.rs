use crate::{
    db::{store::VectorStore, tasks::TaskStore},
    llm::{client::PatchaApiClient, prompts},
    models::{Category, DailySummary, DailyTaskSummary, CategorySummary, Task, TaskStatus},
};
use anyhow::Result;
use chrono::{NaiveDate, Utc};
use std::collections::HashMap;
use std::path::{Path, PathBuf};
use std::str::FromStr;
use std::sync::Arc;

const MODEL: &str = "gpt-4o-mini";

// ---------------------------------------------------------------------------
// DailySummarizer — event-based (pre-compaction data)
// ---------------------------------------------------------------------------

pub struct DailySummarizer {
    vector_store: Arc<VectorStore>,
    llm_client: Arc<PatchaApiClient>,
    summaries_dir: PathBuf,
}

impl DailySummarizer {
    pub fn new(
        vector_store: Arc<VectorStore>,
        llm_client: Arc<PatchaApiClient>,
        data_dir: &Path,
    ) -> Self {
        Self {
            vector_store,
            llm_client,
            summaries_dir: data_dir.join("summaries"),
        }
    }

    pub async fn generate_daily_summary(&self, date: NaiveDate) -> Result<Option<DailySummary>> {
        let date_str = date.format("%Y-%m-%d").to_string();
        let events = self.vector_store.get_events_by_date(&date_str)?;

        if events.is_empty() {
            return Ok(None);
        }

        // Group by category
        let mut by_category: HashMap<String, Vec<_>> = HashMap::new();
        let mut project_counts: HashMap<String, usize> = HashMap::new();

        for event in &events {
            let cat = event
                .category
                .as_ref()
                .map(|c| c.to_string())
                .unwrap_or_else(|| "Other".into());
            by_category.entry(cat).or_default().push(event);

            if let Some(proj) = &event.project {
                *project_counts.entry(proj.clone()).or_insert(0) += 1;
            }
        }

        // Generate per-category summaries
        let mut category_summaries = Vec::new();
        for (cat_str, cat_events) in &by_category {
            let summary_text = self.generate_category_summary(cat_str, cat_events).await;
            let category = Category::from_str(cat_str).unwrap_or(Category::Other);
            category_summaries.push(CategorySummary {
                category,
                event_count: cat_events.len() as u32,
                summary: summary_text,
                projects: cat_events
                    .iter()
                    .filter_map(|e| e.project.clone())
                    .collect::<std::collections::HashSet<_>>()
                    .into_iter()
                    .collect(),
                duration_minutes: None,
            });
        }

        // Top categories by count
        let mut top_cats: Vec<(&String, usize)> = by_category
            .iter()
            .map(|(k, v)| (k, v.len()))
            .collect();
        top_cats.sort_by(|a, b| b.1.cmp(&a.1));
        let top_categories_str = top_cats
            .iter()
            .take(3)
            .map(|(k, v)| format!("{k} ({v})"))
            .collect::<Vec<_>>()
            .join(", ");

        let mut projects: Vec<(String, usize)> = project_counts.into_iter().collect();
        projects.sort_by(|a, b| b.1.cmp(&a.1));
        let top_projects: Vec<String> = projects.iter().take(5).map(|(k, _)| k.clone()).collect();
        let projects_str = {
            let mut s = top_projects.join(", ");
            if projects.len() > 5 {
                s.push_str("...");
            }
            s
        };

        let overall_summary = self
            .generate_overall_summary(
                &date.format("%B %d, %Y").to_string(),
                events.len(),
                &top_categories_str,
                &projects_str,
            )
            .await;

        let summary = DailySummary {
            date: date_str,
            total_events: events.len() as u32,
            category_summaries,
            overall_summary,
            top_projects,
            generated_at: Utc::now(),
        };

        Ok(Some(summary))
    }

    async fn generate_category_summary(
        &self,
        category: &str,
        events: &[&crate::models::Event],
    ) -> String {
        let summaries: Vec<String> = events
            .iter()
            .take(15)
            .map(|e| {
                e.summary
                    .clone()
                    .unwrap_or_else(|| e.raw_content.chars().take(100).collect())
            })
            .collect();

        let extra_count = events.len().saturating_sub(15);

        let user_prompt = match prompts::render_user(
            "category_summary",
            serde_json::json!({
                "category_name": category.to_lowercase(),
                "event_count": events.len(),
                "summaries": summaries,
                "extra_count": extra_count,
            }),
        ) {
            Ok(p) => p,
            Err(_) => return fallback_category_summary(category, events.len()),
        };

        let system = prompts::render_system().unwrap_or_default();
        match self
            .llm_client
            .chat_completion(&system, &user_prompt, MODEL)
            .await
        {
            Ok(resp) => resp,
            Err(_) => fallback_category_summary(category, events.len()),
        }
    }

    async fn generate_overall_summary(
        &self,
        date_str: &str,
        total_events: usize,
        top_categories_str: &str,
        projects_str: &str,
    ) -> String {
        let user_prompt = match prompts::render_user(
            "overall_summary",
            serde_json::json!({
                "date_str": date_str,
                "total_events": total_events,
                "top_categories_str": top_categories_str,
                "projects_str": projects_str,
            }),
        ) {
            Ok(p) => p,
            Err(_) => return format!("Tracked {total_events} activities today."),
        };

        let system = prompts::render_system().unwrap_or_default();
        match self
            .llm_client
            .chat_completion(&system, &user_prompt, MODEL)
            .await
        {
            Ok(resp) => resp,
            Err(_) => format!("Tracked {total_events} activities. Top categories: {top_categories_str}."),
        }
    }

    pub fn save_summary(&self, summary: &DailySummary) -> Result<()> {
        std::fs::create_dir_all(&self.summaries_dir)?;

        // JSON
        let json_path = self.summaries_dir.join(format!("{}_summary.json", summary.date));
        std::fs::write(&json_path, serde_json::to_string_pretty(summary)?)?;

        // Markdown
        let md_path = self.summaries_dir.join(format!("{}_summary.md", summary.date));
        std::fs::write(&md_path, format_summary_as_markdown(summary))?;

        Ok(())
    }

    pub fn load_summary(&self, date: NaiveDate) -> Result<Option<DailySummary>> {
        let path = self
            .summaries_dir
            .join(format!("{}_summary.json", date.format("%Y-%m-%d")));
        if !path.exists() {
            return Ok(None);
        }
        let content = std::fs::read_to_string(path)?;
        Ok(Some(serde_json::from_str(&content)?))
    }
}

// ---------------------------------------------------------------------------
// TaskSummarizer — task-based (post-compaction data)
// ---------------------------------------------------------------------------

pub struct TaskSummarizer {
    task_store: Arc<TaskStore>,
    llm_client: Arc<PatchaApiClient>,
    summaries_dir: PathBuf,
}

impl TaskSummarizer {
    pub fn new(
        task_store: Arc<TaskStore>,
        llm_client: Arc<PatchaApiClient>,
        data_dir: &Path,
    ) -> Self {
        Self {
            task_store,
            llm_client,
            summaries_dir: data_dir.join("task_summaries"),
        }
    }

    pub async fn generate_daily_task_summary(&self, date: NaiveDate) -> Result<DailyTaskSummary> {
        let tasks = self.task_store.get_tasks_by_date(date)?;

        if tasks.is_empty() {
            return Ok(DailyTaskSummary {
                date: date.format("%Y-%m-%d").to_string(),
                total_tasks: 0,
                completed_tasks: 0,
                total_duration_minutes: 0.0,
                tasks: Vec::new(),
                overall_summary: "No tasks found for this date.".into(),
                top_projects: Vec::new(),
                generated_at: Utc::now(),
            });
        }

        let total = tasks.len() as u32;
        let completed = tasks
            .iter()
            .filter(|t| t.status == TaskStatus::Completed)
            .count() as u32;
        let total_minutes: f64 = tasks.iter().filter_map(|t| t.duration_minutes).sum();

        let mut project_counts: HashMap<String, usize> = HashMap::new();
        for task in &tasks {
            if let Some(p) = &task.project {
                *project_counts.entry(p.clone()).or_insert(0) += 1;
            }
        }
        let mut top_projects: Vec<String> = project_counts
            .iter()
            .collect::<Vec<_>>()
            .into_iter()
            .sorted_by(|a, b| b.1.cmp(a.1))
            .map(|(k, _)| k.clone())
            .take(5)
            .collect();

        let overall = self
            .generate_task_based_overview(date, &tasks)
            .await;

        Ok(DailyTaskSummary {
            date: date.format("%Y-%m-%d").to_string(),
            total_tasks: total,
            completed_tasks: completed,
            total_duration_minutes: total_minutes,
            tasks,
            overall_summary: overall,
            top_projects,
            generated_at: Utc::now(),
        })
    }

    /// Rich narrative summary showing individual tasks with AI descriptions.
    pub async fn generate_rich_daily_summary(
        &self,
        date: NaiveDate,
        use_ai: bool,
    ) -> Result<serde_json::Value> {
        let tasks = self.task_store.get_tasks_by_date(date)?;

        if tasks.is_empty() {
            return Ok(serde_json::json!({
                "date": date.format("%Y-%m-%d").to_string(),
                "overview": "No tasks found for this date.",
                "total_tasks": 0,
                "tasks": [],
                "projects": [],
            }));
        }

        let total = tasks.len();
        let completed = tasks.iter().filter(|t| t.status == TaskStatus::Completed).count();
        let total_minutes: f64 = tasks.iter().filter_map(|t| t.duration_minutes).sum();

        // Build task summary list, sorted by duration
        let mut task_summaries: Vec<serde_json::Value> = Vec::new();
        for (i, task) in tasks.iter().enumerate() {
            let summary_text = if use_ai && i < 10 {
                self.generate_task_summary(task).await
            } else {
                format!(
                    "{} — {} activities over {} minutes",
                    task.title,
                    task.activity_count.unwrap_or(0),
                    task.duration_minutes.unwrap_or(0.0) as i64
                )
            };
            task_summaries.push(serde_json::json!({
                "id": task.id,
                "title": task.title,
                "summary": summary_text,
                "category": task.category.as_ref().map(|c| c.to_string()).unwrap_or_else(|| "Other".into()),
                "project": task.project,
                "status": format!("{:?}", task.status).to_lowercase(),
                "duration_minutes": task.duration_minutes.unwrap_or(0.0),
                "activity_count": task.activity_count.unwrap_or(0),
                "confidence_score": task.confidence_score.unwrap_or(0.0),
            }));
        }
        task_summaries.sort_by(|a, b| {
            let da = a["duration_minutes"].as_f64().unwrap_or(0.0);
            let db = b["duration_minutes"].as_f64().unwrap_or(0.0);
            db.partial_cmp(&da).unwrap()
        });

        let projects: Vec<String> = tasks
            .iter()
            .filter_map(|t| t.project.clone())
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();

        let overview = if use_ai {
            self.generate_task_based_overview(date, &tasks).await
        } else {
            let top: Vec<&str> = task_summaries
                .iter()
                .take(3)
                .filter_map(|t| t["title"].as_str())
                .collect();
            format!(
                "{} — {} tasks ({} completed). Key: {}.",
                date.format("%B %d, %Y"),
                total,
                completed,
                top.join(", ")
            )
        };

        Ok(serde_json::json!({
            "date": date.format("%Y-%m-%d").to_string(),
            "overview": overview,
            "total_tasks": total,
            "completed_tasks": completed,
            "total_time_minutes": total_minutes,
            "tasks": task_summaries,
            "projects": projects,
        }))
    }

    /// Weekly summary across `num_days` ending today.
    pub async fn generate_weekly_task_summary(&self, num_days: u32) -> Result<serde_json::Value> {
        let end = Utc::now().date_naive();
        let start = end - chrono::Duration::days(num_days as i64);
        let tasks = self.task_store.get_tasks_by_date_range(start, end)?;

        let total = tasks.len();
        let completed = tasks.iter().filter(|t| t.status == TaskStatus::Completed).count();
        let total_hours: f64 = tasks.iter().filter_map(|t| t.duration_minutes).sum::<f64>() / 60.0;

        let mut by_project: HashMap<String, usize> = HashMap::new();
        let mut by_category: HashMap<String, usize> = HashMap::new();
        for task in &tasks {
            if let Some(p) = &task.project { *by_project.entry(p.clone()).or_insert(0) += 1; }
            if let Some(c) = &task.category { *by_category.entry(c.to_string()).or_insert(0) += 1; }
        }

        Ok(serde_json::json!({
            "period_days": num_days,
            "start_date": start.format("%Y-%m-%d").to_string(),
            "end_date": end.format("%Y-%m-%d").to_string(),
            "total_tasks": total,
            "completed_tasks": completed,
            "completion_rate": if total > 0 { completed as f64 / total as f64 } else { 0.0 },
            "total_hours": total_hours,
            "by_project": by_project,
            "by_category": by_category,
        }))
    }

    /// Productivity trends over N days.
    pub fn get_productivity_trends(&self, days: u32) -> Result<serde_json::Value> {
        let end = Utc::now().date_naive();
        let start = end - chrono::Duration::days(days as i64);
        self.task_store.get_task_statistics(days)
    }

    // -----------------------------------------------------------------------
    // LLM helpers
    // -----------------------------------------------------------------------

    async fn generate_task_summary(&self, task: &Task) -> String {
        let accomplishments = task.accomplishments.join("; ");
        let prompt = format!(
            "Summarize this work task in one sentence:\nTitle: {}\nDescription: {}\nAccomplishments: {}",
            task.title,
            task.description.as_deref().unwrap_or(""),
            if accomplishments.is_empty() { "none recorded".into() } else { accomplishments },
        );
        let system = prompts::render_system().unwrap_or_default();
        self.llm_client
            .chat_completion(&system, &prompt, MODEL)
            .await
            .unwrap_or_else(|_| format!("{} — {}", task.title, task.description.as_deref().unwrap_or("")))
    }

    async fn generate_task_based_overview(&self, date: NaiveDate, tasks: &[Task]) -> String {
        let total = tasks.len();
        let completed = tasks.iter().filter(|t| t.status == TaskStatus::Completed).count();
        let total_hours: f64 = tasks.iter().filter_map(|t| t.duration_minutes).sum::<f64>() / 60.0;
        let top_titles: Vec<&str> = tasks.iter().take(5).map(|t| t.title.as_str()).collect();
        let projects: Vec<String> = tasks
            .iter()
            .filter_map(|t| t.project.clone())
            .collect::<std::collections::HashSet<_>>()
            .into_iter()
            .collect();

        let prompt = format!(
            "Summarize this workday in 2-3 sentences:\nDate: {}\nTotal tasks: {}\nCompleted: {}\nHours worked: {:.1}\nTop tasks: {}\nProjects: {}",
            date.format("%B %d, %Y"),
            total, completed, total_hours,
            top_titles.join(", "),
            if projects.is_empty() { "various".into() } else { projects.join(", ") },
        );

        let system = prompts::render_system().unwrap_or_default();
        self.llm_client
            .chat_completion(&system, &prompt, MODEL)
            .await
            .unwrap_or_else(|_| {
                format!(
                    "{} — {} tasks, {}/{} completed, {:.1}h total.",
                    date.format("%B %d, %Y"), total, completed, total, total_hours
                )
            })
    }

    pub fn save_summary(&self, date: NaiveDate, data: &serde_json::Value) -> Result<()> {
        std::fs::create_dir_all(&self.summaries_dir)?;
        let path = self.summaries_dir.join(format!("{}_task_summary.json", date.format("%Y-%m-%d")));
        std::fs::write(path, serde_json::to_string_pretty(data)?)?;
        Ok(())
    }
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------

fn fallback_category_summary(category: &str, count: usize) -> String {
    format!("Completed {count} {category} activities.")
}

fn format_summary_as_markdown(summary: &DailySummary) -> String {
    let mut md = format!(
        "# Daily Activity Summary — {}\n\n## Overview\n{}\n\n**Total Activities:** {}\n\n## Activities by Category\n\n",
        summary.date, summary.overall_summary, summary.total_events
    );

    for cs in &summary.category_summaries {
        md.push_str(&format!(
            "### {} ({} activities)\n\n{}\n\n",
            cs.category, cs.event_count, cs.summary
        ));
    }

    if !summary.top_projects.is_empty() {
        md.push_str("## Projects\n\n");
        for p in &summary.top_projects {
            md.push_str(&format!("- **{p}**\n"));
        }
        md.push('\n');
    }

    md.push_str(&format!(
        "---\n*Generated on {}*\n",
        summary.generated_at.format("%Y-%m-%d %H:%M:%S")
    ));
    md
}

// ---------------------------------------------------------------------------
// Iterator helper (sorted_by for Vec)
// ---------------------------------------------------------------------------

trait SortedBy: Iterator {
    fn sorted_by<F>(self, cmp: F) -> std::vec::IntoIter<Self::Item>
    where
        F: Fn(&Self::Item, &Self::Item) -> std::cmp::Ordering,
        Self: Sized,
    {
        let mut v: Vec<Self::Item> = self.collect();
        v.sort_by(cmp);
        v.into_iter()
    }
}

impl<T: Iterator> SortedBy for T {}
