use chrono::{DateTime, Utc};
use serde::{Deserialize, Serialize};
use std::collections::HashMap;
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Core enums
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EventType {
    GitCommit,
    GitStash,
    GitStaged,
    Browser,
    Terminal,
    Window,
    Screen,
    Note,
}

impl std::fmt::Display for EventType {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        let s = serde_json::to_value(self)
            .ok()
            .and_then(|v| v.as_str().map(|s| s.to_owned()))
            .unwrap_or_else(|| format!("{:?}", self).to_lowercase());
        write!(f, "{}", s)
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Hash, Serialize, Deserialize, Default)]
pub enum Category {
    Coding,
    Debugging,
    Research,
    Learning,
    Writing,
    Communication,
    #[default]
    Other,
}

impl std::fmt::Display for Category {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{:?}", self)
    }
}

impl std::str::FromStr for Category {
    type Err = ();
    fn from_str(s: &str) -> Result<Self, Self::Err> {
        match s.to_lowercase().as_str() {
            "coding" => Ok(Category::Coding),
            "debugging" => Ok(Category::Debugging),
            "research" => Ok(Category::Research),
            "learning" => Ok(Category::Learning),
            "writing" => Ok(Category::Writing),
            "communication" => Ok(Category::Communication),
            _ => Ok(Category::Other),
        }
    }
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum TaskStatus {
    #[default]
    Active,
    Completed,
    Paused,
    Abandoned,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, Default)]
#[serde(rename_all = "snake_case")]
pub enum TaskPriority {
    Low,
    #[default]
    Medium,
    High,
    Urgent,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum EntityType {
    Technology,
    Project,
    Concept,
    Person,
    Organization,
    File,
    Feature,
    Issue,
    Documentation,
}

#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize)]
#[serde(rename_all = "snake_case")]
pub enum RelationshipType {
    Uses,
    WorksOn,
    Learns,
    RelatesTo,
    PartOf,
    DependsOn,
    Fixes,
    Documents,
    CollaboratesWith,
    Mentions,
}

// ---------------------------------------------------------------------------
// Event
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Event {
    pub id: String,
    pub timestamp: DateTime<Utc>,
    pub event_type: EventType,
    pub source: Option<String>,
    /// Stable identifier for mutable documents (Google Docs, Notion pages, local files).
    /// Used to deduplicate re-fetches of the same document over time.
    pub source_doc_id: Option<String>,
    pub raw_content: String,
    pub embedding: Option<Vec<f32>>,
    pub summary: Option<String>,
    pub category: Option<Category>,
    pub project: Option<String>,
    pub task_id: Option<String>,
    pub metadata: HashMap<String, serde_json::Value>,
}

impl Event {
    pub fn new(event_type: EventType, raw_content: impl Into<String>) -> Self {
        Self {
            id: Uuid::new_v4().to_string(),
            timestamp: Utc::now(),
            event_type,
            source: None,
            source_doc_id: None,
            raw_content: raw_content.into(),
            embedding: None,
            summary: None,
            category: None,
            project: None,
            task_id: None,
            metadata: HashMap::new(),
        }
    }

