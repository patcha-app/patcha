Fetch the user's recent activity from memorai and produce a completed-task digest.

Steps:
1. Call `get_working_memory` with `minutes: 60` to get the last hour of activity.
2. Call `get_recent_activity` with `hours: 8` for broader context across the session.
3. Analyze both results together. Group raw events into distinct completed tasks — a "task" is a coherent unit of work the user finished (e.g. "fixed auth bug in api/auth.py", "wrote and ran migration 0042", "researched qdrant embedding options").
4. Ignore noise: window switches, scrolling, idle time, repeated opens of the same file.
5. Output a concise bullet list, one line per completed task, in chronological order. Each line: what was done and (if obvious) what file/tool/repo it involved. No headers, no preamble, no trailing summary.

If $ARGUMENTS is provided, treat it as a time range override (e.g. "3h", "today") and adjust the `minutes`/`hours` parameters accordingly.
