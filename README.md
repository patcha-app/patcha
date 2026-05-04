# patcha

Localised observability for your computer. Patcha runs as a background daemon, collects activity from your browser, terminal, git, and screen, stores it in a local vector database, and lets you query it semantically - via CLI or as an MCP tool in Claude Desktop.

## How it works

1. **Daemon** polls activity sources on a configurable interval and stores raw events in [Qdrant](https://qdrant.tech).
2. **Collectors** pull from browser history, shell history, git commits/stashes/staging, and macOS Accessibility/OCR. See [docs/collectors.md](docs/collectors.md).
3. **Embedding** converts raw events into vectors via OpenAI `text-embedding-3-small`. See [docs/embedding.md](docs/embedding.md).
4. **Retrieval** exposes working memory, recent activity, and semantic search. See [docs/retrieval/retrieval.md](docs/retrieval/retrieval.md).
5. **Compaction** nightly converts raw events into structured tasks and prunes the vector store. See [docs/compaction.md](docs/compaction.md).
6. **MCP server** exposes retrieval as tools for Claude Desktop or any MCP client. See [docs/retrieval/mcp.md](docs/retrieval/mcp.md).

## Requirements

- macOS (Accessibility APIs and AppleScript are macOS-only)
- Python 3.11+
- [uv](https://github.com/astral-sh/uv)
- OpenAI API key (for embeddings)
- Qdrant — local path or remote instance

## Installation

```bash
git clone https://github.com/xtanion/patcha.git
cd patcha
uv sync
```

Set your OpenAI key (or patcha will prompt on first run):

```bash
echo "OPENAI_API_KEY=sk-..." >> ~/.patcha/.env
```

Install git hooks:

```bash
bash scripts/install-hooks.sh
```

### macOS Accessibility permission

The screen/window collectors require Accessibility access. Go to:

**System Settings > Privacy & Security > Accessibility** and add your terminal app (or the patcha binary if using the built executable).

## Getting started

Start the background daemon:

```bash
uv run patcha start-daemon
```

Check that it's running:

```bash
uv run patcha daemon-status
```

Collect a manual snapshot:

```bash
uv run patcha collect
```

Search your activity:

```bash
uv run patcha search "qdrant setup"
```

Stop the daemon:

```bash
uv run patcha stop-daemon
```

## CLI reference

| Command | Description |
|---------|-------------|
| `start-daemon` | Start the background activity collector |
| `stop-daemon` | Stop the daemon |
| `daemon-status` | Show daemon health and collection stats |
| `collect` | Run a one-shot collection from all sources |
| `search <query>` | Semantic search over activity history |
| `observe` | Cluster today's activity into themes |
| `summarize` | Generate a written summary of a day's activity |
| `review` | Review activity over a date range |
| `tasks` | List identified tasks |
| `task-details <id>` | Show full detail for a task |
| `compact-day` | Manually trigger compaction for a past date |
| `status` | Show config and store health |
| `config` | Get or set configuration values |

Run `uv run patcha --help` or `uv run patcha <command> --help` for full options.

## MCP (Claude Desktop)

The MCP server exposes three tools: `get_working_memory`, `get_recent_activity`, and `search_activity`.

Add to `claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "patcha": {
      "command": "uv",
      "args": ["run", "--project", "/path/to/patcha", "patcha-mcp"]
    }
  }
}
```

For HTTP transport: `uv run patcha-mcp --http --port 7861`

See [docs/retrieval/mcp.md](docs/retrieval/mcp.md) for full tool documentation.

## Configuration

Patcha reads configuration from `~/.patcha/.env`. Key variables:

| Variable | Default | Description |
|----------|---------|-------------|
| `OPENAI_API_KEY` | required | Used for generating embeddings |
| `QDRANT_PATH` | `~/.patcha/qdrant_storage` | Local Qdrant storage path |
| `QDRANT_URL` | — | Remote Qdrant instance (overrides `QDRANT_PATH`) |

Run `uv run patcha config` to inspect or modify settings at runtime.

## Development

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format .
```

### Commit convention

Commits must follow `type: description` where type is one of:

- `feat:` — new feature
- `fix:` — bug fix
- `chore:` — maintenance, deps, tooling

Scope is optional: `feat(cli): add --json flag`.

The `commit-msg` hook enforces this. Install hooks with `bash scripts/install-hooks.sh`.

## Docs

| Topic | File |
|-------|------|
| Collectors (browser, terminal, git, screen) | [docs/collectors.md](docs/collectors.md) |
| Embedding pipeline | [docs/embedding.md](docs/embedding.md) |
| Retrieval (working memory, search) | [docs/retrieval/retrieval.md](docs/retrieval/retrieval.md) |
| MCP server | [docs/retrieval/mcp.md](docs/retrieval/mcp.md) |
| Daily compaction | [docs/compaction.md](docs/compaction.md) |
