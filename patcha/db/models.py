"""Data models for digital activity tracking."""

import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional
from enum import Enum
from pydantic import BaseModel, Field


class EventType(str, Enum):
    GIT_COMMIT = "git_commit"
    GIT_STASH = "git_stash"
    GIT_STAGED = "git_staged"
    BROWSER = "browser"
    TERMINAL = "terminal"
    NOTE = "note"
    WINDOW = "window"
    SCREEN = "screen"


class Category(str, Enum):
    CODING = "Coding"
    DEBUGGING = "Debugging"
    RESEARCH = "Research"
    LEARNING = "Learning"
    WRITING = "Writing"
    COMMUNICATION = "Communication"
    OTHER = "Other"


class TaskStatus(str, Enum):
    ACTIVE = "active"
    COMPLETED = "completed"
    PAUSED = "paused"
    ABANDONED = "abandoned"


class TaskPriority(str, Enum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    URGENT = "urgent"


class Event(BaseModel):
    timestamp: datetime
    type: EventType
    source: str
    project: Optional[str] = None
    raw_content: str
    metadata: Dict[str, Any] = Field(default_factory=dict)
    summary: Optional[str] = None
    category: Optional[Category] = None
    embedding: Optional[List[float]] = None

    # Stable identity for mutable documents (Google Docs, files, web pages).
    # When set, storage uses a deterministic ID so re-indexing the same doc
    # replaces the old embedding instead of creating a duplicate.
    source_doc_id: Optional[str] = None

    # Task-related fields
    task_id: Optional[str] = None
    task_confidence: Optional[float] = None
    task_assignment_method: Optional[str] = None  # "clustering", "ai", "manual"


class GitCommit(BaseModel):
    hash: str
    message: str
    author: str
    timestamp: datetime
    files_changed: List[str]
    insertions: int
    deletions: int
    branch: str
    diff: str = ""


class GitStash(BaseModel):
    name: str
    message: str
    timestamp: datetime
    files_changed: List[str]


class BrowserActivity(BaseModel):
    title: str
    url: str
    timestamp: datetime
    domain: str
    duration: Optional[int] = None


class TerminalCommand(BaseModel):
    command: str
    timestamp: datetime
    exit_code: Optional[int] = None
    working_dir: str


class Task(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    title: str
    description: Optional[str] = None
    status: TaskStatus = TaskStatus.ACTIVE
    priority: TaskPriority = TaskPriority.MEDIUM
    category: Optional[Category] = None
    project: Optional[str] = None
    start_time: datetime
    end_time: Optional[datetime] = None
    last_activity_time: Optional[datetime] = None
    duration_minutes: Optional[int] = None
    activities: List[str] = Field(default_factory=list)  # Activity IDs
    activity_count: int = 0
    embedding: Optional[List[float]] = None
    metadata: Dict[str, Any] = Field(default_factory=dict)
    confidence_score: float = 0.0
    parent_task_id: Optional[str] = None  # For sub-tasks
    subtask_ids: List[str] = Field(default_factory=list)
    tags: List[str] = Field(default_factory=list)
    created_at: datetime = Field(default_factory=datetime.now)
    updated_at: datetime = Field(default_factory=datetime.now)

    def add_activity(self, activity_id: str):
        """Add an activity to this task."""
        if activity_id not in self.activities:
            self.activities.append(activity_id)
            self.activity_count = len(self.activities)
            self.updated_at = datetime.now()

    def complete_task(self):
        """Mark task as completed."""
        self.status = TaskStatus.COMPLETED
        self.end_time = datetime.now()
        self.updated_at = datetime.now()
        if self.start_time and self.end_time:
            delta = self.end_time - self.start_time
            self.duration_minutes = int(delta.total_seconds() / 60)


class TaskSearchResult(BaseModel):
    task: Task
    score: float
    matching_activities: List[str] = Field(default_factory=list)
    match_reason: str


class DailySummary(BaseModel):
    date: str
    category_summaries: Dict[Category, str]
    overall_summary: str
    total_events: int
    events_by_category: Dict[Category, int]
    projects_worked_on: List[str]
    created_at: datetime


class DailyTaskSummary(BaseModel):
    date: str
    total_tasks: int
    completed_tasks: int
    active_tasks: int
    tasks_by_category: Dict[Category, int]
    tasks_by_project: Dict[str, int]
    task_summaries: Dict[str, str]  # task_id -> summary
    overall_summary: str
    total_time_minutes: int
    productivity_score: float
    created_at: datetime = Field(default_factory=datetime.now)

from enum import Enum
from typing import List, Dict, Any, Optional, Set
from pydantic import BaseModel
from datetime import datetime


class EntityType(Enum):
    """Types of entities that can be extracted from activities."""
    TECHNOLOGY = "technology"  # Programming languages, frameworks, tools
    PROJECT = "project"  # Project names, repositories
    CONCEPT = "concept"  # Technical concepts, methodologies
    PERSON = "person"  # People mentioned in activities
    ORGANIZATION = "organization"  # Companies, teams
    FILE = "file"  # Files, directories
    FEATURE = "feature"  # Features being worked on
    ISSUE = "issue"  # Bugs, problems being solved
    DOCUMENTATION = "documentation"  # Docs, guides, references


class RelationshipType(Enum):
    """Types of relationships between entities."""
    USES = "uses"  # Person uses Technology
    WORKS_ON = "works_on"  # Person works on Project/Feature
    LEARNS = "learns"  # Person learns Concept/Technology
    RELATES_TO = "relates_to"  # Generic relationship
    PART_OF = "part_of"  # Feature part of Project
    DEPENDS_ON = "depends_on"  # Project depends on Technology
    FIXES = "fixes"  # Activity fixes Issue
    DOCUMENTS = "documents"  # Activity documents Feature/Project
    COLLABORATES_WITH = "collaborates_with"  # Person collaborates with Person
    MENTIONS = "mentions"  # Activity mentions Entity


class Entity(BaseModel):
    """An entity extracted from activities."""
    id: str  # Unique identifier
    name: str  # Display name
    type: EntityType
    aliases: Set[str] = set()  # Alternative names
    confidence: float = 1.0  # Confidence in extraction
    first_seen: datetime
    last_seen: datetime
    mention_count: int = 1
    metadata: Dict[str, Any] = {}

    def __hash__(self):
        return hash(self.id)


class Relationship(BaseModel):
    """A relationship between two entities."""
    id: str
    source_entity_id: str
    target_entity_id: str
    relationship_type: RelationshipType
    confidence: float = 1.0
    first_seen: datetime
    last_seen: datetime
    strength: float = 1.0  # Relationship strength based on frequency
    metadata: Dict[str, Any] = {}
    supporting_activities: List[str] = []  # Activity IDs that support this relationship


class GraphNode(BaseModel):
    """A node in the knowledge graph with connected relationships."""
    entity: Entity
    outgoing_relationships: List[Relationship] = []
    incoming_relationships: List[Relationship] = []

    @property
    def all_relationships(self) -> List[Relationship]:
        """Get all relationships (incoming and outgoing)."""
        return self.outgoing_relationships + self.incoming_relationships

    def get_neighbors(self) -> Set[str]:
        """Get all connected entity IDs."""
        neighbors = set()
        for rel in self.all_relationships:
            if rel.source_entity_id == self.entity.id:
                neighbors.add(rel.target_entity_id)
            else:
                neighbors.add(rel.source_entity_id)
        return neighbors


class GraphPath(BaseModel):
    """A path through the knowledge graph."""
    entities: List[Entity]
    relationships: List[Relationship]
    total_strength: float
    path_length: int

    def __post_init__(self):
        self.path_length = len(self.relationships)
        self.total_strength = sum(rel.strength for rel in self.relationships)


class GraphContext(BaseModel):
    """Context retrieved from the knowledge graph."""
    query_entities: List[Entity]
    related_entities: List[Entity]
    relationships: List[Relationship]
    paths: List[GraphPath]
    context_strength: float

    def to_text_context(self) -> str:
        """Convert graph context to text for RAG prompt."""
        context_parts = []

        # Add entity context
        if self.query_entities:
            entity_names = [e.name for e in self.query_entities]
            context_parts.append(f"Primary entities: {', '.join(entity_names)}")

        # Add relationship context
        if self.relationships:
            rel_descriptions = []
            for rel in self.relationships[:5]:  # Limit to top 5
                rel_descriptions.append(
                    f"{rel.source_entity_id} {rel.relationship_type.value} {rel.target_entity_id}"
                )
            context_parts.append(f"Key relationships: {'; '.join(rel_descriptions)}")

        # Add path context for complex connections
        if self.paths:
            path_descriptions = []
            for path in self.paths[:3]:  # Limit to top 3 paths
                entity_names = [e.name for e in path.entities]
                path_descriptions.append(" -> ".join(entity_names))
            context_parts.append(f"Connection paths: {'; '.join(path_descriptions)}")

        return "\n".join(context_parts)


class EntityExtractionResult(BaseModel):
    """Result of entity extraction from an activity."""
    entities: List[Entity]
    relationships: List[Relationship]
    confidence: float
    extraction_method: str
    metadata: Dict[str, Any] = {}
