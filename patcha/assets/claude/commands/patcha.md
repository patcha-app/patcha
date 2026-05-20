Search patcha activity history and load context for the current task.

If $ARGUMENTS is provided, treat it as a search query:
1. Call `search_activity` with query=$ARGUMENTS and limit=15.
2. If the query references a specific app (e.g. "in Xcode", "in Arc", "in WezTerm"), call `list_apps` first to confirm the exact app name spelling, then re-run `search_activity` with that app passed as the `app` filter.

If $ARGUMENTS is empty:
1. Call `list_apps` to show which apps have recorded activity and their event counts.
2. Call `get_working_memory` with minutes=30 to show what is happening right now.

Use the patcha skill to interpret and present the results.
