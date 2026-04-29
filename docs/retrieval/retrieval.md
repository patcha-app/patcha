# Context Retrieval

`memorai/db/retrieval/context.py`

The retrieval layer is the primary interface between the vector store and AI agent consumers. It provides three read functions that translate raw Qdrant records into human-readable text blocks.

## Functions

### `get_working_memory(store, minutes=15)`

Returns a compact, deduplicated summary of the last N minutes of activity. Intended for the "what is the user doing right now" use case.

- Fetches events with their stored vectors via `store.get_recent_events_with_vectors(since)`
- Runs cosine-similarity deduplication to collapse near-identical consecutive events of the same type (e.g. repeated window focus events)
- Formats each event into a single line: `[HH:MM] type: detail`

Output example:
```
# Working memory (last 15m)
[14:32] terminal: git status
[14:33] browser: memorai docs | github.com
[14:35] git_commit: fix: collector dedup | memorai/collectors/accessibility.py
```

### `get_recent_activity(store, hours=3)`

Same pipeline as `get_working_memory` but over a longer window (hours, not minutes). Used for broader historical context. The dedup threshold is the same — controlled by `config.working_memory_dedup_threshold`.

### `search_activity(store, preprocessor, query, limit=5)`

Semantic search over the full activity history.

1. Generates an embedding for the query string using `EventPreprocessor.generate_embedding`
2. Calls `store.search_events(embedding, limit=limit)` to get the top-N cosine-nearest events
3. Returns each result with its similarity score and formatted event line

Output example:
```
# Search results for "qdrant setup"
[score=0.912 | 2026-04-28 11:04] terminal: docker run -p 6333:6333 qdrant/qdrant
[score=0.874 | 2026-04-28 11:07] browser: Qdrant Quickstart | qdrant.tech
```

## Deduplication

`_dedup_by_similarity(rows, threshold)` operates per event type. For each incoming row, it computes cosine similarity against the last seen vector for that type. If similarity exceeds the threshold, the incoming row replaces the previous one (keeping the most recent version of near-duplicate events). This prevents a burst of identical window or screen events from flooding the output.

The threshold is configured via `config.working_memory_dedup_threshold`.

## Event Line Format

`_format_line(payload)` renders each event type differently:

| Type | Format |
|------|--------|
| `terminal` | command string (truncated to 120 chars) |
| `browser` | `title \| domain` |
| `git_commit` / `git_stash` | commit message + up to 5 changed files |
| `git_staged` | raw content (120 chars) |
| `screen` / `window` | `app_name — window_title` |
| other | raw content (120 chars) |
