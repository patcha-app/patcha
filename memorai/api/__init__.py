from datetime import date, datetime, timedelta
from typing import Dict, List, Optional, Any
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from memorai.db.models import Category, Event, EventType
from memorai.db.store import VectorStore
from memorai.summary import DailySummarizer
from memorai.config import config
from memorai.db.retrieval.graphrag import GraphRAGSystem
from memorai.db.graph import KnowledgeGraph
from memorai.db.entities import EntityExtractor
from memorai.process import EventPreprocessor
import subprocess
import json


app = FastAPI(title="MemorAI API", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://localhost:3001", "http://127.0.0.1:3000", "http://127.0.0.1:3001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class CategoryNode(BaseModel):
    id: str
    name: str
    category: str
    event_count: int
    color: str


class CategoryDetails(BaseModel):
    category: str
    summary: str
    event_count: int
    events: List[Dict[str, Any]]


class SearchRequest(BaseModel):
    query: str
    limit: Optional[int] = 10
    use_graph: Optional[bool] = True
    date_range_days: Optional[int] = 30


class SearchResult(BaseModel):
    id: str
    timestamp: str
    type: str
    summary: str
    score: float
    source: str
    category: Optional[str] = None
    project: Optional[str] = None


class GraphSearchResponse(BaseModel):
    query: str
    results: List[SearchResult]
    graph_context: Dict[str, Any]
    total_results: int
    search_stats: Dict[str, Any]


class ActivityEvent(BaseModel):
    id: str
    timestamp: str
    type: str
    summary: str
    content: str
    category: Optional[str] = None
    project: Optional[str] = None
    source: str
    metadata: Optional[Dict[str, Any]] = None


class TaskItem(BaseModel):
    content: str
    status: str
    activeForm: str
    timestamp: Optional[str] = None


class DashboardStats(BaseModel):
    total_activities: int
    activities_today: int
    categories_active: int
    top_categories: List[Dict[str, Any]]
    recent_activities: List[ActivityEvent]
    knowledge_graph_stats: Dict[str, Any]


class EntityNode(BaseModel):
    id: str
    name: str
    type: str
    confidence: float
    mention_count: int
    last_seen: str


class RelationshipEdge(BaseModel):
    source: str
    target: str
    type: str
    strength: float
    confidence: float


class KnowledgeGraphData(BaseModel):
    entities: List[EntityNode]
    relationships: List[RelationshipEdge]
    stats: Dict[str, Any]


@app.get("/")
async def root():
    return {"message": "Digital Activity Tracker API"}


@app.get("/api/categories", response_model=List[CategoryNode])
async def get_categories(date_filter: Optional[str] = Query(None)):
    try:
        vector_store = VectorStore()
        target_date = date.fromisoformat(date_filter) if date_filter else date.today()

        summarizer = DailySummarizer(vector_store)
        summary = summarizer.load_summary(target_date)

        if not summary:
            print(f"No cached summary for {target_date}, getting raw event counts...")
            events = vector_store.get_events_by_date(target_date)

            if not events:
                return []

            category_counts = {}
            for event in events:
                category = event.get("payload", {}).get("category")
                if category:
                    category_counts[category] = category_counts.get(category, 0) + 1
        else:
            category_counts = {cat.value: count for cat, count in summary.events_by_category.items()}

        color_map = {
            "Coding": "#3b82f6",
            "Debugging": "#ef4444",
            "Research": "#10b981",
            "Learning": "#8b5cf6",
            "Writing": "#f59e0b",
            "Communication": "#06b6d4",
            "Other": "#6b7280"
        }

        nodes = []
        for category, count in category_counts.items():
            if count > 0:
                nodes.append(CategoryNode(
                    id=category,
                    name=category,
                    category=category,
                    event_count=count,
                    color=color_map.get(category, "#6b7280")
                ))

        return nodes

    except Exception as e:
        print(f"Error getting categories: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch categories")


@app.get("/api/category/{category_name}", response_model=CategoryDetails)
async def get_category_details(
    category_name: str,
    date_filter: Optional[str] = Query(None)
):
    try:
        try:
            category = Category(category_name)
        except ValueError:
            raise HTTPException(status_code=400, detail="Invalid category")

        vector_store = VectorStore()
        target_date = date.fromisoformat(date_filter) if date_filter else date.today()

        summarizer = DailySummarizer(vector_store)
        summary = summarizer.generate_daily_summary(target_date)

        if not summary:
            raise HTTPException(status_code=404, detail="No data found for the specified date")

        category_summary = summary.category_summaries.get(category, "No summary available")
        event_count = summary.events_by_category.get(category, 0)

        if event_count == 0:
            return CategoryDetails(
                category=category.value,
                summary=category_summary,
                event_count=0,
                events=[]
            )

        all_events_data = vector_store.get_events_by_date(target_date)
        events_data = [
            event for event in all_events_data
            if event.get("payload", {}).get("category") == category.value
        ]

        events = []
        for event_data in events_data[:20]:
            payload = event_data["payload"]
            events.append({
                "id": event_data["id"],
                "timestamp": payload.get("timestamp"),
                "type": payload.get("type"),
                "summary": payload.get("summary", "No summary"),
                "project": payload.get("project"),
                "source": payload.get("source")
            })

        events.sort(key=lambda x: x["timestamp"], reverse=True)

        return CategoryDetails(
            category=category.value,
            summary=category_summary,
            event_count=event_count,
            events=events
        )

    except HTTPException:
        raise
    except Exception as e:
        print(f"Error getting category details: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch category details")


@app.get("/api/health")
async def health_check():
    try:
        vector_store = VectorStore()
        info = vector_store.get_collection_info()

        return {
            "status": "healthy",
            "vector_store": {
                "connected": True,
                "collection": info.get("name", "unknown"),
                "points_count": info.get("points_count", 0)
            },
            "config": {
                "qdrant_url": config.qdrant_url,
                "openai_api_configured": bool(config.openai_api_key)
            }
        }
    except Exception as e:
        return {
            "status": "unhealthy",
            "error": str(e)
        }


@app.post("/api/search", response_model=GraphSearchResponse)
async def search_activities(request: SearchRequest):
    try:
        vector_store = VectorStore()
        preprocessor = EventPreprocessor()
        knowledge_graph = KnowledgeGraph()
        entity_extractor = EntityExtractor()

        if request.use_graph:
            graph_rag = GraphRAGSystem(vector_store, preprocessor, knowledge_graph, entity_extractor)

            query_embedding = preprocessor.generate_embedding(request.query)
            if not query_embedding:
                raise HTTPException(status_code=400, detail="Failed to generate query embedding")

            vector_results = vector_store.search_events(
                query_vector=query_embedding,
                limit=request.limit * 2
            )

            if not vector_results:
                return GraphSearchResponse(
                    query=request.query,
                    results=[],
                    graph_context={},
                    total_results=0,
                    search_stats={"vector_results": 0, "graph_results": 0}
                )

            query_activities = []
            for result in vector_results[:5]:
                event = Event(
                    type=EventType(result.get("type", "browser")),
                    timestamp=datetime.fromisoformat(result.get("timestamp", datetime.now().isoformat())),
                    source=result.get("source", "unknown"),
                    raw_content=result.get("content", ""),
                    summary=result.get("summary", ""),
                    project=result.get("project"),
                    metadata=result
                )
                query_activities.append(event)

            enhanced_context = graph_rag.retrieve_enhanced_context(
                query_activities,
                context_limit=request.limit
            )

            results = []
            for i, activity in enumerate(enhanced_context["activities"]):
                if isinstance(activity, dict):
                    payload = activity.get("payload", activity)
                    results.append(SearchResult(
                        id=activity.get("id", f"result_{i}"),
                        timestamp=payload.get("timestamp", ""),
                        type=payload.get("type", "unknown"),
                        summary=payload.get("summary", "No summary"),
                        score=0.8,
                        source=payload.get("source", "unknown"),
                        category=payload.get("category"),
                        project=payload.get("project")
                    ))
                else:
                    results.append(SearchResult(
                        id=f"event_{i}",
                        timestamp=activity.timestamp.isoformat(),
                        type=activity.type.value if hasattr(activity.type, 'value') else str(activity.type),
                        summary=activity.summary or "No summary",
                        score=0.8,
                        source=activity.source,
                        category=activity.category.value if activity.category else None,
                        project=activity.project
                    ))

            return GraphSearchResponse(
                query=request.query,
                results=results,
                graph_context={
                    "entities": len(enhanced_context["graph_context"].query_entities),
                    "relationships": len(enhanced_context["graph_context"].relationships),
                    "context_strength": enhanced_context["graph_strength"]
                },
                total_results=len(results),
                search_stats=enhanced_context["source_breakdown"]
            )

        else:
            query_embedding = preprocessor.generate_embedding(request.query)
            if not query_embedding:
                raise HTTPException(status_code=400, detail="Failed to generate query embedding")

            vector_results = vector_store.search_events(
                query_vector=query_embedding,
                limit=request.limit
            )

            results = []
            for i, result in enumerate(vector_results):
                results.append(SearchResult(
                    id=result.get("id", f"result_{i}"),
                    timestamp=result.get("timestamp", ""),
                    type=result.get("type", "unknown"),
                    summary=result.get("summary", "No summary"),
                    score=result.get("score", 0.0),
                    source=result.get("source", "unknown"),
                    category=result.get("category"),
                    project=result.get("project")
                ))

            return GraphSearchResponse(
                query=request.query,
                results=results,
                graph_context={},
                total_results=len(results),
                search_stats={"vector_results": len(results), "graph_results": 0}
            )

    except Exception as e:
        print(f"Error in search: {e}")
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")


@app.get("/api/dashboard", response_model=DashboardStats)
async def get_dashboard_stats(days: int = Query(7, description="Number of days to include in stats")):
    try:
        vector_store = VectorStore()
        knowledge_graph = KnowledgeGraph()

        end_date = date.today()
        start_date = end_date - timedelta(days=days)

        today_events = vector_store.get_events_by_date(end_date)

        total_activities = 0
        category_counts = {}
        recent_activities = []

        for event_data in today_events[:10]:
            payload = event_data.get("payload", {})
            total_activities += 1

            category = payload.get("category", "Other")
            category_counts[category] = category_counts.get(category, 0) + 1

            recent_activities.append(ActivityEvent(
                id=event_data.get("id", "unknown"),
                timestamp=payload.get("timestamp", ""),
                type=payload.get("type", "unknown"),
                summary=payload.get("summary", "No summary"),
                content=payload.get("content", "")[:200] + "..." if len(payload.get("content", "")) > 200 else payload.get("content", ""),
                category=category,
                project=payload.get("project"),
                source=payload.get("source", "unknown"),
                metadata=payload.get("metadata", {})
            ))

        top_categories = [
            {"name": cat, "count": count, "percentage": round(count/total_activities*100, 1)}
            for cat, count in sorted(category_counts.items(), key=lambda x: x[1], reverse=True)
        ]

        kg_stats = knowledge_graph.get_graph_stats()

        return DashboardStats(
            total_activities=total_activities,
            activities_today=len(today_events),
            categories_active=len(category_counts),
            top_categories=top_categories,
            recent_activities=recent_activities,
            knowledge_graph_stats=kg_stats
        )

    except Exception as e:
        print(f"Error getting dashboard stats: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch dashboard stats")


@app.get("/api/knowledge-graph", response_model=KnowledgeGraphData)
async def get_knowledge_graph_data(limit: int = Query(50, description="Max entities to return")):
    try:
        knowledge_graph = KnowledgeGraph()

        all_entities = list(knowledge_graph.entities.values())
        all_relationships = list(knowledge_graph.relationships.values())

        top_entities = sorted(all_entities, key=lambda e: e.mention_count, reverse=True)[:limit]

        entities = []
        entity_ids = set()

        for entity in top_entities:
            entities.append(EntityNode(
                id=entity.id,
                name=entity.name,
                type=entity.type.value,
                confidence=entity.confidence,
                mention_count=entity.mention_count,
                last_seen=entity.last_seen.isoformat()
            ))
            entity_ids.add(entity.id)

        relationships = []
        for rel in all_relationships:
            if rel.source_entity_id in entity_ids and rel.target_entity_id in entity_ids:
                relationships.append(RelationshipEdge(
                    source=rel.source_entity_id,
                    target=rel.target_entity_id,
                    type=rel.relationship_type.value,
                    strength=rel.strength,
                    confidence=rel.confidence
                ))

        stats = knowledge_graph.get_graph_stats()

        return KnowledgeGraphData(
            entities=entities,
            relationships=relationships,
            stats=stats
        )

    except Exception as e:
        print(f"Error getting knowledge graph data: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch knowledge graph data")


@app.get("/api/activities", response_model=List[ActivityEvent])
async def get_activities(
    date_filter: Optional[str] = Query(None),
    category: Optional[str] = Query(None),
    limit: int = Query(20)
):
    try:
        vector_store = VectorStore()
        target_date = date.fromisoformat(date_filter) if date_filter else date.today()

        events_data = vector_store.get_events_by_date(target_date)

        if category:
            events_data = [
                event for event in events_data
                if event.get("payload", {}).get("category") == category
            ]

        activities = []
        for event_data in events_data[:limit]:
            payload = event_data.get("payload", {})
            activities.append(ActivityEvent(
                id=event_data.get("id", "unknown"),
                timestamp=payload.get("timestamp", ""),
                type=payload.get("type", "unknown"),
                summary=payload.get("summary", "No summary"),
                content=payload.get("content", ""),
                category=payload.get("category"),
                project=payload.get("project"),
                source=payload.get("source", "unknown"),
                metadata=payload.get("metadata", {})
            ))

        activities.sort(key=lambda x: x.timestamp, reverse=True)

        return activities

    except Exception as e:
        print(f"Error getting activities: {e}")
        raise HTTPException(status_code=500, detail="Failed to fetch activities")


@app.get("/api/tasks", response_model=List[TaskItem])
async def get_tasks():
    try:
        result = subprocess.run(
            ["uv", "run", "memorai", "tasks", "--format", "json"],
            capture_output=True,
            text=True,
            check=False
        )

        if result.returncode != 0:
            print(f"memorai tasks command failed: {result.stderr}")
            return []

        try:
            tasks_data = json.loads(result.stdout)
            tasks = []

            if isinstance(tasks_data, list):
                for task in tasks_data:
                    if isinstance(task, dict):
                        tasks.append(TaskItem(
                            content=task.get('content', ''),
                            status=task.get('status', 'pending'),
                            activeForm=task.get('activeForm', ''),
                            timestamp=task.get('timestamp')
                        ))

            return tasks

        except json.JSONDecodeError:
            print(f"Failed to parse tasks JSON: {result.stdout}")
            return []

    except Exception as e:
        print(f"Error getting tasks: {e}")
        return []
