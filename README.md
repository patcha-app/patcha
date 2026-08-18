# patcha

Local-first observability for your computer. Patcha runs as a background daemon,
collects activity from your browser, terminal, git, and screen, stores it in a
**local** vector database, and lets you query it semantically — from the CLI or
as an MCP tool inside Claude and other MCP clients.

Everything stays on your machine. Embeddings and screen understanding run
on-device; the only network calls are optional (LLM summarization, which can
also run through a local `claude` CLI — see [LLM backend](#llm-backend)).

> **Platform:** macOS only. Patcha relies on macOS Accessibility, Screen
> Recording, and Vision (OCR) APIs.

---

## How it works

1. **Daemon** (`patcha daemon`) polls activity sources on a configurable
   interval and writes events to a local SQLite database.
2. **Collectors** pull from browser history, shell history, git
   (commits/stashes/staging), and the active window/screen via macOS
   Accessibility + OCR. See [docs/collectors.md](docs/collectors.md).
3. **Perception** filters redundant frames and captions screenshots on-device
   (FastVLM gist + MobileCLIP visual prefilter) so retrieval works on *what you
   were doing*, not just literal on-screen text.
4. **Embedding** turns events into vectors with a local
   [fastembed](https://github.com/Anush008/fastembed-rs) model (BGE). No API key
   required. See [docs/embedding.md](docs/embedding.md).
5. **Storage & retrieval** live in SQLite with the
   [`sqlite-vec`](https://github.com/asg017/sqlite-vec) extension for
   approximate-nearest-neighbor search, plus a knowledge graph for structural
   queries. See [docs/retrieval/retrieval.md](docs/retrieval/retrieval.md).
6. **Compaction** nightly folds raw events into structured tasks and prunes the
   store. See [docs/compaction.md](docs/compaction.md).
7. **MCP server** (`patcha mcp`) exposes retrieval as tools for Claude or any
   MCP client. See [docs/retrieval/mcp.md](docs/retrieval/mcp.md).

The core is written in Rust (`rust/patcha`). A native macOS menu bar app
(`swift-xcode/`) manages the daemon so you never need a terminal.

---

## Requirements

- macOS (Apple Silicon or Intel)
- [Rust](https://rustup.rs) (stable) to build the CLI/daemon
- Xcode or the Xcode Command Line Tools (for the Swift helper binaries and the
  menu bar app)
- Optional: the [`claude` CLI](https://docs.claude.com/en/docs/claude-code)
  installed and logged in, to run AI features (summaries, categorization, chat)
  fully locally without a patcha account

No OpenAI key and no external vector database are required — embeddings and the
vector store are local.

---

## Build & install

Clone and build the CLI:

```bash
git clone https://github.com/xtanion/patcha.git
cd patcha/rust
cargo build --release
# binary at rust/target/release/patcha
```

Build the Swift helper binaries (Accessibility, OCR, visual embedder, window
observer) that the daemon shells out to:

```bash
bash build.sh
```

`build.sh` compiles the Swift helpers, builds the Rust release binary, and (if
Xcode is present) packages the menu bar app into `dist/Patcha.app`.

Install the git hooks (commit-message linting + test run):

```bash
bash scripts/install-hooks.sh
```

### macOS permissions

The screen/window collectors need **Accessibility** and **Screen Recording**
access. Grant them under **System Settings → Privacy & Security**. The menu bar
app requests these on first run; if you run the CLI directly, add your terminal
(or the `patcha` binary) to both lists.

---

## Getting started

Start the background daemon:

```bash
patcha daemon
```

Run a one-off collection cycle without the daemon:

```bash
patcha collect
```

Search your activity semantically:

```bash
patcha search "sqlite-vec setup"
```

Summarize a day, or review a range:

```bash
patcha summarize
patcha review --from 2026-08-01 --to 2026-08-07
```

Run `patcha --help` or `patcha <command> --help` for full options.

---

## LLM backend

Categorization, summaries, compaction, and chat use an LLM. Patcha picks the
backend automatically:

- **No login (default):** requests run through your local `claude` CLI —
  nothing leaves your machine beyond what the CLI itself sends.
- **Signed in (`patcha login`):** requests route through patcha-api.

Override with the `PATCHA_LLM_BACKEND` environment variable:

| Value    | Behavior                                              |
| -------- | ----------------------------------------------------- |
| `auto`   | (default) use `claude` when there is no token, else API |
| `claude` | always use the local `claude` CLI                     |
| `api`    | always use patcha-api (requires `patcha login`)       |

Collection, embedding, storage, and search never require a login or network.

---

## CLI reference

| Command                | Description                                       |
| ---------------------- | ------------------------------------------------- |
| `daemon`               | Run the background collector + processing loop     |
| `mcp`                  | Run the MCP server (stdio or HTTP)                 |
| `collect`              | Run one collection cycle from all sources          |
| `observe`              | Collect and cluster without calling an LLM         |
| `search <query>`       | Semantic search over activity history              |
| `review`               | Review activity over a date range                  |
| `summarize`            | Generate a written daily summary                   |
| `cluster` / `patterns` | Cluster activity / find recurring patterns         |
| `tasks`                | List identified tasks                              |
| `task-details <id>`    | Show full detail for a task                        |
| `compact-day`          | Manually compact a past date into tasks            |
| `rag-summary`          | RAG-enhanced summary                               |
| `analyze-graph`        | Analyze the activity knowledge graph               |
| `login` / `logout`     | Sign in / out of patcha-api (optional)             |
| `reembed` / `migrate`  | Maintenance: re-embed events / migrate old data    |

Run `patcha --help` for the complete list.

---

## MCP (Claude Desktop & other clients)

The MCP server exposes six read-only tools: `get_working_memory`,
`get_recent_activity`, `search_activity`, `get_activity_context`,
`get_session`, and `find_connected`.

Add to your MCP client config (e.g. `claude_desktop_config.json`):

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

For HTTP transport: `patcha mcp --port 6969`.

See [docs/retrieval/mcp.md](docs/retrieval/mcp.md) for full tool documentation.

---

## Configuration

Patcha reads configuration from environment variables (and, if present,
`~/.patcha/.env` / `.env` in the working directory). Common variables:

| Variable               | Default                     | Description                            |
| ---------------------- | --------------------------- | -------------------------------------- |
| `PATCHA_DB_PATH`       | `~/.patcha/patcha.db`       | SQLite database path                   |
| `DATA_DIR`             | `~/.patcha/data`            | JSONL logs and snapshots               |
| `EMBEDDING_MODEL`      | `BAAI/bge-base-en-v1.5`     | Local fastembed model                  |
| `EMBEDDING_CACHE_DIR`  | `~/.patcha/models`          | Where embedding models are cached      |
| `POLL_INTERVAL`        | `60`                        | Seconds between collection cycles      |
| `ENABLE_*_COLLECTOR`   | `true`                      | Toggle git/browser/terminal/window/AX  |
| `PATCHA_LLM_BACKEND`   | `auto`                      | `auto` / `claude` / `api`              |
| `LOG_LEVEL` / `RUST_LOG` | `info`                    | Logging verbosity                      |

See `rust/patcha/src/config.rs` for the full list of tunables.

---

## Development

```bash
cd rust
cargo build
cargo test          # some collector tests need macOS GUI permissions
cargo clippy --all-targets
cargo fmt --all
```

CI runs `fmt --check`, `clippy -D warnings`, and `build` on macOS. The
`cargo test` suite is run locally via the pre-commit hook because several tests
exercise OCR / Accessibility and need a real GUI session.

### Commit convention

Commits must follow `type: description`, where `type` is one of:

- `feat:` — new feature
- `fix:` — bug fix
- `chore:` — maintenance, deps, tooling

Scope is optional: `feat(cli): add --json flag`. The `commit-msg` hook enforces
this — install hooks with `bash scripts/install-hooks.sh`.

See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

---

## Docs

| Topic                                       | File                                                       |
| ------------------------------------------- | ---------------------------------------------------------- |
| Collectors (browser, terminal, git, screen) | [docs/collectors.md](docs/collectors.md)                   |
| Embedding pipeline                          | [docs/embedding.md](docs/embedding.md)                     |
| Retrieval (working memory, search)          | [docs/retrieval/retrieval.md](docs/retrieval/retrieval.md) |
| MCP server                                  | [docs/retrieval/mcp.md](docs/retrieval/mcp.md)             |
| RAG & knowledge graph                       | [docs/retrieval/rag.md](docs/retrieval/rag.md)             |
| Daily compaction                            | [docs/compaction.md](docs/compaction.md)                   |

---

## License

[MIT](LICENSE) © the Patcha authors.
