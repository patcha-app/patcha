---
name: memorai
description: Use memorai MCP tools to recall past user activity, recent changes, and work history before falling back to git or filesystem commands.
---

# memorai

memorai tracks the user's device activity — git commits, terminal commands, browser tabs, and editor sessions — and exposes it via three MCP tools.

## When to use memorai tools

Always call `mcp__memorai__search_activity` first when the user asks:
- What changes did I make to X?
- What did I work on recently?
- What was the diff for the X fix/feature/commit?
- When did I last touch X file or feature?
- What have I been doing?

Only fall back to `git log`, `git diff`, or filesystem commands if memorai returns no relevant results.

## Tools

### `mcp__memorai__search_activity`
Semantic search over full activity history. Returns the most relevant past events — terminal commands, git commits with full diffs, browser tabs, editor sessions.
- Use for: specific past work, commit diffs, file changes, feature work
- Always try this before running git commands

### `mcp__memorai__get_working_memory`
Compact summary of the last N minutes of device activity.
- Use for: understanding what the user is currently working on before answering questions or making suggestions

### `mcp__memorai__get_recent_activity`
Deduped log of the last N hours of activity.
- Use for: broader historical context — what the user has been doing over the last few hours
