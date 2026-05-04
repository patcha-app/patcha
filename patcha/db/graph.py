"""Knowledge graph storage and management for graph RAG."""

import json
import sqlite3
from typing import List, Dict, Set, Optional, Tuple
from datetime import datetime, timedelta
from collections import defaultdict, deque

from patcha.db.models import (
    Entity,
    Relationship,
    GraphPath,
    EntityType,
    RelationshipType,
    EntityExtractionResult,
)
from patcha.config import config


class KnowledgeGraph:
    """
    In-memory knowledge graph with SQLite persistence.
    Manages entities, relationships, and graph traversal for RAG.
    """

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(config.data_dir / "knowledge_graph.db")

        self.db_path = db_path
        self.entities: Dict[str, Entity] = {}
        self.relationships: Dict[str, Relationship] = {}
        self.entity_relationships: Dict[str, Set[str]] = defaultdict(set)

        # Initialize database
        self._init_database()
        self._load_from_database()

    def _init_database(self):
        """Initialize SQLite database schema."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS entities (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    type TEXT NOT NULL,
                    aliases TEXT,
                    confidence REAL,
                    first_seen TEXT,
                    last_seen TEXT,
                    mention_count INTEGER,
                    metadata TEXT
                )
            """)

            conn.execute("""
                CREATE TABLE IF NOT EXISTS relationships (
                    id TEXT PRIMARY KEY,
                    source_entity_id TEXT,
                    target_entity_id TEXT,
                    relationship_type TEXT,
                    confidence REAL,
                    first_seen TEXT,
                    last_seen TEXT,
                    strength REAL,
                    metadata TEXT,
                    supporting_activities TEXT,
                    FOREIGN KEY (source_entity_id) REFERENCES entities (id),
                    FOREIGN KEY (target_entity_id) REFERENCES entities (id)
                )
            """)

            # Create indexes for better query performance
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(type)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_entities_name ON entities(name)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_source ON relationships(source_entity_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_target ON relationships(target_entity_id)"
            )
            conn.execute(
                "CREATE INDEX IF NOT EXISTS idx_relationships_type ON relationships(relationship_type)"
            )

    def _load_from_database(self):
        """Load entities and relationships from SQLite database."""
        with sqlite3.connect(self.db_path) as conn:
            # Load entities
            cursor = conn.execute("SELECT * FROM entities")
            for row in cursor.fetchall():
                entity = self._row_to_entity(row)
                self.entities[entity.id] = entity

            # Load relationships
            cursor = conn.execute("SELECT * FROM relationships")
            for row in cursor.fetchall():
                relationship = self._row_to_relationship(row)
                self.relationships[relationship.id] = relationship
                self.entity_relationships[relationship.source_entity_id].add(
                    relationship.id
                )
                self.entity_relationships[relationship.target_entity_id].add(
                    relationship.id
                )

    def _row_to_entity(self, row) -> Entity:
        """Convert database row to Entity object."""
        return Entity(
            id=row[0],
            name=row[1],
            type=EntityType(row[2]),
            aliases=set(json.loads(row[3])) if row[3] else set(),
            confidence=row[4],
            first_seen=datetime.fromisoformat(row[5]),
            last_seen=datetime.fromisoformat(row[6]),
            mention_count=row[7],
            metadata=json.loads(row[8]) if row[8] else {},
        )

    def _row_to_relationship(self, row) -> Relationship:
        """Convert database row to Relationship object."""
        return Relationship(
            id=row[0],
            source_entity_id=row[1],
            target_entity_id=row[2],
            relationship_type=RelationshipType(row[3]),
            confidence=row[4],
            first_seen=datetime.fromisoformat(row[5]),
            last_seen=datetime.fromisoformat(row[6]),
            strength=row[7],
            metadata=json.loads(row[8]) if row[8] else {},
            supporting_activities=json.loads(row[9]) if row[9] else [],
        )

    def add_entity(self, entity: Entity, activity_id: Optional[str] = None) -> Entity:
        """Add or update an entity in the graph."""
        existing_entity = self.entities.get(entity.id)

        if existing_entity:
            # Update existing entity
            existing_entity.last_seen = max(existing_entity.last_seen, entity.last_seen)
            existing_entity.mention_count += 1
            existing_entity.aliases.update(entity.aliases)

            # Update confidence (weighted average)
            total_mentions = existing_entity.mention_count
            existing_entity.confidence = (
                existing_entity.confidence * (total_mentions - 1) + entity.confidence
            ) / total_mentions

            self.entities[entity.id] = existing_entity
            self._persist_entity(existing_entity)
            return existing_entity
        else:
            # Add new entity
            self.entities[entity.id] = entity
            self._persist_entity(entity)
            return entity

    def add_relationship(
        self, relationship: Relationship, activity_id: Optional[str] = None
    ) -> Relationship:
        """Add or update a relationship in the graph."""
        existing_rel = self.relationships.get(relationship.id)

        if existing_rel:
            # Update existing relationship
            existing_rel.last_seen = max(existing_rel.last_seen, relationship.last_seen)
            existing_rel.strength += 0.1  # Increase strength with each occurrence

            # Update confidence (weighted average)
            existing_rel.confidence = (
                existing_rel.confidence + relationship.confidence
            ) / 2

            if activity_id and activity_id not in existing_rel.supporting_activities:
                existing_rel.supporting_activities.append(activity_id)

            self.relationships[relationship.id] = existing_rel
            self._persist_relationship(existing_rel)
            return existing_rel
        else:
            # Add new relationship
            if activity_id:
                relationship.supporting_activities = [activity_id]

            self.relationships[relationship.id] = relationship
            self.entity_relationships[relationship.source_entity_id].add(
                relationship.id
            )
            self.entity_relationships[relationship.target_entity_id].add(
                relationship.id
            )
            self._persist_relationship(relationship)
            return relationship

    def _persist_entity(self, entity: Entity):
        """Persist entity to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO entities
                (id, name, type, aliases, confidence, first_seen, last_seen, mention_count, metadata)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    entity.id,
                    entity.name,
                    entity.type.value,
                    json.dumps(list(entity.aliases)),
                    entity.confidence,
                    entity.first_seen.isoformat(),
                    entity.last_seen.isoformat(),
                    entity.mention_count,
                    json.dumps(entity.metadata),
                ),
            )

    def _persist_relationship(self, relationship: Relationship):
        """Persist relationship to database."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                INSERT OR REPLACE INTO relationships
                (id, source_entity_id, target_entity_id, relationship_type, confidence,
                 first_seen, last_seen, strength, metadata, supporting_activities)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
                (
                    relationship.id,
                    relationship.source_entity_id,
                    relationship.target_entity_id,
                    relationship.relationship_type.value,
                    relationship.confidence,
                    relationship.first_seen.isoformat(),
                    relationship.last_seen.isoformat(),
                    relationship.strength,
                    json.dumps(relationship.metadata),
                    json.dumps(relationship.supporting_activities),
                ),
            )

    def get_entity(self, entity_id: str) -> Optional[Entity]:
        """Get entity by ID."""
        return self.entities.get(entity_id)

    def get_entities_by_type(self, entity_type: EntityType) -> List[Entity]:
        """Get all entities of a specific type."""
        return [
            entity for entity in self.entities.values() if entity.type == entity_type
        ]

    def get_entities_by_name_pattern(self, pattern: str) -> List[Entity]:
        """Get entities matching a name pattern."""
        pattern_lower = pattern.lower()
        matching_entities = []

        for entity in self.entities.values():
            if pattern_lower in entity.name.lower() or any(
                pattern_lower in alias.lower() for alias in entity.aliases
            ):
                matching_entities.append(entity)

        return matching_entities

    def get_neighbors(self, entity_id: str, max_depth: int = 1) -> List[Entity]:
        """Get neighboring entities up to max_depth."""
        if entity_id not in self.entities:
            return []

        visited = set()
        neighbors = []
        queue = deque([(entity_id, 0)])

        while queue:
            current_id, depth = queue.popleft()

            if current_id in visited or depth > max_depth:
                continue

            visited.add(current_id)

            if depth > 0:  # Don't include the starting entity
                neighbors.append(self.entities[current_id])

            if depth < max_depth:
                # Add connected entities to queue
                for rel_id in self.entity_relationships.get(current_id, set()):
                    relationship = self.relationships[rel_id]
                    next_entity_id = (
                        relationship.target_entity_id
                        if relationship.source_entity_id == current_id
                        else relationship.source_entity_id
                    )
                    if next_entity_id not in visited:
                        queue.append((next_entity_id, depth + 1))

        return neighbors

    def find_paths(
        self, source_id: str, target_id: str, max_depth: int = 3
    ) -> List[GraphPath]:
        """Find paths between two entities."""
        if source_id not in self.entities or target_id not in self.entities:
            return []

        paths = []
        visited = set()

        def dfs(
            current_id: str,
            path_entities: List[Entity],
            path_relationships: List[Relationship],
            depth: int,
        ):
            if depth > max_depth:
                return

            if current_id == target_id and len(path_entities) > 1:
                # Found a path
                total_strength = sum(rel.strength for rel in path_relationships)
                paths.append(
                    GraphPath(
                        entities=path_entities.copy(),
                        relationships=path_relationships.copy(),
                        total_strength=total_strength,
                        path_length=len(path_relationships),
                    )
                )
                return

            if current_id in visited:
                return

            visited.add(current_id)

            # Explore neighbors
            for rel_id in self.entity_relationships.get(current_id, set()):
                relationship = self.relationships[rel_id]
                next_entity_id = (
                    relationship.target_entity_id
                    if relationship.source_entity_id == current_id
                    else relationship.source_entity_id
                )

                if next_entity_id not in visited:
                    path_entities.append(self.entities[next_entity_id])
                    path_relationships.append(relationship)

                    dfs(next_entity_id, path_entities, path_relationships, depth + 1)

                    path_entities.pop()
                    path_relationships.pop()

            visited.remove(current_id)

        # Start DFS
        dfs(source_id, [self.entities[source_id]], [], 0)

        # Sort paths by strength and length
        paths.sort(key=lambda p: (p.total_strength, -p.path_length), reverse=True)

        return paths[:5]  # Return top 5 paths

    def get_related_entities(
        self,
        entity_ids: List[str],
        relationship_types: Optional[List[RelationshipType]] = None,
        max_results: int = 10,
    ) -> List[Tuple[Entity, float]]:
        """Get entities related to the given entities with relevance scores."""
        if not entity_ids:
            return []

        related_entities = defaultdict(float)
        relationship_type_filter = (
            set(relationship_types) if relationship_types else None
        )

        for entity_id in entity_ids:
            if entity_id not in self.entities:
                continue

            # Get direct neighbors
            for rel_id in self.entity_relationships.get(entity_id, set()):
                relationship = self.relationships[rel_id]

                # Filter by relationship type if specified
                if (
                    relationship_type_filter
                    and relationship.relationship_type not in relationship_type_filter
                ):
                    continue

                # Determine the related entity
                related_entity_id = (
                    relationship.target_entity_id
                    if relationship.source_entity_id == entity_id
                    else relationship.source_entity_id
                )

                if related_entity_id not in entity_ids:  # Don't include input entities
                    # Calculate relevance score based on relationship strength and confidence
                    score = relationship.strength * relationship.confidence
                    related_entities[related_entity_id] += score

        # Sort by relevance score
        sorted_entities = sorted(
            [(self.entities[eid], score) for eid, score in related_entities.items()],
            key=lambda x: x[1],
            reverse=True,
        )

        return sorted_entities[:max_results]

    def add_extraction_result(
        self, result: EntityExtractionResult, activity_id: Optional[str] = None
    ):
        """Add entities and relationships from an extraction result."""
        # Add entities
        for entity in result.entities:
            self.add_entity(entity, activity_id)

        # Add relationships
        for relationship in result.relationships:
            self.add_relationship(relationship, activity_id)

    def get_graph_stats(self) -> Dict[str, int]:
        """Get statistics about the knowledge graph."""
        entity_type_counts = defaultdict(int)
        relationship_type_counts = defaultdict(int)

        for entity in self.entities.values():
            entity_type_counts[entity.type.value] += 1

        for relationship in self.relationships.values():
            relationship_type_counts[relationship.relationship_type.value] += 1

        return {
            "total_entities": len(self.entities),
            "total_relationships": len(self.relationships),
            "entity_types": dict(entity_type_counts),
            "relationship_types": dict(relationship_type_counts),
        }

    def cleanup_old_entities(self, days_threshold: int = 30):
        """Remove entities that haven't been seen in the specified number of days."""
        cutoff_date = datetime.now() - timedelta(days=days_threshold)
        entities_to_remove = []

        for entity_id, entity in self.entities.items():
            if entity.last_seen < cutoff_date and entity.mention_count < 3:
                entities_to_remove.append(entity_id)

        for entity_id in entities_to_remove:
            self._remove_entity(entity_id)

        return len(entities_to_remove)

    def _remove_entity(self, entity_id: str):
        """Remove an entity and its associated relationships."""
        if entity_id not in self.entities:
            return

        # Remove relationships
        relationships_to_remove = list(self.entity_relationships.get(entity_id, set()))
        for rel_id in relationships_to_remove:
            self._remove_relationship(rel_id)

        # Remove entity
        del self.entities[entity_id]
        del self.entity_relationships[entity_id]

        # Remove from database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM entities WHERE id = ?", (entity_id,))

    def _remove_relationship(self, relationship_id: str):
        """Remove a relationship."""
        if relationship_id not in self.relationships:
            return

        relationship = self.relationships[relationship_id]

        # Remove from entity relationship mappings
        self.entity_relationships[relationship.source_entity_id].discard(
            relationship_id
        )
        self.entity_relationships[relationship.target_entity_id].discard(
            relationship_id
        )

        # Remove relationship
        del self.relationships[relationship_id]

        # Remove from database
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("DELETE FROM relationships WHERE id = ?", (relationship_id,))
