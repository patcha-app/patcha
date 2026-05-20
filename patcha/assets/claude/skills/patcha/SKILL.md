---
name: patcha
description: Interpret and act on data returned by patcha MCP tools (get_working_memory, search_activity, get_recent_activity, list_apps). Use when processing patcha context to answer user questions or establish session context.
---

## Reading patcha MCP results

Patcha tracks device activity across five event types. Here is how to interpret each.

### Event types

**terminal** — shell commands the user ran
- `raw_content` is JSON: `{"command": "...", "cwd": "..."}` — extract `command`
- High signal for what the user built, ran, tested, or debugged

**browser** — browser tabs and research
- `raw_content` is JSON: `{"title": "...", "url": "...", "domain": "..."}`
- Use to understand what docs, issues, or references the user consulted

**git_commit / git_stash** — version control events
- `raw_content` is JSON with `message`, `files_changed[]`, and `diff`
- The diff is included — use it to understand exactly what changed
- Highest signal for "what did they actually ship?"

**screen / window** — what was on screen (captured via accessibility or OCR)
- `metadata.app_name` = which app was active
- `metadata.gist` = short description of what was visible (if present)
- `metadata.window_title` = window title
- `metadata.transition == "switch"` means the user switched to this app
- Use to understand tool switches and workflow context

**git_staged** — files staged but not yet committed
- Indicates work in progress, not yet a commit

### What to surface, what to ignore

Surface: git commits (especially with diffs), terminal commands that built or ran something meaningful, browser research on a specific topic, app switches that mark a context change.

Ignore: repeated window events for the same app with no content change, blank browser tabs, transition events without substance.

### Grouping into tasks

A task is a coherent unit of work. Group events by time proximity and topic. A git commit or significant app switch usually marks a task boundary. The events before a commit (terminal commands, browser tabs, staged files) belong to that commit's task.

### Presenting results

- Lead with what the user accomplished (commits, completed commands)
- Follow with what they were researching or debugging
- Include app context when it adds meaning (e.g. "in Xcode", "in Arc")
- Use timestamps to establish sequence, not just presence
- One line per task, no preamble, no trailing summary
