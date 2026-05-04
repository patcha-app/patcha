# MCP Server

`patcha/mcp_server.py`

Exposes patcha's retrieval functions as [Model Context Protocol](https://modelcontextprotocol.io) tools so any MCP-compatible AI client (e.g. Claude Desktop) can query the user's activity history directly.

The MCP server is a **read-only consumer** — it never writes to Qdrant. The `ActivityDaemon` runs as a separate process and populates the vector store; the MCP server only retrieves from it.

## Tools

### `get_working_memory`

Returns a compact, deduplicated summary of the user's recent device activity.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `minutes` | integer | 15 | How many minutes back to look |

**Calls:** `context.get_working_memory(store, minutes)`

**Output example:**
```
# Working memory (last 15m)
[14:32] terminal: git status
[14:33] browser: patcha docs | github.com
[14:35] git_commit: fix: collector dedup | patcha/collectors/accessibility.py
```

Use this to understand what the user is currently working on before answering questions or making suggestions.

---

### `search_activity`

Semantic search over the full activity history using vector similarity.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `query` | string | required | What to search for in past activity |
| `limit` | integer | 5 | Number of results to return |

**Calls:** `context.search_activity(store, preprocessor, query, limit)`

1. Generates an embedding for `query` via `EventPreprocessor.generate_embedding`
2. Runs `store.search_events(embedding, limit=limit)` against Qdrant
3. Returns each result with a similarity score and formatted event line

**Output example:**
```
# Search results for "qdrant vector search setup"
[score=0.912 | 2026-04-28 11:04] terminal: docker run -p 6333:6333 qdrant/qdrant
[score=0.874 | 2026-04-28 11:07] browser: Qdrant Quickstart | qdrant.tech
```

Use this to find specific past work — e.g. `authentication bug fix`, `npm install error`.

---

### `get_recent_activity`

A deduplicated log of raw activity over the last N hours.

**Parameters:**

| Name | Type | Default | Description |
|------|------|---------|-------------|
| `hours` | integer | 3 | How many hours back to look |

**Calls:** `context.get_recent_activity(store, hours)`

Same pipeline as `get_working_memory` but over a longer window. Uses the same cosine-similarity deduplication threshold (`config.working_memory_dedup_threshold`).

**Output example:**
```
# Recent activity (last 3h)
[12:01] terminal: uv run pytest
[12:15] browser: Python asyncio docs | docs.python.org
[13:40] git_commit: feat: mcp http transport | patcha/mcp_server.py
```

Use this for broader historical context — what the user has been working on over the past few hours, not just the last few minutes.

---

## Transport

The server supports two transports selectable at startup.

### stdio (default)

Used for Claude Desktop and most MCP clients. The server reads/writes MCP protocol messages over stdin/stdout.

```
patcha-mcp
patcha-mcp --stdio
```

### HTTP

Serves over HTTP using Starlette + uvicorn. Useful for non-stdio MCP clients or local debugging.

```
patcha-mcp --http
patcha-mcp --http --port 7861 --host 127.0.0.1
```

The MCP endpoint is mounted at `/mcp`. Uses `StreamableHTTPSessionManager` in stateless mode.

**Flags:**

| Flag | Default | Description |
|------|---------|-------------|
| `--stdio` | true | Serve over stdio |
| `--http` | false | Serve over HTTP |
| `--port` | 7861 | HTTP port |
| `--host` | 127.0.0.1 | HTTP host |

---

## Runtime

`VectorStore` and `EventPreprocessor` are lazy-initialized singletons — created on the first tool call, not at startup. This means the first call to any tool may be slower than subsequent calls.

`EventPreprocessor` requires a valid `OPENAI_API_KEY` to generate embeddings. If the key is missing, `search_activity` will return an embedding-failure message; `get_working_memory` and `get_recent_activity` are unaffected.

---

## Configuration

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | required | Used by `EventPreprocessor` for query embeddings |
| `QDRANT_URL` | `http://localhost:6333` | Remote Qdrant instance |
| `QDRANT_PATH` | `~/.patcha/qdrant_storage` | Local Qdrant storage (used if `QDRANT_URL` is unset) |

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "patcha": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/patcha-3", "patcha-mcp"]
    }
  }
}
```

---

## Data flow

```
MCP client (Claude Desktop / HTTP client)
    |
    | MCP protocol (stdio or HTTP /mcp)
    v
mcp_server.py — call_tool()
    |
    +-- get_working_memory  -->  context.get_working_memory(store, minutes)
    |                                |
    +-- get_recent_activity -->  context.get_recent_activity(store, hours)
    |                                |
    |                            store.get_recent_events_with_vectors(since)
    |                            _dedup_by_similarity(rows, threshold)
    |
    +-- search_activity  -->  context.search_activity(store, preprocessor, query, limit)
                                  |
                              preprocessor.generate_embedding(query)  [OpenAI API]
                              store.search_events(embedding, limit)   [Qdrant ANN]

ActivityDaemon (separate process) --> writes events to Qdrant
MCP server                         --> reads only
```

---

## Limitations

The MCP tools are a subset of the CLI's capabilities:

| Capability | CLI | MCP |
|-----------|-----|-----|
| Working memory | yes | yes |
| Recent activity | yes | yes |
| Semantic search | yes | yes |
| Filter by date | yes | no |
| Filter by category/project | yes | no |
| Task management | yes | no |
| MCP Resources | n/a | no |
| MCP Prompts | n/a | no |
