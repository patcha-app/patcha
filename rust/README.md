# patcha (Rust)

Single binary replacing the Python daemon + MCP server + CLI.

## Build

```bash
cargo build --release
```

## Daemon

```bash
# Start (foreground)
./target/release/patcha daemon start

# Start (background)
./target/release/patcha daemon start &

# Start with logging to file
RUST_LOG=info ./target/release/patcha daemon start 2>> ~/.patcha/data/daemon.log &

# Check status
./target/release/patcha daemon status

# Stop
./target/release/patcha daemon stop
```

`RUST_LOG` levels: `error`, `warn`, `info` (default), `debug`.

## MCP server

```bash
# stdio (default — for Claude Desktop / MCP clients)
./target/release/patcha mcp

# HTTP on port 6969
./target/release/patcha mcp --port 6969
```

## CLI

```bash
./target/release/patcha collect              # collect events from all sources
./target/release/patcha search "query"       # semantic search
./target/release/patcha summarize            # daily summary
./target/release/patcha compact-day          # compact yesterday into tasks
./target/release/patcha tasks                # list tasks
./target/release/patcha patterns             # activity patterns
./target/release/patcha migrate              # migrate data from Qdrant to sqlite-vec
./target/release/patcha --help               # full command list
```
