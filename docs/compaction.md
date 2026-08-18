# Daily Compaction

`rust/patcha/src/compaction.rs`

Compaction is the nightly process that converts raw activity events from
previous day(s) into structured `Task` objects and prunes the raw events from
the vector store.

## Why compaction exists

Raw events are high-frequency and noisy (repeated window-focus events, browser
refreshes, idle terminal sessions). Keeping them all indefinitely bloats the
store and dilutes search. Compaction:

1. **Deduplicates** near-identical events
2. **Identifies** coherent tasks by clustering the survivors (`TaskIdentifier`)
3. **Summarizes** each task with an LLM and stores it in the task store
4. **Prunes** the raw events for that date from the vector store

## State tracking

Compaction state lives in `DATA_DIR/daily_compaction.json`:

```json
{
  "compacted_dates": ["2026-08-01", "2026-08-02"],
  "last_trigger_date": "2026-08-03"
}
```

`compacted_dates` prevents re-processing a date; `last_trigger_date` ensures the
daemon only runs the sweep once per calendar day.

## DailyCompactor

### `compact_day(target_date, dry_run, force)`

Compacts a single past date. Refuses today or future dates, and skips dates
already in `compacted_dates` (unless `force`).

Pipeline:

```
raw events for date (from VectorStore)
  └── dedup_events
        ├── content dedup (exact key per type)
        └── vector dedup (only when >50 events remain)
              └── TaskIdentifier.identify_tasks_from_activities
                    └── store each Task
                          └── VectorStore.mark_events_compacted
                                └── prune raw events for the date
```

With `dry_run`, no writes happen — it returns counts only. The result JSON
includes `date`, `event_count`, `deduped_count`, `task_count`, and `dry_run`.

### `maybe_compact_previous_days()`

Called by the daemon on startup and periodically. Iterates recent days, skips
those already compacted, compacts the rest, and updates `last_trigger_date`.

## Deduplication

`dedup_events` runs two passes in sequence:

### Content dedup (`dedup_by_content`)

Exact-match dedup keyed on the semantically meaningful part of each event
(e.g. `(terminal, command)`, `(browser, url)`, `(window, app, title)`). When a
duplicate is found, the later event replaces the earlier one, keeping the most
recent timestamp for the same activity.

### Vector dedup (`dedup_by_vector`)

Runs only when more than 50 events remain after content dedup (it's too
expensive for small sets). Each event's normalized embedding is compared by
cosine similarity against previously kept vectors; an event is dropped if it is
≥ `working_memory_dedup_threshold` similar to any kept event. This catches
semantic near-duplicates that exact content dedup misses.

## Task identification

`TaskIdentifier` groups the deduplicated events into tasks:

- Events are split into semantic chunks (by project group, then clustered by
  embedding similarity using an HDBSCAN/DBSCAN-style pass)
- Chunks with at least `daily_compaction_min_activities` events are analyzed in
  parallel by `LLMTaskAnalyzer`, which produces a title, accomplishments, and
  metadata for each task via the configured [LLM backend](../../README.md#llm-backend)
- If the LLM is unavailable, a deterministic `fallback_analysis` still produces a
  usable task from the raw activity

## Configuration

- `DAILY_COMPACTION_MIN_ACTIVITIES` — minimum events before a chunk becomes a task
- `WORKING_MEMORY_DEDUP_THRESHOLD` — cosine threshold shared by vector dedup and
  working-memory dedup
