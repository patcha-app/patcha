use crate::{
    db::Db,
    models::{Entity, EntityType, Relationship},
};
use anyhow::Result;
use chrono::Utc;
use rusqlite::params;
use std::collections::{HashMap, HashSet, VecDeque};
use std::sync::Arc;

pub struct KnowledgeGraph {
    db: Arc<Db>,
}

impl KnowledgeGraph {
    pub fn new(db: Arc<Db>) -> Self {
        Self { db }
    }

    // -----------------------------------------------------------------------
    // Write
    // -----------------------------------------------------------------------

    pub fn add_entity(&self, entity: &Entity) -> Result<()> {
        let conn = self.db.conn();
        let aliases = serde_json::to_string(&entity.aliases)?;
        let metadata = serde_json::to_string(&entity.metadata)?;
        let entity_type = format!("{:?}", entity.entity_type).to_lowercase();

        conn.execute(
            "INSERT INTO entities(id, name, type, aliases, confidence, first_seen, last_seen, mention_count, metadata)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9)
             ON CONFLICT(id) DO UPDATE SET
               last_seen     = excluded.last_seen,
               mention_count = mention_count + 1,
               confidence    = max(confidence, excluded.confidence),
               aliases       = excluded.aliases,
               metadata      = excluded.metadata",
            params![
                entity.id,
                entity.name,
                entity_type,
                aliases,
                entity.confidence,
                entity.first_seen.to_rfc3339(),
                entity.last_seen.to_rfc3339(),
                entity.mention_count,
                metadata,
            ],
        )?;
        Ok(())
    }

    /// Upsert entity by name — returns the canonical id (existing or new).
    pub fn upsert_entity_by_name(
        &self,
        name: &str,
        entity_type: EntityType,
    ) -> Result<String> {
        let conn = self.db.conn();
        let type_str = format!("{:?}", entity_type).to_lowercase();

        // Try to find existing
        let existing: Option<String> = conn
            .query_row(
                "SELECT id FROM entities WHERE lower(name) = lower(?1) AND type = ?2 LIMIT 1",
                params![name, type_str],
                |row| row.get(0),
            )
            .ok();

        if let Some(id) = existing {
            // Bump mention_count and last_seen
            conn.execute(
                "UPDATE entities SET mention_count = mention_count + 1, last_seen = ?1 WHERE id = ?2",
                params![Utc::now().to_rfc3339(), id],
            )?;
            return Ok(id);
        }

        let entity = Entity::new(name, entity_type);
        drop(conn);
        self.add_entity(&entity)?;
        Ok(entity.id)
    }