    pub fn date_str(&self) -> String {
        self.timestamp.format("%Y-%m-%d").to_string()
    }
}

// ---------------------------------------------------------------------------
// Task
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Task {
    pub id: String,
    pub title: String,
    pub description: Option<String>,
    pub status: TaskStatus,
    pub priority: TaskPriority,
    pub category: Option<Category>,
    pub project: Option<String>,
    pub start_time: Option<DateTime<Utc>>,
    pub end_time: Option<DateTime<Utc>>,
    pub duration_minutes: Option<f64>,
    pub activity_count: Option<u32>,
    pub confidence_score: Option<f64>,
    pub tags: Vec<String>,
    pub accomplishments: Vec<String>,
    pub main_themes: Vec<String>,
    pub activity_ids: Vec<String>,
    pub parent_task_id: Option<String>,
    pub subtask_ids: Vec<String>,
    pub embedding: Option<Vec<f32>>,
    pub created_at: DateTime<Utc>,
    pub updated_at: DateTime<Utc>,
}

impl Task {
    pub fn new(title: impl Into<String>) -> Self {
        let now = Utc::now();
        Self {
            id: Uuid::new_v4().to_string(),
            title: title.into(),
            description: None,
            status: TaskStatus::default(),
            priority: TaskPriority::default(),
            category: None,
            project: None,
            start_time: None,
            end_time: None,
            duration_minutes: None,
            activity_count: None,
            confidence_score: None,
            tags: Vec::new(),
            accomplishments: Vec::new(),
            main_themes: Vec::new(),
            activity_ids: Vec::new(),
            parent_task_id: None,
            subtask_ids: Vec::new(),
            embedding: None,
            created_at: now,
            updated_at: now,
        }
    }

    pub fn date_str(&self) -> String {
        self.start_time
            .unwrap_or(self.created_at)
            .format("%Y-%m-%d")
            .to_string()
    }
}

// ---------------------------------------------------------------------------
// Knowledge graph
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Entity {
    pub id: String,
    pub name: String,
    pub entity_type: EntityType,
    pub aliases: Vec<String>,
    pub confidence: f64,
    pub first_seen: DateTime<Utc>,
    pub last_seen: DateTime<Utc>,
    pub mention_count: u32,
    pub metadata: HashMap<String, serde_json::Value>,
}

impl Entity {
    pub fn new(name: impl Into<String>, entity_type: EntityType) -> Self {
        let now = Utc::now();
        Self {
            id: Uuid::new_v4().to_string(),
            name: name.into(),
            entity_type,
            aliases: Vec::new(),
            confidence: 1.0,
            first_seen: now,
            last_seen: now,
            mention_count: 1,
            metadata: HashMap::new(),
        }
    }
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct Relationship {
    pub id: String,
    pub source_entity_id: String,
    pub target_entity_id: String,
    pub relationship_type: RelationshipType,
    pub confidence: f64,
    pub first_seen: DateTime<Utc>,
    pub last_seen: DateTime<Utc>,
    pub strength: f64,
    pub supporting_activity_ids: Vec<String>,
    pub metadata: HashMap<String, serde_json::Value>,
}

// ---------------------------------------------------------------------------
// Summaries
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct CategorySummary {
    pub category: Category,
    pub event_count: u32,
    pub summary: String,
    pub projects: Vec<String>,
    pub duration_minutes: Option<f64>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DailySummary {
    pub date: String,
    pub total_events: u32,
    pub category_summaries: Vec<CategorySummary>,
    pub overall_summary: String,
    pub top_projects: Vec<String>,
    pub generated_at: DateTime<Utc>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct DailyTaskSummary {
    pub date: String,
    pub total_tasks: u32,
    pub completed_tasks: u32,
    pub total_duration_minutes: f64,
    pub tasks: Vec<Task>,
    pub overall_summary: String,
    pub top_projects: Vec<String>,
    pub generated_at: DateTime<Utc>,
}

// ---------------------------------------------------------------------------
// Graph traversal helpers
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphNode {
    pub id: String,
    pub label: String,
    pub props: HashMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphEdge {
    pub id: String,
    pub src_id: String,
    pub dst_id: String,
    pub edge_type: String,
    pub props: HashMap<String, serde_json::Value>,
}

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct GraphPath {
    pub nodes: Vec<GraphNode>,
    pub edges: Vec<GraphEdge>,
    pub total_strength: f64,
}

// ---------------------------------------------------------------------------
// Search results
// ---------------------------------------------------------------------------

#[derive(Debug, Clone, Serialize, Deserialize)]
pub struct SearchResult {
    pub id: String,
    pub score: f64,
    pub event: Option<Event>,
    pub task: Option<Task>,
}
