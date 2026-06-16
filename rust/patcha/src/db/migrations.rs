use anyhow::Result;
use rusqlite::Connection;

pub fn run(conn: &Connection) -> Result<()> {
    conn.execute_batch(SCHEMA)?;
    Ok(())
}

const SCHEMA: &str = "
-- Schema version tracking
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER PRIMARY KEY
);

-- -------------------------------------------------------------------------
-- Events: raw activity records
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS events (
    id          TEXT PRIMARY KEY,
    timestamp   TEXT NOT NULL,
    date        TEXT NOT NULL,
    event_type  TEXT NOT NULL,
    source      TEXT,
    raw_content TEXT NOT NULL DEFAULT '',
    summary     TEXT,
    category    TEXT,
    project     TEXT,
    task_id     TEXT,
    metadata    TEXT NOT NULL DEFAULT '{}',
    compacted   INTEGER NOT NULL DEFAULT 0
);

CREATE INDEX IF NOT EXISTS idx_events_date        ON events(date);
CREATE INDEX IF NOT EXISTS idx_events_type        ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_category    ON events(category);
CREATE INDEX IF NOT EXISTS idx_events_project     ON events(project);
CREATE INDEX IF NOT EXISTS idx_events_task_id     ON events(task_id);
CREATE INDEX IF NOT EXISTS idx_events_compacted   ON events(compacted);

-- sqlite-vec virtual table for event embeddings (768-dim BGE)
CREATE VIRTUAL TABLE IF NOT EXISTS vec_events USING vec0(
    embedding float[768],
    +event_id TEXT
);

-- -------------------------------------------------------------------------
-- Full-text search index over events (BM25 keyword arm of hybrid retrieval).
-- Standalone FTS5 table; `text` concatenates the human-readable signal from
-- raw_content, summary, and the searchable metadata fields. Kept in sync with
-- the events table via triggers below.
-- -------------------------------------------------------------------------
CREATE VIRTUAL TABLE IF NOT EXISTS fts_events USING fts5(
    event_id UNINDEXED,
    text,
    tokenize = 'porter unicode61'
);

-- Trigger text expression must stay identical across insert/update/backfill.
CREATE TRIGGER IF NOT EXISTS events_fts_ai AFTER INSERT ON events BEGIN
    INSERT INTO fts_events(event_id, text) VALUES (
        new.id,
        new.raw_content || ' ' || coalesce(new.summary, '') || ' '
            || coalesce(json_extract(new.metadata, '$.app_name'), '') || ' '
            || coalesce(json_extract(new.metadata, '$.window_title'), '') || ' '
            || coalesce(json_extract(new.metadata, '$.gist'), '') || ' '
            || coalesce(json_extract(new.metadata, '$.domain'), '')
    );
END;

CREATE TRIGGER IF NOT EXISTS events_fts_ad AFTER DELETE ON events BEGIN
    DELETE FROM fts_events WHERE event_id = old.id;
END;

CREATE TRIGGER IF NOT EXISTS events_fts_au AFTER UPDATE ON events BEGIN
    DELETE FROM fts_events WHERE event_id = old.id;
    INSERT INTO fts_events(event_id, text) VALUES (
        new.id,
        new.raw_content || ' ' || coalesce(new.summary, '') || ' '
            || coalesce(json_extract(new.metadata, '$.app_name'), '') || ' '
            || coalesce(json_extract(new.metadata, '$.window_title'), '') || ' '
            || coalesce(json_extract(new.metadata, '$.gist'), '') || ' '
            || coalesce(json_extract(new.metadata, '$.domain'), '')
    );
END;

-- One-time backfill for events that predate the FTS index (idempotent: the
-- NOT IN guard skips rows already indexed, so re-running on each boot is a no-op).
INSERT INTO fts_events(event_id, text)
SELECT id,
       raw_content || ' ' || coalesce(summary, '') || ' '
           || coalesce(json_extract(metadata, '$.app_name'), '') || ' '
           || coalesce(json_extract(metadata, '$.window_title'), '') || ' '
           || coalesce(json_extract(metadata, '$.gist'), '') || ' '
           || coalesce(json_extract(metadata, '$.domain'), '')
FROM events
WHERE id NOT IN (SELECT event_id FROM fts_events);

