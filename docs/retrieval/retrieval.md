# Context Retrieval

`rust/patcha/src/db/retrieval/`

The retrieval layer is the primary read interface over the local store. It turns
`sqlite-vec` records and the activity graph into human-readable text blocks for
CLI output and MCP consumers. It never writes.

## Vector-based retrieval (`context.rs`)

### `get_working_memory(store, minutes)`

A compact, deduplicated summary of the last N minutes — the "what is the user
doing right now" view.

- Fetches recent events (with their vectors) since the cutoff
- Runs cosine-similarity dedup to collapse near-identical consecutive events of
  the same type (e.g. repeated window-focus events)
- Formats each event to one line: `[HH:MM] type: detail`

```
# Working memory (last 15m)
[14:32] terminal: git status
[14:33] browser: patcha docs | github.com
[14:35] git_commit: fix: collector dedup | collectors/accessibility.rs
```

### `get_recent_activity(store, hours)`

Same pipeline as `get_working_memory` over a longer window (hours). Uses the same
dedup threshold (`working_memory_dedup_threshold`).

### `search_activity(store, embedder, query, limit)`

Semantic search over the full history:

1. Embeds the query locally with the `Embedder` (BGE query prefix applied)
2. Runs `sqlite-vec` nearest-neighbor search for the top results
3. Optionally reranks candidates with the cross-encoder reranker
4. Returns each result with its score and a formatted line

```
# Search results for "sqlite-vec setup"
[score=0.912 | 2026-08-01 11:04] terminal: cargo add sqlite-vec
[score=0.874 | 2026-08-01 11:07] browser: sqlite-vec docs | github.com
```

The underlying ranked search lives in `search.rs` (`SemanticSearch`), which also
supports `search_by_date_range`.

## Graph-based retrieval (`graph_context.rs`)

These functions answer *structural* and *temporal* questions using the activity
knowledge graph rather than pure vector similarity:

- **`get_activity_context(graph, app, time, direction, count)`** — what the user
  was doing right before/after a moment, anchored by app and/or time.
- **`get_session(graph, app, time)`** — the full work session around a moment:
  every app and event between two idle gaps.
- **`find_connected(...)`** — every event structurally connected to a specific
  `file`, `project`, `url`, or `app`.

## Deduplication

Dedup operates per event type: for each incoming row, cosine similarity is
computed against the last kept vector of that type; if it exceeds
`working_memory_dedup_threshold`, the incoming row replaces the previous one.
This keeps a burst of identical window/screen events from flooding the output.

## Event line format

Each event type renders differently, for example:

| Type                     | Format                              |
| ------------------------ | ----------------------------------- |
| `terminal`               | the command (truncated)             |
| `browser`                | `title \| domain`                   |
| `git_commit` / `git_stash` | commit message + a few changed files |
| `screen` / `window`      | `app — window title`                |
| other                    | raw content (truncated)             |

All of the above are surfaced to AI clients through the
[MCP server](mcp.md); the RAG layer that builds richer analysis on top is
documented in [rag.md](rag.md).
