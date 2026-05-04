"""Graph-enhanced RAG system that combines vector search with knowledge graph traversal."""

from typing import List, Dict, Any, Optional
from datetime import datetime, date
import numpy as np

from openai import OpenAI

from patcha.db.models import Event
from patcha.db.store import VectorStore
from patcha.process import EventPreprocessor
from patcha.db.graph import KnowledgeGraph
from patcha.db.entities import EntityExtractor
from patcha.db.models import Entity, Relationship, GraphContext
from patcha.config import config


class GraphRAGSystem:
    """
    Enhanced RAG system that combines vector similarity search with
    knowledge graph traversal for richer contextual understanding.
    """

    def __init__(
        self,
        vector_store: VectorStore,
        preprocessor: EventPreprocessor,
        knowledge_graph: Optional[KnowledgeGraph] = None,
        entity_extractor: Optional[EntityExtractor] = None,
    ):
        self.vector_store = vector_store
        self.preprocessor = preprocessor
        self.knowledge_graph = knowledge_graph or KnowledgeGraph()
        self.entity_extractor = entity_extractor or EntityExtractor()

        self.client = OpenAI(api_key=config.openai_api_key)
        self.model_name = "gpt-4o-mini"

    def retrieve_enhanced_context(
        self,
        query_activities: List[Event],
        context_limit: int = 15,
        vector_weight: float = 0.6,
        graph_weight: float = 0.4,
    ) -> Dict[str, Any]:
        """
        Retrieve context using both vector similarity and graph relationships.

        Args:
            query_activities: Activities to find context for
            context_limit: Maximum number of context items
            vector_weight: Weight for vector similarity results
            graph_weight: Weight for graph relationship results

        Returns:
            Enhanced context combining vector and graph results
        """
        if not query_activities:
            return self._empty_context()

        # Extract entities from query activities
        query_entities = self._extract_query_entities(query_activities)

        # Get vector-based context
        vector_context = self._get_vector_context(query_activities, context_limit)

        # Get graph-based context
        graph_context = self._get_graph_context(query_entities, context_limit)

        # Combine and rank results
        combined_context = self._combine_contexts(
            vector_context, graph_context, vector_weight, graph_weight, context_limit
        )

        return combined_context

    def _extract_query_entities(self, activities: List[Event]) -> List[Entity]:
        """Extract entities from query activities."""
        query_entities = []

        for activity in activities:
            try:
                # Handle both Event objects and dictionaries
                if isinstance(activity, dict):
                    # Convert dictionary to Event-like structure for entity extraction
                    extraction_result = (
                        self.entity_extractor.extract_entities_from_event(activity)
                    )
                else:
                    # Regular Event object
                    extraction_result = (
                        self.entity_extractor.extract_entities_from_event(activity)
                    )

                query_entities.extend(extraction_result.entities)
            except Exception as e:
                print(f"Error extracting entities from query activity: {e}")

        return query_entities

    def _get_vector_context(
        self, activities: List[Event], limit: int
    ) -> Dict[str, Any]:
        """Get context using traditional vector similarity search."""
        try:
            # Generate composite embedding for activities
            composite_embedding = self._generate_composite_embedding(activities)
            if not composite_embedding:
                return {"vector_results": [], "vector_strength": 0.0}

            # Search vector store
            vector_results = self.vector_store.search_events(
                query_vector=composite_embedding,
                limit=limit * 2,  # Get more to allow for graph filtering
            )

            return {
                "vector_results": vector_results,
                "vector_strength": len(vector_results) / (limit * 2),
            }

        except Exception as e:
            print(f"Error in vector context retrieval: {e}")
            return {"vector_results": [], "vector_strength": 0.0}

    def _get_graph_context(
        self, query_entities: List[Entity], limit: int
    ) -> GraphContext:
        """Get context using knowledge graph traversal."""
        if not query_entities:
            return GraphContext(
                query_entities=[],
                related_entities=[],
                relationships=[],
                paths=[],
                context_strength=0.0,
            )

        try:
            # Add query entities to knowledge graph (in case they're new)
            for entity in query_entities:
                self.knowledge_graph.add_entity(entity)

            # Get related entities through graph traversal
            entity_ids = [e.id for e in query_entities]
            related_entities_with_scores = self.knowledge_graph.get_related_entities(
                entity_ids, max_results=limit
            )

            related_entities = [
                entity for entity, score in related_entities_with_scores
            ]

            # Get relationships between query entities and related entities
            all_entity_ids = entity_ids + [e.id for e in related_entities]
            relationships = self._get_relevant_relationships(all_entity_ids)

            # Find interesting paths between entities
            paths = self._find_interesting_paths(query_entities, related_entities)

            # Calculate context strength
            context_strength = self._calculate_graph_context_strength(
                query_entities, related_entities, relationships, paths
            )

            return GraphContext(
                query_entities=query_entities,
                related_entities=related_entities,
                relationships=relationships,
                paths=paths,
                context_strength=context_strength,
            )

        except Exception as e:
            print(f"Error in graph context retrieval: {e}")
            return GraphContext(
                query_entities=query_entities,
                related_entities=[],
                relationships=[],
                paths=[],
                context_strength=0.0,
            )

    def _get_relevant_relationships(self, entity_ids: List[str]) -> List[Relationship]:
        """Get relationships between the given entities."""
        relevant_relationships = []
        entity_id_set = set(entity_ids)

        for relationship in self.knowledge_graph.relationships.values():
            if (
                relationship.source_entity_id in entity_id_set
                and relationship.target_entity_id in entity_id_set
            ):
                relevant_relationships.append(relationship)

        # Sort by strength and confidence
        relevant_relationships.sort(
            key=lambda r: r.strength * r.confidence, reverse=True
        )

        return relevant_relationships[:10]  # Limit to top 10

    def _find_interesting_paths(
        self, query_entities: List[Entity], related_entities: List[Entity]
    ) -> List:
        """Find interesting paths between query entities and related entities."""
        paths = []

        # Find paths between query entities and top related entities
        for query_entity in query_entities[:3]:  # Limit query entities
            for related_entity in related_entities[:5]:  # Limit related entities
                entity_paths = self.knowledge_graph.find_paths(
                    query_entity.id, related_entity.id, max_depth=3
                )
                paths.extend(entity_paths)

        # Sort paths by strength and limit
        paths.sort(key=lambda p: p.total_strength, reverse=True)
        return paths[:5]

    def _calculate_graph_context_strength(
        self,
        query_entities: List[Entity],
        related_entities: List[Entity],
        relationships: List[Relationship],
        paths: List,
    ) -> float:
        """Calculate the strength/quality of graph context."""
        if not query_entities:
            return 0.0

        # Factors that contribute to context strength
        entity_factor = len(related_entities) / 10.0  # Normalize to 0-1
        relationship_factor = len(relationships) / 10.0
        path_factor = len(paths) / 5.0

        # Average relationship confidence
        if relationships:
            avg_rel_confidence = sum(r.confidence for r in relationships) / len(
                relationships
            )
        else:
            avg_rel_confidence = 0.0

        # Combine factors
        strength = (
            entity_factor * 0.3
            + relationship_factor * 0.3
            + path_factor * 0.2
            + avg_rel_confidence * 0.2
        )

        return min(strength, 1.0)

    def _combine_contexts(
        self,
        vector_context: Dict[str, Any],
        graph_context: GraphContext,
        vector_weight: float,
        graph_weight: float,
        limit: int,
    ) -> Dict[str, Any]:
        """Combine vector and graph contexts into unified result."""
        # Get vector results
        vector_results = vector_context.get("vector_results", [])
        vector_strength = vector_context.get("vector_strength", 0.0)

        # Convert graph context to activity results
        graph_results = self._graph_context_to_activities(graph_context)

        # Score and rank all results
        scored_results = []

        # Score vector results
        for i, result in enumerate(vector_results):
            score = vector_weight * (1.0 - i / len(vector_results)) * vector_strength
            scored_results.append(
                {"activity": result, "score": score, "source": "vector", "rank": i}
            )

        # Score graph results
        for i, result in enumerate(graph_results):
            score = (
                graph_weight
                * (1.0 - i / len(graph_results))
                * graph_context.context_strength
            )
            scored_results.append(
                {"activity": result, "score": score, "source": "graph", "rank": i}
            )

        # Sort by combined score and remove duplicates
        scored_results.sort(key=lambda x: x["score"], reverse=True)
        unique_results = self._deduplicate_activities(scored_results)

        # Take top results
        final_results = unique_results[:limit]

        return {
            "activities": [r["activity"] for r in final_results],
            "graph_context": graph_context,
            "vector_strength": vector_strength,
            "graph_strength": graph_context.context_strength,
            "combined_score": sum(r["score"] for r in final_results)
            / len(final_results)
            if final_results
            else 0.0,
            "source_breakdown": {
                "vector": len([r for r in final_results if r["source"] == "vector"]),
                "graph": len([r for r in final_results if r["source"] == "graph"]),
            },
        }

    def _graph_context_to_activities(self, graph_context: GraphContext) -> List[Event]:
        """Convert graph context back to relevant activities."""
        # This is a simplified implementation
        # In practice, you'd want to query activities that mention the related entities
        activities = []

        # Get activities from vector store that mention related entities
        for entity in graph_context.related_entities[:5]:
            try:
                # Search for activities mentioning this entity
                entity_embedding = self.preprocessor.generate_embedding(entity.name)
                if entity_embedding:
                    entity_activities = self.vector_store.search_events(
                        query_vector=entity_embedding, limit=3
                    )
                    activities.extend(entity_activities)
            except Exception as e:
                print(f"Error retrieving activities for entity {entity.name}: {e}")

        return activities[:10]  # Limit results

    def _deduplicate_activities(self, scored_results: List[Dict]) -> List[Dict]:
        """Remove duplicate activities from scored results."""
        seen_activities = set()
        unique_results = []

        for result in scored_results:
            activity = result["activity"]
            # Create a simple hash for deduplication
            activity_hash = (
                f"{activity.get('type', '')}_{activity.get('timestamp', '')}"
            )

            if activity_hash not in seen_activities:
                seen_activities.add(activity_hash)
                unique_results.append(result)

        return unique_results

    def _generate_composite_embedding(
        self, activities: List[Event]
    ) -> Optional[List[float]]:
        """Generate a composite embedding representing multiple activities."""
        embeddings = []
        weights = []

        for activity in activities:
            if hasattr(activity, "embedding") and activity.embedding:
                embeddings.append(activity.embedding)
                # Weight more recent activities higher
                age_hours = (datetime.now() - activity.timestamp).total_seconds() / 3600
                weight = max(0.1, 1.0 - (age_hours / (24 * 7)))  # Decay over a week
                weights.append(weight)

        if not embeddings:
            # Generate embeddings for activities that don't have them
            combined_text = " ".join(
                [
                    activity.summary or activity.raw_content[:200] or ""
                    for activity in activities
                ]
            )
            if combined_text.strip():
                return self.preprocessor.generate_embedding(combined_text)
            return None

        # Calculate weighted average
        if len(weights) != len(embeddings):
            weights = [1.0] * len(embeddings)

        weighted_embedding = np.average(embeddings, axis=0, weights=weights)
        return weighted_embedding.tolist()

    def _empty_context(self) -> Dict[str, Any]:
        """Return empty context structure."""
        return {
            "activities": [],
            "graph_context": GraphContext(
                query_entities=[],
                related_entities=[],
                relationships=[],
                paths=[],
                context_strength=0.0,
            ),
            "vector_strength": 0.0,
            "graph_strength": 0.0,
            "combined_score": 0.0,
            "source_breakdown": {"vector": 0, "graph": 0},
        }

    def enhance_activity_processing(self, activities: List[Event]) -> List[Event]:
        """Process activities and add them to the knowledge graph."""
        enhanced_activities = []

        for activity in activities:
            try:
                # Extract entities and relationships (handles both Event objects and dicts)
                extraction_result = self.entity_extractor.extract_entities_from_event(
                    activity
                )

                # Generate activity ID - handle both Event objects and dictionaries
                if isinstance(activity, dict):
                    activity_type = activity.get("type", "browser")
                    timestamp_str = activity.get(
                        "timestamp", datetime.now().isoformat()
                    )
                    activity_id = f"{activity_type}_{timestamp_str}"
                else:
                    activity_id = (
                        f"{activity.type.value}_{activity.timestamp.isoformat()}"
                    )

                # Add to knowledge graph
                self.knowledge_graph.add_extraction_result(
                    extraction_result, activity_id
                )

                # Store extraction results in activity metadata
                if isinstance(activity, dict):
                    # For dictionaries, add metadata directly
                    if "metadata" not in activity:
                        activity["metadata"] = {}
                    activity["metadata"]["entities"] = [
                        {
                            "id": e.id,
                            "name": e.name,
                            "type": e.type.value,
                            "confidence": e.confidence,
                        }
                        for e in extraction_result.entities
                    ]
                    activity["metadata"]["relationships"] = [
                        {
                            "source": r.source_entity_id,
                            "target": r.target_entity_id,
                            "type": r.relationship_type.value,
                            "confidence": r.confidence,
                        }
                        for r in extraction_result.relationships
                    ]
                else:
                    # For Event objects
                    if not hasattr(activity, "metadata") or activity.metadata is None:
                        activity.metadata = {}

                    activity.metadata["entities"] = [
                        {
                            "id": e.id,
                            "name": e.name,
                            "type": e.type.value,
                            "confidence": e.confidence,
                        }
                        for e in extraction_result.entities
                    ]

                    activity.metadata["relationships"] = [
                        {
                            "source": r.source_entity_id,
                            "target": r.target_entity_id,
                            "type": r.relationship_type.value,
                            "confidence": r.confidence,
                        }
                        for r in extraction_result.relationships
                    ]

                enhanced_activities.append(activity)

            except Exception as e:
                print(f"Error enhancing activity: {e}")
                enhanced_activities.append(activity)  # Add without enhancement

        return enhanced_activities

    def generate_enhanced_summary_with_graph(
        self, activities: List[Event], target_date: Optional[date] = None
    ) -> str:
        """Generate a summary enhanced with knowledge graph context."""
        if not activities:
            return "No activities found for the specified period."

        try:
            # Get enhanced context
            context = self.retrieve_enhanced_context(activities, context_limit=10)
            graph_context = context["graph_context"]

            # Prepare context for summarization
            activity_summaries = [
                activity.summary or "No summary" for activity in activities
            ]

            # Build graph context text
            graph_context_text = graph_context.to_text_context()

            # Generate enhanced summary
            prompt = f"""
            Generate a comprehensive daily summary enhanced with relationship context.

            Activities for {target_date or "today"}:
            {"; ".join(activity_summaries)}

            Knowledge Graph Context:
            {graph_context_text}

            Focus on:
            1. Main accomplishments and tasks
            2. Technology and project relationships discovered
            3. Learning progressions and skill development
            4. Patterns and connections between different activities

            Generate a coherent narrative that weaves together the activities with their
            broader context and relationships.
            """

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            return response.choices[0].message.content.strip()

        except Exception as e:
            print(f"Error generating enhanced summary: {e}")
            # Fallback to basic summary
            activity_summaries = [
                activity.summary or "Activity completed" for activity in activities
            ]
            return f"Completed {len(activities)} activities: {'; '.join(activity_summaries[:5])}"

    def get_knowledge_graph_insights(self) -> Dict[str, Any]:
        """Get insights about the current knowledge graph."""
        stats = self.knowledge_graph.get_graph_stats()

        # Get most connected entities
        entity_connections = {}
        for entity_id, entity in self.knowledge_graph.entities.items():
            connection_count = len(
                self.knowledge_graph.entity_relationships.get(entity_id, set())
            )
            entity_connections[entity_id] = connection_count

        most_connected = sorted(
            entity_connections.items(), key=lambda x: x[1], reverse=True
        )[:10]

        # Get most recent entities
        recent_entities = sorted(
            self.knowledge_graph.entities.values(),
            key=lambda e: e.last_seen,
            reverse=True,
        )[:10]

        return {
            "stats": stats,
            "most_connected_entities": [
                {
                    "name": self.knowledge_graph.entities[eid].name,
                    "type": self.knowledge_graph.entities[eid].type.value,
                    "connections": count,
                }
                for eid, count in most_connected
            ],
            "recent_entities": [
                {
                    "name": e.name,
                    "type": e.type.value,
                    "last_seen": e.last_seen.isoformat(),
                    "mentions": e.mention_count,
                }
                for e in recent_entities
            ],
        }
