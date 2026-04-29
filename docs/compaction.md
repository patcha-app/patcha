# Daily Compaction

`memorai/utils/compaction.py`

Compaction is the nightly process that converts raw activity events from the previous day(s) into structured `Task` objects and removes the raw events from Qdrant.

## Why compaction exists

Raw events are high-frequency and contain a lot of noise (repeated window focus events, browser refreshes, idle terminal sessions). Keeping them indefinitely would make the vector store expensive to query. Compaction:

1. Deduplicates — collapses near-identical events
2. Identifies — groups events into coherent tasks using `TaskIdentifier`
3. Summarizes — stores tasks with richer metadata in `TaskStore`
4. Prunes — deletes the raw events from the main Qdrant collection

## State tracking

Compaction state is stored in `data/daily_compaction.json`:

```json
{
  "compacted_dates": ["2026-04-27", "2026-04-28"],
  "last_trigger_date": "2026-04-29"
}
```

`compacted_dates` prevents re-processing a date. `last_trigger_date` ensures the daemon only runs the compaction check once per calendar day.

## DailyCompactor

### `compact_day(target_date, dry_run=False, force=False)`

Compacts a single past date. Refuses to compact today or future dates.

**Pipeline:**

```
Qdrant (raw events for date)
    └── reconstruct Event objects from vector + payload
            └── dedup by content
                    └── dedup by vector (only if >50 events remain)
                            └── TaskIdentifier.identify_tasks_from_activities
                                    └── TaskStore.store_task (each task)
                                            └── VectorStore.mark_events_compacted
                                                    └── VectorStore.delete_events_by_date
```

With `dry_run=True`, no writes happen — returns the counts without modifying state.

Returns a dict with: `date`, `event_count` (before dedup), `deduped_count` (after dedup), `task_count`, `dry_run`.

### `maybe_compact_previous_days()`

Called by the daemon on startup and periodically. Iterates over the last 7 days, skipping dates already in `compacted_dates`, and calls `compact_day` for each. Updates `last_trigger_date` when done.

## Deduplication

Two passes are applied in sequence:

### Content dedup (`_dedup_by_content`)

Exact-match dedup keyed on the semantically meaningful part of each event:

| Type | Dedup key |
|------|-----------|
| `terminal` | `("terminal", command)` |
| `browser` | `("browser", url)` |
| `window` / `screen` | `(type, app_name, window_title)` |
| others | not deduped by content (kept as-is) |

When a duplicate is found, the later event replaces the earlier one at the same position (keeps the most recent timestamp for the same activity).

### Vector dedup (`_dedup_by_vector`)

Only runs when more than 50 events remain after content dedup — too expensive to run on small sets.

Uses cosine similarity against all previously-kept embeddings. An event is dropped if its similarity to any kept event is ≥ `config.working_memory_dedup_threshold`. This catches semantically-near-duplicate events that content dedup misses (e.g. slightly different terminal commands, different URLs on the same site).

## Configuration

- `config.daily_compaction_min_activities` — minimum number of activities required before `TaskIdentifier` will group them into a task
- `config.working_memory_dedup_threshold` — cosine similarity threshold for both vector dedup and working memory dedup (shared)
