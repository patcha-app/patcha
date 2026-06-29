# Screen Capture & Activity Graph Architecture Plan

`patcha/collectors/accessibility.py` (refactor) + new `patcha/perception/`, `patcha/triggers/`, and `patcha/graph/` modules

This document specifies upgrades to the screen-capture pipeline and the addition of a relational activity graph alongside the existing vector store. The plan shifts screen capture from a text-only signal with brittle diffing to a layered visual + text pipeline with semantic gist extraction, and adds a graph layer that captures the structural relationships between events that vector similarity alone cannot express. All phases ship independently.

---

## Goals

- **Gist** — capture what the user is *doing* on screen, not just the literal text visible
- **Reliable diff** — replace the 80% text-similarity heuristic with semantic change detection
- **Event-driven** — capture context switches instantly via macOS event observers rather than discovering them up to 5 seconds late via polling
- **Relational** — capture structural relationships between events (temporal chains, app→file→project links) in a graph that supports queries vector similarity cannot answer
- **Local-first** — all new components run on-device; no new cloud dependencies
- **Apple Silicon native** — leverage MLX/CoreML for fast on-device inference
- **Backward compatible** — existing event schema and downstream consumers (`TaskIdentifier`, `VectorStore`, `DailyCompactor`) keep working through the transition

---

## What stays as-is

The current pipeline has several pieces doing exactly the right job. They don't change.

### Collectors