    pub fn add_relationship(&self, rel: &Relationship) -> Result<()> {
        let conn = self.db.conn();
        let rel_type = format!("{:?}", rel.relationship_type)
            .chars()
            .enumerate()
            .map(|(i, c)| {
                if i > 0 && c.is_uppercase() {
                    format!("_{}", c.to_lowercase())
                } else {
                    c.to_lowercase().to_string()
                }
            })
            .collect::<String>();
        let supporting = serde_json::to_string(&rel.supporting_activity_ids)?;
        let metadata = serde_json::to_string(&rel.metadata)?;

        conn.execute(
            "INSERT INTO relationships(id, source_entity_id, target_entity_id, relationship_type,
               confidence, first_seen, last_seen, strength, supporting_activities, metadata)
             VALUES (?1, ?2, ?3, ?4, ?5, ?6, ?7, ?8, ?9, ?10)
             ON CONFLICT(id) DO UPDATE SET
               last_seen             = excluded.last_seen,
               strength              = strength + 0.1,
               supporting_activities = excluded.supporting_activities,
               metadata              = excluded.metadata",
            params![
                rel.id,
                rel.source_entity_id,
                rel.target_entity_id,
                rel_type,
                rel.confidence,
                rel.first_seen.to_rfc3339(),
                rel.last_seen.to_rfc3339(),
                rel.strength,
                supporting,
                metadata,
            ],
        )?;
        Ok(())
    }

    // -----------------------------------------------------------------------
    // Read
    // -----------------------------------------------------------------------

    pub fn get_entity_by_id(&self, id: &str) -> Result<Option<Entity>> {
        let conn = self.db.conn();
        conn.query_row(
            "SELECT id, name, type, aliases, confidence, first_seen, last_seen, mention_count, metadata
             FROM entities WHERE id = ?1",
            params![id],
            row_to_entity,
        )
        .optional()
        .map_err(Into::into)
    }

    pub fn get_entity_by_name(&self, name: &str) -> Result<Option<Entity>> {
        let conn = self.db.conn();
        conn.query_row(
            "SELECT id, name, type, aliases, confidence, first_seen, last_seen, mention_count, metadata
             FROM entities WHERE lower(name) = lower(?1) LIMIT 1",
            params![name],
            row_to_entity,
        )
        .optional()
        .map_err(Into::into)
    }

    /// BFS neighbors up to `max_depth` hops.
    pub fn get_neighbors(
        &self,
        entity_id: &str,
        max_depth: usize,
    ) -> Result<Vec<Entity>> {
        let mut visited: HashSet<String> = HashSet::new();
        let mut queue: VecDeque<(String, usize)> = VecDeque::new();
        let mut results: Vec<Entity> = Vec::new();

        visited.insert(entity_id.to_owned());
        queue.push_back((entity_id.to_owned(), 0));

        while let Some((current_id, depth)) = queue.pop_front() {
            if depth >= max_depth {
                continue;
            }
            // Each helper acquires and releases the lock independently — no double-lock.
            let neighbors = self.direct_neighbor_ids(&current_id)?;
            for neighbor_id in neighbors {
                if visited.contains(&neighbor_id) {
                    continue;
                }
                visited.insert(neighbor_id.clone());
                if let Some(entity) = self.get_entity_by_id(&neighbor_id)? {
                    results.push(entity);
                    queue.push_back((neighbor_id, depth + 1));
                }
            }
        }

        Ok(results)
    }

    fn direct_neighbor_ids(&self, entity_id: &str) -> Result<Vec<String>> {
        let conn = self.db.conn();
        let mut stmt = conn.prepare_cached(
            "SELECT DISTINCT CASE WHEN source_entity_id = ?1 THEN target_entity_id
                                 ELSE source_entity_id END as neighbor
             FROM relationships
             WHERE source_entity_id = ?1 OR target_entity_id = ?1",
        )?;
        let ids: Vec<String> = stmt
            .query_map(params![entity_id], |row| row.get(0))?
            .collect::<std::result::Result<_, _>>()?;
        Ok(ids)
    }

    pub fn get_related_entities(&self, entity_ids: &[String]) -> Result<Vec<Entity>> {
        if entity_ids.is_empty() {
            return Ok(Vec::new());
        }
        let mut all: Vec<Entity> = Vec::new();
        for id in entity_ids {
            let mut neighbors = self.get_neighbors(id, 2)?;
            all.append(&mut neighbors);
        }
        // Deduplicate by id
        let mut seen = HashSet::new();
        all.retain(|e| seen.insert(e.id.clone()));
        Ok(all)
    }

    pub fn get_graph_stats(&self) -> Result<serde_json::Value> {
        let conn = self.db.conn();

        let entity_count: i64 =
            conn.query_row("SELECT COUNT(*) FROM entities", [], |r| r.get(0))?;
        let rel_count: i64 =
            conn.query_row("SELECT COUNT(*) FROM relationships", [], |r| r.get(0))?;

        let mut type_stmt =
            conn.prepare("SELECT type, COUNT(*) FROM entities GROUP BY type ORDER BY 2 DESC")?;
        let type_counts: Vec<(String, i64)> = type_stmt
            .query_map([], |row| Ok((row.get(0)?, row.get(1)?)))?
            .collect::<std::result::Result<_, _>>()?;

        Ok(serde_json::json!({
            "total_entities": entity_count,
            "total_relationships": rel_count,
            "entity_types": type_counts.into_iter().collect::<HashMap<_, _>>(),
        }))
    }

    pub fn cleanup_old_entities(&self) -> Result<usize> {
        let conn = self.db.conn();
        let cutoff = (Utc::now() - chrono::Duration::days(30)).to_rfc3339();
        let n = conn.execute(
            "DELETE FROM entities WHERE last_seen < ?1 AND mention_count < 3",
            params![cutoff],
        )?;
        Ok(n)
    }
}

// ---------------------------------------------------------------------------
// Row deserializer
// ---------------------------------------------------------------------------

fn row_to_entity(row: &rusqlite::Row) -> rusqlite::Result<Entity> {
    let id: String = row.get(0)?;
    let name: String = row.get(1)?;
    let type_str: String = row.get(2)?;
    let aliases_str: String = row.get(3)?;
    let confidence: f64 = row.get(4)?;
    let first_seen_str: String = row.get(5)?;
    let last_seen_str: String = row.get(6)?;
    let mention_count: u32 = row.get(7)?;
    let metadata_str: String = row.get(8)?;

    use chrono::DateTime;
    let first_seen = DateTime::parse_from_rfc3339(&first_seen_str)
        .map(|dt| dt.with_timezone(&Utc))
        .unwrap_or_else(|_| Utc::now());
    let last_seen = DateTime::parse_from_rfc3339(&last_seen_str)
        .map(|dt| dt.with_timezone(&Utc))
        .unwrap_or_else(|_| Utc::now());

    let entity_type = match type_str.as_str() {
        "technology" => EntityType::Technology,
        "project" => EntityType::Project,
        "concept" => EntityType::Concept,
        "person" => EntityType::Person,
        "organization" => EntityType::Organization,
        "file" => EntityType::File,
        "feature" => EntityType::Feature,
        "issue" => EntityType::Issue,
        "documentation" => EntityType::Documentation,
        _ => EntityType::Concept,
    };

    let aliases: Vec<String> = serde_json::from_str(&aliases_str).unwrap_or_default();
    let metadata: HashMap<String, serde_json::Value> =
        serde_json::from_str(&metadata_str).unwrap_or_default();

    Ok(Entity {
        id,
        name,
        entity_type,
        aliases,
        confidence,
        first_seen,
        last_seen,
        mention_count,
        metadata,
    })
}

trait OptionalExt<T> {
    fn optional(self) -> Result<Option<T>>;
}

impl<T> OptionalExt<T> for rusqlite::Result<T> {
    fn optional(self) -> Result<Option<T>> {
        match self {
            Ok(v) => Ok(Some(v)),
            Err(rusqlite::Error::QueryReturnedNoRows) => Ok(None),
            Err(e) => Err(e.into()),
        }
    }
}
