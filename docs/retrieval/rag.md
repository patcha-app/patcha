# RAG System

`patcha/db/retrieval/rag.py`, `patcha/db/retrieval/graphrag.py`

Two RAG implementations that augment task analysis and daily summaries with historical context pulled from the vector store.

---

## RAGSystem (`rag.py`)

Standard vector-only RAG. Takes current activities, retrieves semantically similar historical activities, and uses GPT-4o-mini to produce enhanced analysis or summaries.

### Core methods

**`retrieve_context_for_task_analysis(activities, context_limit=10)`**

Builds a context dict for an in-progress task:
- Generates a composite embedding from the activity set (weighted average, weight ∝ content length)
- Retrieves similar historical activities, excluding the current session ± 1h buffer
- Extracts from those results: workflow patterns, project context, category insights, temporal patterns, tool usage

**`retrieve_context_for_summary(target_date, activities, context_limit=15)`**

Builds context for a daily summary:
- Finds same-weekday activities from the past 4 weeks
- Retrieves project progression (per-project activity count over last 30 days)
- Computes productivity patterns (activity intensity, focus score vs. 7-day average)

**`enhance_task_analysis_with_rag(activities, base_analysis)`**

Calls `retrieve_context_for_task_analysis`, then sends a structured prompt to GPT-4o-mini asking for:
- Enhanced title
- Contextual insights
- Enhanced accomplishments list
- Productivity notes
- Recommendations

Falls back to `base_analysis` on any LLM error.

**`enhance_daily_summary_with_rag(target_date, activities, base_summary)`**

Same pattern for daily summaries. Adds `contextual_overview` and `rag_enhanced: True` to the base summary dict.

**`contextual_search(query, expand_context=True, limit=10)`**

Semantic search with optional context expansion (currently returns filtered vector results; expansion hook exists for future query-expansion logic).

### Focus score

`_calculate_focus_score` rates 0–1 based on:
- Category diversity: score decreases by 0.2 per additional unique category
- Project consistency: score decreases by 0.3 per additional unique project

---

## GraphRAGSystem (`graphrag.py`)

Hybrid RAG that combines vector similarity with knowledge graph traversal via `KnowledgeGraph` and `EntityExtractor`.

### Architecture

```
query activities
    ├── entity extraction (EntityExtractor)
    │       └── graph traversal (KnowledgeGraph)
    │               → related entities, relationships, paths
    └── composite embedding
            └── vector search (VectorStore)

combined and ranked by: vector_weight=0.6, graph_weight=0.4 (defaults)
```

### Core methods

**`retrieve_enhanced_context(query_activities, context_limit=15, vector_weight=0.6, graph_weight=0.4)`**

Main entry point. Runs both retrieval paths and calls `_combine_contexts` to produce a unified result dict with keys:
- `activities` — top-N ranked results
- `graph_context` — `GraphContext` dataclass (query entities, related entities, relationships, paths, context_strength)
- `vector_strength`, `graph_strength`, `combined_score`
- `source_breakdown` — `{vector: N, graph: N}`

**`enhance_activity_processing(activities)`**

Processes each activity through the entity extractor and adds extracted entities and relationships to:
1. The in-memory `KnowledgeGraph`
2. The activity's `metadata` dict (for downstream use)

**`generate_enhanced_summary_with_graph(activities, target_date)`**

Generates a narrative summary using GPT-4o-mini, providing both the activity list and `graph_context.to_text_context()` as context. Falls back to a simple count summary on error.

**`get_knowledge_graph_insights()`**

Returns graph statistics plus the 10 most-connected and 10 most-recently-seen entities.

### Scoring

Results are position-ranked within each source:
```
score = weight * (1 - rank/total) * source_strength
```

Vector and graph results are merged, deduplicated by `(type, timestamp)`, sorted by score, and truncated to `context_limit`.