- `BrowserCollector`, `TerminalCollector`, `GitCollector` — unrelated to screen, untouched
- `WindowCollector` — the data it provides (active `app_name` + `window_title`) remains a primary input to the screen-capture flow and is fed to the captioner as context. The delivery mechanism changes from polled AppleScript to event-driven Swift observers (see [Trigger system](#trigger-system) below), but the output format is unchanged

### Within `AccessibilityCollector`

- `ax_content.swift` AX API extractor — keeps its role as the cheapest text signal
- `ocr.swift` Vision OCR — stays as the fallback when AX returns `ocr_needed`
- Active-frame detection (focused field / mouse position) — used for both AX scope *and* visual processing
- `screencapture -R x,y,w,h` cropping with 50px padding — already well-scoped
- `screen_log.jsonl` rotation logic (1,000-write trim, 100k line cap) — fine as-is
- System app skip-list (Finder, Dock, System Preferences, etc.)

### Storage and downstream

- Event schema's existing fields (`type`, `raw_content`, `timestamp`, `metadata`, `embedding`)
- `EventPreprocessor.chunk_text` — multi-chunk events keep working unchanged
- `DailyCompactor` overall pipeline (reconstruct → dedup → identify → store → prune)
- `compacted_dates` state tracking in `daily_compaction.json`
- Content-key dedup (`(type, app_name, window_title)` for screen events)

---

## What changes

### 1. Diff logic in `AccessibilityCollector`

**Before:** if new text is >80% different from last capture, store full text; else store diff. Fixed 5-second polling interval drives all captures.

**After:** a three-tier decision cascade replaces both the text-diff heuristic and the fixed poll loop:

1. **Title check (free).** If `app_name` or `window_title` changed from the previous tick → immediate context switch, skip to full capture. No embedding needed to decide.

2. **Per-app cache check (~10 ms).** If the title didn't change, compare the current frame's visual embedding against the **cached embedding for this app** (not just the last frame). This means returning to an app you left an hour ago short-circuits if the app hasn't changed visually. See [Per-app embedding cache](#per-app-embedding-cache) below.

3. **Cosine similarity threshold.** Applied to the cache comparison:

| Cosine similarity vs cache | Action |
|----------------------------|--------|
| ≥ 0.97 | Drop — no meaningful change, no event written |
| 0.85 – 0.97 | Run AX/OCR text path; store `text` + `visual_embedding`; update cache |
| < 0.85 | Within-window context switch — AX/OCR + VLM caption; update cache |

The 80% text-diff branch is removed entirely. Title check catches app/tab switches; visual embedding catches within-window content changes; the per-app cache eliminates redundant captures when switching between unchanged apps.

### 2. Embedding strategy in `EventPreprocessor`

For screen events specifically, `_build_embedding_text` gets one extra input source: the FastVLM gist. The build order becomes:

```
gist (if present) | app_name | window_title | ax_text_summary
```

This keeps text embeddings useful even when AX text is sparse (image-heavy screens) and lets retrieval find events by what was happening rather than just by literal on-screen text.

The visual embedding (from MobileCLIP2) is stored as a **second vector** on the event, not a replacement for the text embedding. Qdrant supports multi-vector collections via named vectors — see the migration note below.

### 3. Vector dedup in `DailyCompactor`

`_dedup_by_vector` currently checks cosine similarity of text embeddings only. For screen events, also check visual-embedding similarity using the same threshold. An event is dropped if *either* axis exceeds the threshold against any kept event.

This catches the dashboard-refresh case (text changes, visuals don't) and the scrolling case (visuals shift slightly, content doesn't change much) — both of which slip through content dedup today.

### 4. Storage schema

The Event payload and Qdrant point structure both expand. An activity graph is added alongside the vector store. Full details in [Vector store schema](#vector-store-schema) and [Activity graph](#activity-graph) below.

---

## What's new

A new `patcha/perception/` module owns the visual pipeline, a new `patcha/triggers/` module owns the event-driven capture system, and a new `patcha/graph/` module owns the activity graph.

```
patcha/perception/
├── __init__.py
├── helper.py          # Long-lived process manager (lifecycle, IPC, restart)
├── helper_server.py   # Python child process that loads models once
├── embedder.py        # MobileCLIP2 client (small wrapper around helper RPC)
├── captioner.py       # FastVLM client (small wrapper around helper RPC)
├── app_cache.py       # Per-app embedding cache
└── models/            # Downloaded weights (gitignored)

patcha/triggers/
├── __init__.py
├── observer.swift     # Long-lived Swift helper: NS/AX event subscriptions
├── coordinator.py     # CaptureCoordinator: debounce + depth routing
└── idle_detector.py   # CGEventTap-based idle/active tracking

patcha/graph/
├── __init__.py
├── store.py           # GraphStore: FalkorDB Lite wrapper, node/edge upserts
├── schema.py          # Node and edge type definitions, Cypher templates
├── linker.py          # LLM-assisted entity linking (Phase B)
└── query.py           # Hybrid retrieval: graph traversal + Qdrant vector merge
```

### Why a long-lived helper

Both MobileCLIP2 and FastVLM have non-trivial load times (5–10 seconds for FastVLM-0.5B with weights mmap'd). Invoking a fresh process per poll tick is unworkable. The pattern mirrors how `ax_content.swift` and `ocr.swift` are compiled once and called many times — except for VLMs the heavy state is *loaded weights*, not just compiled code.

`helper_server.py` runs as a child process of the patcha daemon, exposes a Unix socket, and stays resident. The daemon talks to it via JSON-RPC for `embed(image_path) → vector` and `caption(image_path, context) → str`.

### MobileCLIP2 — the embedder

Apple's companion model to FastVLM, designed as an efficient image-text encoder. Used here purely for image-only embeddings. ~50 MB weights, ~10 ms per frame on M-series silicon.

Loaded once at helper startup. Called when the title-check tier doesn't already resolve the capture decision — i.e., when the app/window title hasn't changed and we need to determine if the visual content shifted.

### FastVLM-0.5B — the captioner

Apple's open-source on-device VLM (`apple/ml-fastvlm` on GitHub). Runs via MLX. Called only on context switches (title change, cosine sim < 0.85, or idle→active resume), or when AX returned `ocr_needed` and the visual signal indicates a visually rich screen.

Prompt template:

```
You are observing a user's screen. The active app is {app_name} and the
window title is {window_title}. In one sentence, describe what the user
is doing.
```

Output: a 10–30 token caption stored as `gist`.

Expected latency: 200–600 ms TTFT on M2/M3, fast enough that even at frequent trigger rates only a fraction of captures invoke it.

### Trigger system

The fixed 5-second poll is replaced by a hybrid of event-driven instant triggers and a relaxed background poll. This is owned by `patcha/triggers/`.

#### Event observer (`observer.swift`)

A long-lived Swift process (similar to the existing `ax_content.swift` pattern but persistent) that subscribes to macOS notifications and writes trigger events to a Unix socket. The Python daemon reads this socket via `coordinator.py`.

Events subscribed:

| Event source | macOS API | Fires when |
|-------------|-----------|------------|
| App activation | `NSWorkspaceDidActivateApplicationNotification` | User switches to a different app |
| Window title change | `kAXTitleChangedNotification` via AXObserver | Focused window title updates (new tab, new file, page navigation) |
| Space/desktop switch | `NSWorkspace.activeSpaceDidChangeNotification` | User switches macOS spaces or full-screen apps |
| Screen lock | `NSWorkspace.willScreenLockNotification` | Session end — pause all capture |
| Screen unlock | `NSWorkspace.didScreenUnlockNotification` | Session start — full capture of whatever is on screen |

Each trigger event is a JSON message: `{"type": "app_switch", "app_name": "...", "window_title": "...", "timestamp": "..."}`.

#### Idle detector (`idle_detector.py`)

Monitors `CGEventTap` for keyboard and mouse activity. Tracks only *that* input occurred, not *what* was typed — no keystroke logging.

- After `config.idle_timeout` seconds (default: 120) of no input → mark user idle, pause the background poll timer
- On first input after idle → fire an `idle_resume` trigger, resume poll timer

This eliminates the ~720 wasted captures per hour of idle time that the current fixed poll generates.

#### CaptureCoordinator (`coordinator.py`)

Sits between all trigger sources and the actual capture pipeline. Two responsibilities:

**Debouncing.** When the user alt-tabs through 5 apps in 2 seconds, the observer fires 5 `app_switch` events. The coordinator holds a short window (`config.debounce_ms`, default: 500 ms) after the first trigger before firing the capture. If another trigger arrives within the window, reset the timer. Only the final destination gets captured.

**Capture depth routing.** Not every trigger needs the full pipeline:

| Trigger | Capture depth | Rationale |
|---------|--------------|-----------|
| `app_switch` | Full (AX + VLM + embed) | Major context boundary |
| `title_change` | Medium (embed + check cache → escalate if needed) | Might be minor (title refresh) or major (new tab) |
| `space_switch` | Full (AX + VLM + embed) | Usually a major context shift |
| `idle_resume` | Full (AX + VLM + embed) | User returned — capture what they see |
| `screen_unlock` | Full (AX + VLM + embed) | New session start |
| `screen_lock` | None — pause everything | Session end |
| `poll_tick` | Light (embed comparison via cache → escalate if needed) | Within-window drift detection only |

The background poll interval relaxes from 5 seconds to `config.poll_interval` (default: 15 seconds) since it only handles within-window content drift — all context switches are caught instantly by event triggers.

### Per-app embedding cache

An in-memory cache that stores the last-stored visual embedding per app, enabling fast short-circuiting when the user returns to an unchanged app.

```python
app_state_cache = {
    "Visual Studio Code": {
        "visual_embedding": [...],    # 512-dim MobileCLIP2 vector
        "window_title": "accessibility.py — patcha",
        "timestamp": "2026-04-29T10:45:00Z",
        "event_id": "screen::1746523500000::a3f8"
    },
    "Arc": {
        "visual_embedding": [...],
        "window_title": "GitHub — patcha PR #42",
        "timestamp": "2026-04-29T09:30:00Z",
        "event_id": "screen::1746519000000::b7c2"
    }
}
```

**Update rule:** every time an event is *stored* for an app, update that app's cache entry. Dropped frames (cosine ≥ 0.97) do NOT update the cache — otherwise you'd slowly drift and never notice gradual changes.

**TTL.** If `now - cached_timestamp > config.cache_ttl` (default: 2 hours), treat the app as unseen and do a full capture regardless. Apps change while you're away — a browser tab may have refreshed, a build may have finished, Slack has new messages.

**Size.** Most users actively use 5–15 apps. One 512-dim float32 vector per app = ~30 KB total. No eviction policy needed; clear on daemon restart.

**Cache key.** Keyed on `app_name`. When `window_title` changes within the same app, the cache entry is invalidated (the title-check tier catches this and routes to full capture, which then updates the cache with the new state).

### (Optional, Phase 4) Foundation Models framework integration

Once on macOS 26+, replace `text-embedding-3-small` for screen events with on-device structured extraction via the Foundation Models framework. The framework is text-only as of 26.3 but supports guided generation, so we can extract `{ activity, project, intent }` directly from AX text + gist with no API cost.

This is optional and gated on macOS version detection.

### Activity graph

A graph layer sits alongside Qdrant, capturing the structural relationships between events that vector similarity alone cannot express.

#### Why not Microsoft GraphRAG

Microsoft's GraphRAG pipeline is designed for static document corpora — it batch-indexes text with expensive LLM extraction, applies community detection, and generates summaries. Patcha's data is fundamentally different: it's semi-structured (events with typed metadata), arrives incrementally, and has relationships that are mostly explicit in the metadata. The batch LLM indexing cost alone (~minutes per run) is incompatible with a real-time background daemon. Instead, we build the graph incrementally as events arrive, extracting ~90% of edges directly from metadata at zero LLM cost.

#### Graph database: FalkorDB Lite

FalkorDB Lite is an embedded variant of FalkorDB that runs as a subprocess with file-based storage. No server, no Docker, no configuration — just a file path. It supports Cypher queries and has an async Python API. This matches patcha's local-first philosophy exactly.

```python
from falkordblite import FalkorDBLite

db = FalkorDBLite(path="~/Library/Application Support/patcha/activity.db")
graph = db.select_graph("activity")
```

Alternative: KùzuDB — a newer embedded graph database, very fast for analytical queries, also supports Cypher. Either works; FalkorDB Lite has tighter GraphRAG ecosystem integration if you add LLM-assisted linking later.

#### Node types

Created and updated incrementally as events arrive:

| Node label | Created from | Key properties |
|-----------|-------------|----------------|
| `:App` | Any event with `app_name` | `name` |
| `:Window` | Screen/window events | `title`, `app_name` |
| `:File` | Git commits, terminal events | `path`, `project` |
| `:Project` | Git collector, event metadata | `name`, `root_path` |
| `:URL` | Browser events | `url`, `domain` |
| `:Session` | Inferred from idle gaps / lock-unlock | `started_at`, `ended_at` |
| `:Task` | Compaction output | `title`, `summary`, `started_at`, `ended_at` |
| `:ScreenEvent` | Screen captures | `gist`, `timestamp`, `trigger` |
| `:TerminalEvent` | Terminal collector | `command`, `timestamp` |
| `:GitCommit` | Git collector | `sha`, `message`, `timestamp` |
| `:BrowserEvent` | Browser collector | `title`, `url`, `timestamp` |

All nodes carry a `created_at` and `updated_at` timestamp for temporal queries.

#### Edge types

Split into two categories based on extraction cost:

**Explicit edges (zero LLM cost, written at capture time):**

| Edge | From → To | Source |
|------|-----------|--------|
| `ON_APP` | Event → App | `app_name` field on screen/window events |
| `IN_WINDOW` | ScreenEvent → Window | `window_title` field |
| `DURING` | Event → Session | Inferred from timestamp + session boundaries |
| `FOLLOWED_BY` | Event → Event | Temporal ordering within a session |
| `SWITCHED_FROM` | Event → Event | Trigger type = `app_switch` or `space_switch` |
| `MODIFIED` | GitCommit → File | `files_changed` from git collector |
| `IN_PROJECT` | GitCommit/File → Project | Git repo → project mapping |
| `VISITED` | BrowserEvent → URL | `url` field |
| `CONTAINS` | Task → Event | Written during compaction |
| `IN_SESSION` | Task → Session | Overlapping time range |
| `BELONGS_TO` | Window → App | `app_name` on window |

**LLM-inferred edges (Phase B, requires local LLM):**

| Edge | From → To | How inferred |
|------|-----------|--------------|
| `RELATES_TO` | ScreenEvent → File | Gist mentions a filename visible on screen |
| `RELATES_TO` | BrowserEvent → Project | URL is docs/issues for a known project |
| `REVIEWS` | ScreenEvent → GitCommit | Gist indicates PR review, matched to commit |
| `REFERENCES` | Event → URL/File | Entity extraction from gist or AX text |

These require a local LLM (Gemma 4 E2B or Qwen3.5-4B — reuse the captioner if using Gemma 4) to extract entity mentions from gists and link them to existing graph nodes. Run during compaction alongside `TaskIdentifier`, not at capture time.

#### How graph writes integrate with the event pipeline

Graph upserts happen in the same code path as Qdrant writes, adding ~5 ms per event:

```
event stored in Qdrant
  └── graph.store.upsert_event(event)
        ├── MERGE (:App {name: event.app_name})
        ├── MERGE (:Window {title: event.window_title, app: event.app_name})
        ├── CREATE (event_node)-[:ON_APP]->(app)
        ├── CREATE (event_node)-[:IN_WINDOW]->(window)
        ├── CREATE (event_node)-[:DURING]->(current_session)
        └── if prev_event: CREATE (prev_event)-[:FOLLOWED_BY]->(event_node)
```

`MERGE` is idempotent — the same app/window node is reused across events. The graph grows incrementally without batch reindexing.

#### Hybrid retrieval at query time

The graph and Qdrant serve different query shapes:

| Query type | Store | Example |
|-----------|-------|---------|
| "Find events similar to X" | Qdrant (vector search) | "When was I looking at a similar dashboard?" |
| "What happened before/after X?" | Graph (temporal traversal) | "What was I doing right before I opened Slack?" |
| "Everything connected to Y" | Graph (multi-hop) | "Show me all activity related to the auth refactor" |
| "Similar events in the same session" | Both (hybrid) | "Find debugging events from the same session as this PR review" |

Hybrid queries first retrieve candidate nodes from Qdrant by vector similarity, then expand context via graph traversal (e.g., "also show me what happened 5 minutes before and after each match"), then rank the merged results. `graph/query.py` owns this merge logic.

#### Graph-assisted compaction

During compaction, graph topology assists `TaskIdentifier`:

- Events that are temporally adjacent (`FOLLOWED_BY` chains) AND share `App`/`File`/`Project` nodes are likely the same task — this is a graph pattern match, not an LLM call
- The LLM-based `TaskIdentifier` still runs, but the graph pre-groups candidates so the LLM validates clusters rather than discovering them from scratch
- Task boundaries align with `SWITCHED_FROM` edges, which are natural break points

This reduces the LLM's job from "cluster these 187 events into tasks" to "confirm or split these 5 pre-clustered groups."

---

## Vector store schema

This section formalizes what gets stored in Qdrant and how the new fields interact with the existing event/task models. The store splits cleanly into two collections: **raw events** (live, frequently written, pruned by compaction) and **tasks** (long-lived, written by `TaskStore`).

### Raw events collection

Each event is a Qdrant point with one or two named vectors and a flat payload.

#### Vectors

| Vector | Dims | Source | Used for |
|--------|------|--------|----------|
| `text` | 1536 | `text-embedding-3-small` over `gist \| app \| window \| ax_text` for screen; existing logic for other types | Semantic retrieval, content dedup |
| `visual` | 512 | MobileCLIP2 over the captured frame | Visual retrieval, change detection, visual dedup |

Both use cosine distance. The `visual` vector is populated only for screen events; other event types (terminal, browser, git, window) leave it null and are searchable only via the `text` vector. Qdrant handles this through named vectors — points without a `visual` value are simply excluded from `visual`-vector queries rather than throwing errors.

Two different dimensions (1536 vs 512) is fine. Each named vector has its own HNSW index sized for its dim.

#### Payload (screen event example)

```json
{
  "type": "screen",
  "timestamp": "2026-04-29T10:23:45.123Z",
  "project": "patcha",

  "gist": "Debugging a Python KeyError in accessibility.py with the terminal pane open showing a stack trace",
  "app_name": "Visual Studio Code",
  "window_title": "accessibility.py — patcha",

  "raw_text_snippet": "def collect_all(since):\n    events = []\n    for browser in...",
  "raw_text_source": "ax",
  "raw_text_truncated": false,

  "transition": "switch",
  "trigger": "app_switch",
  "compacted": false,

  "chunk_index": 0,
  "total_chunks": 1,
  "source_doc_id": "screen::1746523425123::a3f8b2c1",

  "_meta": {
    "schema_version": 2,
    "text_embedder": "text-embedding-3-small",
    "visual_embedder": "mobileclip2-s2-1.0",
    "captioner": "fastvlm-0.5b",
    "frame_region": [120, 80, 1200, 800]
  }
}
```

#### Field map by event type

Which top-level payload fields are populated for each `type`:

| Field | screen | browser | terminal | git_commit | git_stash | window |
|-------|:------:|:-------:|:--------:|:----------:|:---------:|:------:|
| `gist` | ✓ | | | | | |
| `app_name` | ✓ | | | | | ✓ |
| `window_title` | ✓ | | | | | ✓ |
| `url`, `domain` | | ✓ | | | | |
| `command` | | | ✓ | | | |
| `commit_sha`, `files_changed` | | | | ✓ | ✓ | |
| `transition` | ✓ | | | | | |
| `trigger` | ✓ | | | | | |
| `_meta.visual_embedder` | ✓ | | | | | |
| `_meta.captioner` | ✓ (on switch) | | | | | |

Every event has `type`, `timestamp`, `project`, `raw_text_snippet`, `raw_text_source`, `compacted`, `chunk_*`, `source_doc_id`, and `_meta.schema_version` + `_meta.text_embedder`.

#### What's intentionally not in the payload

- **Full raw AX/OCR text.** It's already represented in the `text` vector. Storing only a 200-char snippet for display saves substantial space — at ~14k events/day post-prefilter the difference is roughly 30 MB/day. The full text remains available in `data/screen_log.jsonl` for the small number of cases that need it.
- **Frame perceptual hashes.** The visual embedding subsumes them. Cosine similarity is more robust than hash distance for change detection anyway.
- **Pointer chains (`prev_event_id`).** Reconstructable from timestamp ordering within an `app_name` + `window_title` session.
- **Full screenshots.** Frames are processed and discarded; only the embedding survives. This is also a privacy property worth preserving.

#### Filter indices

Qdrant payload indices to create on the events collection:

- `type` (keyword)
- `timestamp` (datetime, range queries)
- `project` (keyword)
- `app_name` (keyword)
- `transition` (keyword)
- `trigger` (keyword — `app_switch`, `title_change`, `idle_resume`, `poll_tick`, etc.)
- `compacted` (bool — for excluding already-compacted events from live queries)

Without these, payload filters fall back to a full scan — fine at small sizes, painful at the daily volume this collector produces.

#### ID strategy

The existing `{source_doc_id}::chunk::{i}` pattern extends naturally:

- **Screen events:** `screen::{timestamp_ms}::{frame_region_hash[:8]}` — deterministic, dedupable on retry
- **Other types:** keep the current scheme

Determinism here matters because `VectorStore.store_event` deterministically derives Qdrant point UUIDs from these IDs. Same input → same UUID → upsert rather than duplicate. Retries after a daemon crash don't double-write.

### Tasks collection

Compaction reads raw events and writes tasks to a separate collection. Tasks have a different shape because they represent aggregated activity over a window, not a moment.

#### Vectors

| Vector | Dims | Source | Used for |
|--------|------|--------|----------|
| `text` | 1536 | Embedding of `title` + `summary` | Task-level semantic retrieval |
| `visual` | 512 | Mean-pooled MobileCLIP2 vectors of the screen events in the task | Visual task retrieval ("find tasks that look like this one") |

The visual centroid loses information vs. retaining all source vectors, but it's the right tradeoff: tasks should be one point per task, not N. If you need finer-grained visual retrieval, query the raw events collection during the retention window before they're pruned.

#### Payload

```json
{
  "task_id": "uuid",
  "title": "Debugging accessibility collector OCR fallback",
  "summary": "Investigated why ax_content.swift was returning ocr_needed for VSCode windows. Traced to focused-frame detection failing on split-pane layouts. Patched the AX query to walk up the focus hierarchy.",

  "started_at": "2026-04-29T10:00:00Z",
  "ended_at": "2026-04-29T11:45:00Z",
  "duration_seconds": 6300,
  "date": "2026-04-29",

  "primary_app": "Visual Studio Code",
  "apps_involved": ["Visual Studio Code", "Terminal", "Arc"],
  "project": "patcha",

  "event_types": ["screen", "terminal", "git_commit"],
  "source_event_count": 412,
  "deduped_event_count": 187,

  "key_artifacts": {
    "files_touched": ["patcha/collectors/accessibility.py"],
    "urls_visited": ["https://developer.apple.com/documentation/accessibility"],
    "commits": [
      { "sha": "abc1234", "message": "Fix focused-frame detection for split panes" }
    ]
  },

  "_meta": {
    "schema_version": 1,
    "compacted_at": "2026-04-30T03:00:00Z",
    "compactor_version": "1.2.0",
    "visual_centroid_method": "mean"
  }
}
```

Filter indices for tasks: `date`, `started_at`, `project`, `primary_app`, `event_types` (Qdrant supports any-of matching on array fields).

### Schema versioning

Both collections carry `_meta.schema_version`. When the schema changes — a new field, a model upgrade, a different embedder — increment the version and either backfill old points (if the new value is computable from existing data) or have query code handle both versions gracefully.

This is preferable to silent breaking changes. The MobileCLIP2 case is a good example: if the embedder version changes, the visual vectors are no longer comparable across versions, so dedup and retrieval should scope to a single version.

### Storage estimates

Rough sizing for an 8-hour workday with event-driven triggers, per-app cache, and 15-second background poll:

- Event-driven captures (app switches, title changes, idle resumes): ~200–400/day
- Poll-tick captures that survive the pre-filter + cache: ~500–1,000/day
- Total written events: ~700–1,400/day (down from ~14,000 with 1s polling, or ~5,760 with 5s polling)
- Per-event storage: ~6 KB vectors (1536 + 512 floats × 4 bytes) + ~1 KB payload = ~7 KB
- Daily raw events: ~5–10 MB (down from ~100 MB)
- Tasks per day after compaction: typically 5–20 at ~3 KB each → negligible
- 30-day retention: well under 500 MB raw + cumulative tasks
- Graph storage: ~500 KB/day for nodes + edges, ~150 MB/year (not pruned by compaction)

The dominant cost is now the visual vector. If storage becomes an issue, MobileCLIP2 has 256-dim variants that halve the per-event vector cost with minor recall tradeoffs. Quantizing vectors to int8 in Qdrant is another lever (~4× reduction with measurable but usually acceptable recall loss).

---

## Pipeline flow

```
Trigger sources
  ├── observer.swift ──→ app_switch, title_change, space_switch, lock/unlock
  ├── idle_detector.py → idle_resume
  └── poll timer (15s) → poll_tick (only while user is active)
        │
        └── CaptureCoordinator (debounce 500ms)
              │
              └── Capture decision cascade
                    │
                    ├── screen_lock → pause everything, return
                    │
                    ├── app_switch / space_switch / idle_resume / screen_unlock
                    │     └── FULL CAPTURE (skip comparison, this is a known switch)
                    │           ├── screencapture → frame.png
                    │           ├── embed frame → visual_embedding (for storage)
                    │           ├── AX/OCR → text
                    │           ├── captioner → gist
                    │           ├── update app_state_cache[app_name]
                    │           ├── store event in Qdrant {text, gist, visual_embedding, trigger}
                    │           └── upsert graph nodes + edges (App, Window, Session, FOLLOWED_BY)
                    │
                    ├── title_change
                    │     └── MEDIUM CAPTURE (title changed within same app)
                    │           ├── screencapture → frame.png
                    │           ├── embed frame → visual_embedding
                    │           ├── cosine_sim vs app_state_cache[app_name]
                    │           │     ├── ≥ 0.97 → drop (title flicker, no real change)
                    │           │     └── < 0.97 → AX/OCR + VLM caption if < 0.85
                    │           ├── update cache
                    │           └── store event in Qdrant + upsert graph
                    │
                    └── poll_tick
                          └── LIGHT CAPTURE (within-window drift detection)
                                ├── is app in cache AND cache not stale (< TTL)?
                                │     ├── NO → treat as new app, full capture
                                │     └── YES → screencapture → embed → cosine_sim vs cache
                                │           ├── ≥ 0.97 → drop, no event
                                │           ├── 0.85–0.97 → AX path, update cache
                                │           └── < 0.85 → within-window switch, AX + VLM, update cache
                                └── store event in Qdrant + upsert graph (if not dropped)
```

State carried between ticks: `app_state_cache` (per-app embeddings + metadata), `idle_state` (active/idle + last input timestamp), `debounce_timer`.

---

## Implementation phases

### Phase 1 — Visual pre-filter + per-app cache

**Scope:** add MobileCLIP2 helper, replace text-diff in `AccessibilityCollector` with cosine-sim diff against a per-app embedding cache, store visual embedding on screen events. Keep the existing 5-second poll for now.

**New files:**
- `patcha/perception/helper.py`
- `patcha/perception/helper_server.py`
- `patcha/perception/embedder.py`
- `patcha/perception/app_cache.py`

**Modified files:**
- `patcha/collectors/accessibility.py` — diff logic (title check → cache check → visual embedding)
- `patcha/process.py` — pass through `visual_embedding` to vector store
- Qdrant collection — add named vector `visual`

**Effort:** small–medium. ~2–3 days.

**Validation:** before/after comparison of screen events generated over a 4-hour work session. Expect 40–70% reduction in event count from elimination of scrolling false positives + app-return deduplication.

### Phase 2 — Event-driven triggers

**Scope:** replace the fixed 5-second poll with event-driven triggers + a relaxed background poll. Add `CaptureCoordinator` for debouncing and capture-depth routing. Add idle detection.

**New files:**
- `patcha/triggers/observer.swift`
- `patcha/triggers/coordinator.py`
- `patcha/triggers/idle_detector.py`

**Modified files:**
- `patcha/collectors/accessibility.py` — receive triggers from coordinator instead of timer
- Daemon startup — launch the Swift observer alongside the perception helper

**Effort:** medium. ~3–4 days, mostly in the Swift observer and coordinator plumbing.

**Validation:** instrument trigger counts and capture depths over a full workday. Expect: context switches captured within <500 ms (vs up to 5s before), ~60% fewer total capture operations, zero captures during idle periods.

### Phase 3 — Gist captioning

**Scope:** add FastVLM (or Moondream2) to the helper, invoke on context switches and `idle_resume`, store gist on events, incorporate gist into `_build_embedding_text`.

**New files:**
- `patcha/perception/captioner.py`

**Modified files:**
- `patcha/perception/helper_server.py` — load captioner at startup
- `patcha/triggers/coordinator.py` — full-capture triggers invoke captioner
- `patcha/process.py` — `_build_embedding_text` includes gist for screen events
- `patcha/utils/compaction.py` — `_dedup_by_vector` checks visual axis too

**Effort:** medium. ~3–5 days, mostly in captioner integration and prompt tuning.

**Validation:** sample 50 random screen events from a typical day. For each, judge whether the gist accurately describes what was happening. Target: ≥80% useful gists.

### Phase 4 (optional) — Foundation Models text structuring

**Scope:** macOS 26+ only. Replace `text-embedding-3-small` for screen events with on-device structured extraction via Foundation Models.

**Effort:** medium. Adds a Swift helper binary similar to the existing AX/OCR ones.

**Gating:** ship behind a config flag, fall back to OpenAI on older macOS.

### Parallel workstream: Activity graph

These phases are independent of the perception phases above and can be developed in parallel. Phase A can start alongside Phase 1.

### Phase A — Explicit graph edges

**Scope:** add FalkorDB Lite, write graph nodes and edges from existing event metadata as events arrive. Zero LLM cost. This alone unlocks temporal queries, structural browsing, and graph-assisted compaction.

**New files:**
- `patcha/graph/store.py`
- `patcha/graph/schema.py`

**Modified files:**
- `patcha/collectors/accessibility.py` — call `graph.store.upsert_event()` after Qdrant write
- `patcha/collectors/browser.py` — write URL + BrowserEvent nodes
- `patcha/collectors/git.py` — write File + GitCommit + Project nodes
- `patcha/collectors/terminal.py` — write TerminalEvent nodes
- `patcha/utils/compaction.py` — use graph topology to pre-group events before `TaskIdentifier`; write Task→Event `CONTAINS` edges

**Effort:** medium. ~3–5 days. Most time spent on the `store.py` upsert logic and Cypher templates.

**Validation:** after a full workday, run sample graph queries: "What apps did I use in the same session as commit X?", "What was I doing before I opened Slack?", "Show all events touching accessibility.py." All should return sensible results from explicit edges alone.

### Phase B — LLM-inferred edges

**Scope:** add entity extraction from gists and AX text during compaction. Link screen events to specific files, URLs, PRs using a local LLM. Reuse the captioner model if using Gemma 4 E2B (which supports structured output), or load Qwen3.5-4B.

**New files:**
- `patcha/graph/linker.py`

**Modified files:**
- `patcha/utils/compaction.py` — call `linker.extract_and_link()` after task identification
- `patcha/perception/helper_server.py` — expose an `extract_entities(text) → [entities]` RPC if reusing the captioner model

**Effort:** medium. ~3–5 days, mostly prompt engineering for entity extraction and dedup logic for matching extracted entities to existing graph nodes.

**Validation:** sample 50 screen events with gists. For each, check whether the inferred edges (RELATES_TO file, REVIEWS commit, etc.) are accurate. Target: ≥75% precision — false edges are worse than missing edges.

### Phase C — Hybrid retrieval

**Scope:** add a query layer that combines Qdrant vector search with graph traversal. Query pattern: vector search → graph expansion → merged ranking.

**New files:**
- `patcha/graph/query.py`

**Modified files:**
- Query interface (API or CLI) — route queries through `query.py` instead of direct Qdrant calls

**Effort:** medium. ~3–4 days, mostly in the merge/ranking logic.

**Validation:** compare retrieval quality on 20 test queries against Qdrant-only retrieval. Hybrid should surface more relevant context for structural queries ("what was I doing before X") while matching Qdrant for semantic queries.

---

## Improvements brought

| Improvement | Phase | What it fixes |
|-------------|-------|---------------|
| Scrolling no longer creates events | 1 | Storage waste; noisy task identification |
| Tab switches caught even with similar text | 1 | Missed context changes |
| Visual retrieval ("when was I looking at a graph") | 1 | Text-only retrieval misses visual content |
| Compaction catches dashboard-refresh duplicates | 1 | Storage waste post-compaction |
| Returning to unchanged app skips re-capture | 1 | Redundant captures when alt-tabbing between apps |
| Context switches captured instantly (<500 ms) | 2 | Up to 5s delay with fixed polling |
| Zero captures during idle periods | 2 | ~720 wasted captures per hour of idle time |
| ~60% fewer total capture operations | 2 | CPU/battery cost of running AX/OCR/embed on every tick |
| Background poll relaxes from 5s to 15s | 2 | Reduced steady-state overhead |
| Capture depth adapts to trigger type | 2 | Full pipeline on every tick even when light check suffices |
| Image-heavy screens (Figma, video, design tools) get captured meaningfully | 3 | Major gap — these screens currently log empty AX text |
| `TaskIdentifier` groups by activity, not literal text | 3 | Better task boundaries; less manual cleanup |
| 50-token gist replaces 2KB raw text for downstream embedding | 3 | Cleaner embeddings, better cluster shape |
| Free, offline structured extraction | 4 | OpenAI cost reduction; offline operation |
| Temporal queries ("what was I doing before X?") | A | Vectors can't express adjacency or ordering |
| Structural browsing ("everything connected to this project") | A | Multi-hop traversal across event types |
| Graph-assisted compaction pre-groups events into task candidates | A | Reduces LLM calls in `TaskIdentifier` |
| Cross-event-type linking (screen → git commit → file) | A | Events currently siloed by type |
| LLM-inferred entity links (screen event → specific PR/file) | B | Gists contain entity mentions that go unlinked |
| Hybrid retrieval (vector + graph) for richer query results | C | Structural context around semantic matches |

---

## Tradeoffs and risks

**Memory.** Helper process resident size with both models loaded: ~2–3 GB. This is significant on 8 GB Macs. Mitigation: lazy-load FastVLM, unload after N seconds of no context switches.

**Cold start.** First load takes 5–10 seconds. The daemon should start the helper at boot, not on first capture, so the first poll tick isn't blocked.

**Disk.** Model weights add ~1.5 GB. Stored under `~/Library/Application Support/patcha/models/`, downloaded on first run.

**Caption quality.** FastVLM-0.5B is small. Captions for ambiguous screens may be generic ("user is viewing a webpage"). If quality is insufficient, the same helper architecture lets you swap in Moondream2 (~700 MB at 4-bit, stronger OCR), FastVLM-1.5B, or Gemma 4 E2B (~1.4 GB, structured output) with a config change — no other code paths shift.

**macOS version.** MLX requires macOS 13.5+. Foundation Models requires macOS 26. Phases 1–3 need macOS 13.5+ which most users will have; Phase 4 is gated.

**Permissions.** No new permissions needed — Accessibility and Screen Recording are already granted. The `CGEventTap` used by idle detection requires Accessibility permission (already have it). The `NSWorkspace` notifications for app switch and space change require no special permissions.

**Privacy.** All new processing remains local. Visual embeddings are not sent anywhere. The idle detector monitors input *activity* only (boolean signal), never keystrokes or content. The OpenAI text-embedding call is unchanged for now (Phase 4 removes it for screen events).

**Observer crash recovery.** If `observer.swift` crashes, the daemon should fall back to the existing polled `WindowCollector` (AppleScript every 5 seconds) as a degraded mode with a logged warning. The event-driven path is an optimization, not a hard requirement — the pipeline works without it, just less efficiently.

**Cache staleness.** The 2-hour TTL on app cache entries is a tradeoff: too short and you lose the alt-tab deduplication benefit; too long and you miss changes that happened while the app was backgrounded (Slack messages, browser refreshes, build completions). 2 hours is a starting point — tune based on observed false-negative rate.

**Debounce window.** 500 ms catches most rapid alt-tab sequences, but a user who intentionally glances at an app for 400 ms won't get it captured. This is acceptable — a 400 ms glance isn't a meaningful context switch. If it matters, reduce to 300 ms.

**Determinism in compaction.** Visual embeddings are not stable across MobileCLIP2 versions. If the model is updated, prior embeddings cannot be compared to new ones. Mitigation: store `visual_embedder_version` in event metadata; vector dedup only compares within a single version.

**Helper crash recovery.** If the helper crashes (OOM, model load failure, MLX issue), the daemon should fall back to AX-only mode with a logged warning rather than refusing to capture. Practically: keep the legacy text-diff branch available as a safety fallback for one release cycle, then remove.

**Graph storage.** FalkorDB Lite stores data on disk as a file (`activity.db`). At ~1,000 events/day × ~500 bytes per node/edge set, the graph adds ~500 KB/day — negligible compared to vector storage. The graph is not pruned during compaction; old nodes and edges remain as long-term structural memory even after raw vectors are deleted. Over a year this grows to ~150 MB, which is fine.

**Graph write latency.** Each event upserts 3–5 nodes and edges via Cypher `MERGE` + `CREATE`. FalkorDB Lite benchmarks at <5 ms per query on typical graph sizes. This is in the noise relative to the screencapture + AX/OCR + embedding pipeline.

**LLM-inferred edge quality.** Phase B edges (RELATES_TO, REVIEWS, etc.) will have false positives. A bad edge is worse than a missing edge — it pollutes traversal results. The `linker.py` should apply a confidence threshold and only write edges above it. Edges below threshold can be stored as "candidate" edges for human review or later validation.

**Graph + vector consistency.** Two stores means they can drift if one write succeeds and the other fails. Mitigation: write Qdrant first (the primary), then graph (additive context). If the graph write fails, the event is still searchable by vector — the graph just misses an edge. Log the failure and retry on next compaction pass.

---

## Open questions

1. **Per-event vs per-window gist.** Should every screen event carry a gist, or only the first event in a context-switch window? Storing once per window is cheaper but loses temporal granularity for `TaskIdentifier`. Recommendation: gist per context switch, propagate forward to subsequent events in the same window until the next switch — this is what the pipeline diagram above assumes.

2. **Backfill.** Should historical events be retroactively embedded with MobileCLIP2? Only possible if the original screenshots were retained, which they currently aren't. Decision: no backfill; the pre-Phase-1 corpus stays text-only.

3. **Cache TTL tuning.** The 2-hour default for per-app cache staleness is a guess. Should this be per-app (browsers stale faster than IDEs) or global? Could also adapt dynamically: if the cache hit rate for an app is consistently low (the app always looks different when you return), shorten its TTL automatically.

4. **File save as a trigger.** `FSEvents` on active project directories could fire a capture on file save — a natural "unit of work completed" marker. Worth adding to the observer, or is it redundant with the git staging snapshots that `GitCollector` already captures?

5. **Clipboard change detection.** `NSPasteboard.general.changeCount` is trivially cheap to poll. A copy/paste often signals information transfer between contexts. Worth adding as a trigger, or too noisy?

6. **Captioner model selection.** FastVLM-0.5B vs Moondream2 vs Gemma 4 E2B for the captioner role. The helper architecture supports swapping with a config change. Recommendation: benchmark all three on 50 representative screenshots before committing — screen captioning quality varies significantly across models and the right choice depends on the mix of apps the user runs.

7. **Graph retention policy.** Raw events are pruned by compaction, but graph nodes and edges are structural memory. Should old graph edges be pruned (e.g., `FOLLOWED_BY` edges older than 90 days), or kept indefinitely as a long-term activity skeleton? Keeping them is cheap (~150 MB/year) and enables long-range queries ("how has my workflow changed over the last 6 months").

8. **Graph database choice.** FalkorDB Lite vs KùzuDB. Both are embedded, both support Cypher. FalkorDB Lite has tighter GraphRAG ecosystem integration; KùzuDB is lighter and faster for analytical queries. Worth prototyping Phase A with both and comparing write throughput + query latency on a day's worth of data.

9. **LLM model for entity linking (Phase B).** If using Gemma 4 E2B as the captioner, it can double as the entity linker (structured output via function calling). If using FastVLM or Moondream2 (which lack structured output), you'd need a separate text LLM for linking — Qwen3.5-4B (~2.5 GB at 4-bit) is the smallest reliable option. This decision is coupled to the captioner choice.

10. **Graph-assisted compaction vs pure LLM.** How much does graph pre-grouping actually reduce `TaskIdentifier` LLM calls? Worth measuring on a week of data: run compaction with and without graph pre-grouping, compare task quality and LLM token cost.
