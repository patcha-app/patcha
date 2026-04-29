# MCP Server

`memorai/mcp_server.py`

Exposes memorai's retrieval functions as [Model Context Protocol](https://modelcontextprotocol.io) tools so that any MCP-compatible AI client (e.g. Claude) can query the user's activity history directly.

## Tools

### `get_working_memory`

Returns a compact summary of the user's recent device activity.

```json
{
  "minutes": 15
}
```

Maps to `context.get_working_memory(store, minutes)`. Use this to understand what the user is currently working on before answering questions or making suggestions.

### `search_activity`

Semantic search over the full activity history.

```json
{
  "query": "qdrant vector search setup",
  "limit": 5
}
```

Maps to `context.search_activity(store, preprocessor, query, limit)`. Returns the most relevant past events with similarity scores.

### `get_recent_activity`

A deduped log of raw activity over the last N hours.

```json
{
  "hours": 3
}
```

Maps to `context.get_recent_activity(store, hours)`. Use for broader historical context — what the user has been working on over the past few hours.

## Runtime

The server uses lazy-initialized singletons for `VectorStore` and `EventPreprocessor` — both are created on first tool call.

The server runs over stdio (`mcp.server.stdio.stdio_server`) and is started by calling `mcp_server.main()`, which is the entry point registered in `pyproject.toml`.

## Configuration (Claude Desktop)

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "memorai": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/memorai-3", "memorai-mcp"]
    }
  }
}
```
