# MCP Server

`rust/patcha/src/mcp/server.rs`

Exposes patcha's retrieval functions as [Model Context Protocol](https://modelcontextprotocol.io)
tools so any MCP-compatible client (Claude Desktop, Claude Code, etc.) can query
the user's activity history directly. Built on [`rmcp`](https://github.com/modelcontextprotocol/rust-sdk).

The MCP server is a **read-only consumer** — it never writes to the store. The
daemon (`patcha daemon`) runs as a separate process and populates the local
database; the MCP server only reads from it.

## Running

```bash
patcha mcp            # stdio (default)
patcha mcp --stdio    # stdio, explicit
patcha mcp --port 6969   # Streamable HTTP transport
```

| Flag      | Default | Description                          |
| --------- | ------- | ------------------------------------ |
| `--stdio` | true    | Serve over stdio                     |
| `--port`  | 6969    | Port for the Streamable HTTP transport |

## Tools

### `get_working_memory`

Compact, deduplicated summary of recent activity.

| Param     | Type    | Default | Description                   |
| --------- | ------- | ------- | ----------------------------- |
| `minutes` | integer | 15      | How many minutes back to look |

Use this to understand what the user is currently working on before answering.

### `search_activity`

Semantic search over the full history (vector search + optional reranking). For
git commits/stashes, the full diff is included in the result.

| Param   | Type    | Default  | Description                              |
| ------- | ------- | -------- | ---------------------------------------- |
| `query` | string  | required | What to search for                       |
| `limit` | integer | 10       | Number of results                        |
| `app`   | string  | —        | Filter to a specific app (e.g. `Arc`)    |

### `get_recent_activity`

Deduped log of raw activity over the last N hours — broader context than working
memory.

| Param   | Type    | Default | Description             |
| ------- | ------- | ------- | ----------------------- |
| `hours` | integer | 3       | How many hours back     |
| `app`   | string  | —       | Filter to a specific app |

### `get_activity_context`

Temporal context around a moment — what happened right before/after an event.
Anchor by app, time, or both. Answers ordering/adjacency questions that semantic
search can't.

| Param       | Type    | Default | Description                                |
| ----------- | ------- | ------- | ------------------------------------------ |
| `app`       | string  | —       | Anchor on this app's nearest event         |
| `time`      | string  | —       | Anchor near this ISO 8601 timestamp        |
| `direction` | string  | `both`  | `before`, `after`, or `both`               |
| `count`     | integer | 3       | Events on each side                        |

### `get_session`

The full work session around a moment — every app and event between two idle
gaps. Anchor by app, time, or both.

| Param  | Type   | Default | Description                         |
| ------ | ------ | ------- | ----------------------------------- |
| `app`  | string | —       | Anchor on this app's nearest event  |
| `time` | string | —       | Anchor near this ISO 8601 timestamp |

### `find_connected`

Every event structurally connected to a `file`, `project`, `url`, or `app`
(provide exactly one), via the activity knowledge graph.

| Param     | Type   | Description                    |
| --------- | ------ | ----------------------------- |
| `file`    | string | e.g. `collectors/accessibility.rs` |
| `project` | string | e.g. `patcha`                 |
| `url`     | string | exact URL visited             |
| `app`     | string | e.g. `Arc`                    |

## Configuration

The MCP server reads the same local configuration as the daemon (see
`rust/patcha/src/config.rs`). Notably:

- **No API key is required** — query embeddings run locally via fastembed.
- The store is the local SQLite database at `PATCHA_DB_PATH`
  (`~/.patcha/patcha.db`).

### Claude Desktop

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "patcha": {
      "command": "/path/to/patcha",
      "args": ["mcp", "--stdio"]
    }
  }
}
```

## Data flow

```
MCP client (Claude Desktop / Claude Code / HTTP client)
    |
    |  MCP protocol (stdio or Streamable HTTP)
    v
patcha mcp — PatchaServer tool handlers
    |
    +-- get_working_memory / get_recent_activity --> retrieval::context (vector store)
    +-- search_activity --------------------------> embed query (local) + sqlite-vec + rerank
    +-- get_activity_context / get_session /
        find_connected ---------------------------> retrieval::graph_context (activity graph)

patcha daemon (separate process) --> writes events to the local store
patcha mcp                        --> reads only
```

## Limitations

The MCP tools are a read-only subset of the CLI. Date/category filtering, task
management, and compaction are CLI-only.
