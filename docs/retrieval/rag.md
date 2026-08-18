# RAG & Knowledge Graph

`rust/patcha/src/db/retrieval/rag.rs`, `rust/patcha/src/db/retrieval/graphrag.rs`

Two RAG implementations that augment task analysis and daily summaries with
historical context pulled from the local store. Both call the configured
[LLM backend](../../README.md#llm-backend) (patcha-api or the local `claude` CLI)
for narrative generation and fall back gracefully when the LLM is unavailable.

---

## RagSystem (`rag.rs`)

Vector-only RAG. Takes current activities, retrieves semantically similar
historical activities from `sqlite-vec`, and asks the LLM for enhanced analysis
or summaries.

### Core capabilities

**Context for task analysis** — builds a composite embedding from an activity set
(weighted by content length), retrieves similar historical activities (excluding
the current session with a time buffer), and extracts workflow patterns, project
context, category insights, and tool usage.

**Context for a daily summary** — finds same-weekday activities from recent weeks,
computes per-project progression, and derives productivity patterns (activity
intensity, focus score vs. a recent average).

**Enhanced task analysis / daily summary** — sends the retrieved context to the
LLM to produce an enhanced title, contextual insights, accomplishments,
productivity notes, and recommendations. Falls back to the base analysis on any
LLM error.

**Contextual search** — semantic search with an optional context-expansion hook.

### Focus score

A 0–1 score that decreases with category diversity and project switching, used as
a rough proxy for focused vs. fragmented work.

---

## GraphRagSystem (`graphrag.rs`)

Hybrid RAG that combines vector similarity with knowledge-graph traversal via
`KnowledgeGraph` and `EntityExtractor`.

### Architecture

```
query activities
    ├── entity extraction (EntityExtractor)
    │       └── graph traversal (KnowledgeGraph)
    │               → related entities, relationships, paths
    └── composite embedding
            └── vector search (VectorStore / sqlite-vec)

combined and ranked by vector_weight (default 0.6) and graph_weight (default 0.4)
```

### Core capabilities

**Enhanced context retrieval** — runs both the vector and graph paths and merges
them into a unified result: ranked activities, a graph-context object (query
entities, related entities, relationships, paths, context strength), per-source
strengths, a combined score, and a source breakdown.

**Activity enrichment** — runs each activity through the entity extractor and adds
the extracted entities and relationships both to the in-memory `KnowledgeGraph`
and to the activity's metadata for downstream use.

**Graph-aware summary** — generates a narrative summary from both the activity
list and the graph context, falling back to a simple count summary on error.

**Graph insights** — returns graph statistics plus the most-connected and
most-recently-seen entities.

### Scoring

Results are position-ranked within each source
(`score = weight * (1 - rank/total) * source_strength`), merged, deduplicated by
`(type, timestamp)`, sorted by score, and truncated to the context limit.