-- -------------------------------------------------------------------------
-- Tasks
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS tasks (
    id               TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    description      TEXT,
    status           TEXT NOT NULL DEFAULT 'active',
    priority         TEXT NOT NULL DEFAULT 'medium',
    category         TEXT,
    project          TEXT,
    start_time       TEXT,
    end_time         TEXT,
    duration_minutes REAL,
    activity_count   INTEGER,
    confidence_score REAL,
    tags             TEXT NOT NULL DEFAULT '[]',
    accomplishments  TEXT NOT NULL DEFAULT '[]',
    main_themes      TEXT NOT NULL DEFAULT '[]',
    activity_ids     TEXT NOT NULL DEFAULT '[]',
    parent_task_id   TEXT,
    subtask_ids      TEXT NOT NULL DEFAULT '[]',
    created_at       TEXT NOT NULL,
    updated_at       TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_status    ON tasks(status);
CREATE INDEX IF NOT EXISTS idx_tasks_project   ON tasks(project);
CREATE INDEX IF NOT EXISTS idx_tasks_category  ON tasks(category);
CREATE INDEX IF NOT EXISTS idx_tasks_date      ON tasks(date(start_time));

CREATE VIRTUAL TABLE IF NOT EXISTS vec_tasks USING vec0(
    embedding float[768],
    +task_id TEXT
);

-- -------------------------------------------------------------------------
-- Activity graph: structural temporal graph
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS activity_nodes (
    id         TEXT PRIMARY KEY,
    label      TEXT NOT NULL,
    props      TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_anodes_label ON activity_nodes(label);

CREATE TABLE IF NOT EXISTS activity_edges (
    id         TEXT PRIMARY KEY,
    src_id     TEXT NOT NULL,
    dst_id     TEXT NOT NULL,
    type       TEXT NOT NULL,
    props      TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    FOREIGN KEY (src_id) REFERENCES activity_nodes(id),
    FOREIGN KEY (dst_id) REFERENCES activity_nodes(id)
);

CREATE INDEX IF NOT EXISTS idx_aedges_src  ON activity_edges(src_id, type);
CREATE INDEX IF NOT EXISTS idx_aedges_dst  ON activity_edges(dst_id, type);
CREATE INDEX IF NOT EXISTS idx_aedges_type ON activity_edges(type);

-- Single-row session state
CREATE TABLE IF NOT EXISTS activity_session_state (
    id                 INTEGER PRIMARY KEY CHECK (id = 1),
    current_session_id TEXT,
    last_event_ts      TEXT
);

INSERT OR IGNORE INTO activity_session_state(id) VALUES (1);

-- -------------------------------------------------------------------------
-- Knowledge graph: entities and relationships
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS entities (
    id            TEXT PRIMARY KEY,
    name          TEXT NOT NULL,
    type          TEXT NOT NULL,
    aliases       TEXT NOT NULL DEFAULT '[]',
    confidence    REAL NOT NULL DEFAULT 1.0,
    first_seen    TEXT NOT NULL,
    last_seen     TEXT NOT NULL,
    mention_count INTEGER NOT NULL DEFAULT 1,
    metadata      TEXT NOT NULL DEFAULT '{}'
);

CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name);
CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type);

CREATE TABLE IF NOT EXISTS relationships (
    id                    TEXT PRIMARY KEY,
    source_entity_id      TEXT NOT NULL,
    target_entity_id      TEXT NOT NULL,
    relationship_type     TEXT NOT NULL,
    confidence            REAL NOT NULL DEFAULT 1.0,
    first_seen            TEXT NOT NULL,
    last_seen             TEXT NOT NULL,
    strength              REAL NOT NULL DEFAULT 1.0,
    supporting_activities TEXT NOT NULL DEFAULT '[]',
    metadata              TEXT NOT NULL DEFAULT '{}',
    FOREIGN KEY (source_entity_id) REFERENCES entities(id),
    FOREIGN KEY (target_entity_id) REFERENCES entities(id)
);

CREATE INDEX IF NOT EXISTS idx_rels_src  ON relationships(source_entity_id, relationship_type);
CREATE INDEX IF NOT EXISTS idx_rels_dst  ON relationships(target_entity_id, relationship_type);
CREATE INDEX IF NOT EXISTS idx_rels_type ON relationships(relationship_type);

-- -------------------------------------------------------------------------
-- Timeline summaries: one LLM-written narrative per (date, hour), generated
-- by the daemon's hourly job and read back by the /api/timeline endpoint.
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS timeline_summaries (
    date         TEXT NOT NULL,
    hour         INTEGER NOT NULL,
    title        TEXT NOT NULL DEFAULT '',
    summary      TEXT NOT NULL DEFAULT '',
    tree         TEXT,                          -- JSON TimelineTreeNode, nullable
    apps         TEXT NOT NULL DEFAULT '[]',    -- JSON array of app names
    categories   TEXT NOT NULL DEFAULT '[]',    -- JSON array of category labels
    event_count  INTEGER NOT NULL DEFAULT 0,
    generated_at TEXT NOT NULL,
    PRIMARY KEY (date, hour)
);

CREATE INDEX IF NOT EXISTS idx_timeline_summaries_date ON timeline_summaries(date);
";
