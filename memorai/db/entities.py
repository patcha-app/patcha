"""Entity extraction from activities for knowledge graph construction."""

import re
import json
from typing import List, Dict, Set, Tuple, Optional
from datetime import datetime
from collections import defaultdict

from openai import OpenAI

from memorai.db.models import Event, EventType
from typing import Union
from memorai.db.models import (
    Entity, Relationship, EntityType, RelationshipType,
    EntityExtractionResult
)
from memorai.config import config


class EntityExtractor:
    """Extracts entities and relationships from activities for graph RAG."""

    def __init__(self):
        self.client = OpenAI(api_key=config.openai_api_key)
        self.model_name = "gpt-4o-mini"

        # Pre-defined technology patterns for quick recognition
        self.tech_patterns = {
            # Programming languages
            'python', 'javascript', 'typescript', 'java', 'rust', 'go', 'kotlin',
            'swift', 'c++', 'c#', 'php', 'ruby', 'scala', 'clojure',

            # Frameworks and libraries
            'react', 'vue', 'angular', 'svelte', 'django', 'flask', 'fastapi',
            'express', 'next.js', 'nuxt', 'spring', 'rails', 'laravel',
            'tensorflow', 'pytorch', 'keras', 'scikit-learn', 'pandas', 'numpy',

            # Tools and platforms
            'docker', 'kubernetes', 'git', 'github', 'gitlab', 'jenkins',
            'aws', 'gcp', 'azure', 'terraform', 'ansible', 'nginx',
            'redis', 'mongodb', 'postgresql', 'mysql', 'elasticsearch',

            # Concepts
            'api', 'rest', 'graphql', 'microservices', 'cicd', 'devops',
            'machine learning', 'deep learning', 'nlp', 'computer vision'
        }

        # Generic entity type patterns (no hardcoded brands)
        self.concept_patterns = {
            # Technology concepts
            'augmented reality', 'virtual reality', 'mixed reality', 'artificial intelligence',
            'machine learning', 'deep learning', 'computer vision', 'natural language processing',
            'blockchain', 'cryptocurrency', 'quantum computing', 'edge computing',

            # Product categories
            'smartphone', 'tablet', 'laptop', 'desktop', 'smart glasses', 'headset',
            'smartwatch', 'smart speaker', 'gaming console', 'smart tv',

            # Software concepts
            'operating system', 'web browser', 'mobile app', 'web app', 'desktop app',
            'database', 'vector database', 'graph database', 'search engine',

            # Development concepts
            'frontend', 'backend', 'fullstack', 'devops', 'microservices', 'serverless',
            'containerization', 'orchestration', 'continuous integration', 'deployment'
        }

        # Common project indicators
        self.project_indicators = {
            'project', 'repo', 'repository', 'app', 'application', 'service',
            'system', 'platform', 'website', 'tool', 'library', 'framework'
        }

    def extract_entities_from_event(self, event: Union[Event, dict]) -> EntityExtractionResult:
        """Extract entities and relationships from a single event."""
        try:
            # Combine different extraction methods
            rule_based_entities = self._extract_entities_rule_based(event)
            llm_result = self._extract_entities_llm(event)

            # Merge and deduplicate results
            all_entities = self._merge_entities(rule_based_entities, llm_result.entities)
            all_relationships = llm_result.relationships

            # Calculate overall confidence
            confidence = (len(rule_based_entities) * 0.8 + llm_result.confidence * 0.2) / max(1, len(all_entities))

            # Get event type for metadata
            if isinstance(event, dict):
                event_type_value = event.get('type', 'browser')
            else:
                event_type_value = event.type.value if hasattr(event.type, 'value') else str(event.type)

            return EntityExtractionResult(
                entities=all_entities,
                relationships=all_relationships,
                confidence=min(confidence, 1.0),
                extraction_method="hybrid",
                metadata={
                    "rule_based_count": len(rule_based_entities),
                    "llm_count": len(llm_result.entities),
                    "event_type": event_type_value
                }
            )

        except Exception as e:
            print(f"Error extracting entities from event: {e}")
            return EntityExtractionResult(
                entities=[],
                relationships=[],
                confidence=0.0,
                extraction_method="error",
                metadata={"error": str(e)}
            )

    def _extract_entities_rule_based(self, event: Union[Event, dict]) -> List[Entity]:
        """Extract entities using rule-based patterns."""
        entities = []
        text_content = self._get_searchable_text(event)

        # Get timestamp - handle both Event objects and dictionaries
        if isinstance(event, dict):
            timestamp_str = event.get('timestamp', datetime.now().isoformat())
            if isinstance(timestamp_str, str):
                timestamp = datetime.fromisoformat(timestamp_str.replace('Z', '+00:00'))
            else:
                timestamp = timestamp_str
            event_type = event.get('type', 'browser')
            project = event.get('project')
        else:
            timestamp = event.timestamp
            event_type = event.type.value if hasattr(event.type, 'value') else str(event.type)
            project = getattr(event, 'project', None)

        # Extract technologies
        for tech in self.tech_patterns:
            if self._fuzzy_match(tech, text_content):
                entity_id = f"tech_{tech.replace(' ', '_').replace('.', '_')}"
                entities.append(Entity(
                    id=entity_id,
                    name=tech,
                    type=EntityType.TECHNOLOGY,
                    confidence=0.9,
                    first_seen=timestamp,
                    last_seen=timestamp
                ))

        # Extract concepts (generic technology/product concepts)
        for concept in self.concept_patterns:
            if self._fuzzy_match(concept, text_content):
                entity_id = f"concept_{concept.replace(' ', '_').replace('.', '_')}"
                entities.append(Entity(
                    id=entity_id,
                    name=concept,
                    type=EntityType.CONCEPT,
                    confidence=0.8,
                    first_seen=timestamp,
                    last_seen=timestamp
                ))

        # Dynamic entity extraction: Extract capitalized words/phrases as potential entities
        # This will capture company names, product names, etc. without hardcoding
        capitalized_entities = self._extract_capitalized_entities(text_content, timestamp)
        entities.extend(capitalized_entities)

        # Extract project names from git activities
        if event_type in ['git_commit', 'git_stash'] and project:
            project_id = f"project_{project.replace(' ', '_').replace('/', '_')}"
            entities.append(Entity(
                id=project_id,
                name=project,
                type=EntityType.PROJECT,
                confidence=1.0,
                first_seen=timestamp,
                last_seen=timestamp
            ))

        # Extract file types and patterns
        file_patterns = re.findall(r'\b\w+\.(py|js|ts|java|rs|go|cpp|c|h|md|json|yaml|yml|sql|html|css|scss)\b', text_content.lower())
        for match in file_patterns:
            extension = match
            if extension in ['py', 'js', 'ts', 'java', 'rs', 'go', 'cpp']:
                # Map file extensions to technologies
                tech_mapping = {
                    'py': 'python', 'js': 'javascript', 'ts': 'typescript',
                    'java': 'java', 'rs': 'rust', 'go': 'go', 'cpp': 'c++'
                }
                if extension in tech_mapping:
                    tech_name = tech_mapping[extension]
                    entity_id = f"tech_{tech_name}"
                    entities.append(Entity(
                        id=entity_id,
                        name=tech_name,
                        type=EntityType.TECHNOLOGY,
                        confidence=0.8,
                        first_seen=timestamp,
                        last_seen=timestamp
                    ))

        return self._deduplicate_entities(entities)

    def _extract_capitalized_entities(self, text: str, timestamp: datetime) -> List[Entity]:
        """Extract capitalized words/phrases as potential entities (companies, products, etc.)."""
        entities = []

        # Find sequences of capitalized words (potential company/product names)
        # Pattern: One or more capitalized words, optionally with numbers
        capitalized_pattern = r'\b[A-Z][a-zA-Z0-9]*(?:\s+[A-Z][a-zA-Z0-9]*)*(?:\s+\d+)?(?:\s+[A-Z][a-zA-Z]*)*\b'

        matches = re.findall(capitalized_pattern, text)

        # Filter out common words and very short matches
        common_words = {
            'YouTube', 'Google', 'The', 'This', 'That', 'And', 'Or', 'But', 'In', 'On', 'At',
            'To', 'For', 'Of', 'With', 'By', 'From', 'About', 'Into', 'Through', 'During',
            'Before', 'After', 'Above', 'Below', 'Up', 'Down', 'Out', 'Off', 'Over', 'Under'
        }

        for match in matches:
            match = match.strip()

            # Skip if too short, common word, or already covered by tech patterns
            if (len(match) < 3 or
                match in common_words or
                match.lower() in self.tech_patterns or
                match.lower() in self.concept_patterns):
                continue

            # Determine entity type based on context clues
            entity_type = self._classify_capitalized_entity(match, text)

            entity_id = f"{entity_type.value}_{match.replace(' ', '_').replace('.', '_')}"
            entities.append(Entity(
                id=entity_id,
                name=match,
                type=entity_type,
                confidence=0.7,  # Lower confidence for dynamic extraction
                first_seen=timestamp,
                last_seen=timestamp
            ))

        return entities

    def _classify_capitalized_entity(self, entity_name: str, context: str) -> EntityType:
        """Classify a capitalized entity based on context clues."""
        entity_lower = entity_name.lower()
        context_lower = context.lower()

        # Check for company indicators
        company_indicators = ['inc', 'corp', 'ltd', 'llc', 'company', 'technologies', 'labs']
        if any(indicator in entity_lower for indicator in company_indicators):
            return EntityType.ORGANIZATION

        # Check for product indicators (numbers often indicate product models)
        if re.search(r'\d+', entity_name):
            product_indicators = ['pro', 'max', 'plus', 'air', 'mini', 'ultra']
            if any(indicator in entity_lower for indicator in product_indicators):
                return EntityType.CONCEPT  # Product/device

        # Check for event indicators
        event_indicators = ['conference', 'summit', 'keynote', 'event', 'expo', 'fest']
        if any(indicator in context_lower for indicator in event_indicators):
            return EntityType.CONCEPT  # Event/conference

        # Check for file/documentation indicators
        if any(ext in entity_lower for ext in ['.py', '.js', '.md', '.txt', '.json', '.xml']):
            return EntityType.FILE

        # Default to organization for capitalized entities (likely company/product names)
        return EntityType.ORGANIZATION

    def _extract_entities_llm(self, event: Union[Event, dict]) -> EntityExtractionResult:
        """Extract entities using LLM-based analysis."""
        try:
            text_content = self._get_searchable_text(event)

            # Get event details - handle both Event objects and dictionaries
            if isinstance(event, dict):
                event_type = event.get('type', 'browser')
                summary = event.get('summary', 'No summary')
                timestamp = event.get('timestamp', datetime.now().isoformat())
                if isinstance(timestamp, str):
                    timestamp = datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
            else:
                event_type = event.type.value if hasattr(event.type, 'value') else str(event.type)
                summary = getattr(event, 'summary', None) or 'No summary'
                timestamp = event.timestamp

            prompt = f"""
            Analyze this software development activity and extract entities and their relationships.

            Activity Type: {event_type}
            Summary: {summary}
            Content: {text_content[:1000]}

            Extract entities of these types:
            - TECHNOLOGY: Programming languages, frameworks, tools, libraries
            - PROJECT: Project names, repositories, applications
            - CONCEPT: Technical concepts, methodologies, patterns
            - FEATURE: Features being worked on
            - ISSUE: Problems, bugs, issues being addressed
            - FILE: Important files or directories mentioned

            Also identify relationships between entities:
            - USES: Someone uses a technology
            - WORKS_ON: Work on a project/feature
            - FIXES: Fixes an issue
            - PART_OF: Feature is part of project
            - RELATES_TO: General relationship

            Return JSON format:
            {{
                "entities": [
                    {{
                        "name": "entity_name",
                        "type": "TECHNOLOGY|PROJECT|CONCEPT|FEATURE|ISSUE|FILE",
                        "confidence": 0.0-1.0
                    }}
                ],
                "relationships": [
                    {{
                        "source": "entity1_name",
                        "target": "entity2_name",
                        "type": "USES|WORKS_ON|FIXES|PART_OF|RELATES_TO",
                        "confidence": 0.0-1.0
                    }}
                ]
            }}
            """

            response = self.client.chat.completions.create(
                model=self.model_name,
                messages=[{"role": "user", "content": prompt}],
            )
            result = self._parse_llm_response(response.choices[0].message.content, timestamp)

            return result

        except Exception as e:
            print(f"Error in LLM entity extraction: {e}")
            return EntityExtractionResult(
                entities=[],
                relationships=[],
                confidence=0.0,
                extraction_method="llm_error",
                metadata={"error": str(e)}
            )

    def _parse_llm_response(self, response_text: str, timestamp: datetime) -> EntityExtractionResult:
        """Parse LLM response and convert to entities/relationships."""
        try:
            # Extract JSON from response
            json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
            if not json_match:
                raise ValueError("No JSON found in response")

            data = json.loads(json_match.group())

            entities = []
            relationships = []

            # Parse entities
            for entity_data in data.get('entities', []):
                entity_type = EntityType(entity_data['type'].lower())
                entity_id = f"{entity_type.value}_{entity_data['name'].replace(' ', '_').replace('/', '_')}"

                entity = Entity(
                    id=entity_id,
                    name=entity_data['name'],
                    type=entity_type,
                    confidence=entity_data.get('confidence', 0.8),
                    first_seen=timestamp,
                    last_seen=timestamp
                )
                entities.append(entity)

            # Parse relationships
            entity_name_to_id = {e.name: e.id for e in entities}

            for rel_data in data.get('relationships', []):
                source_name = rel_data['source']
                target_name = rel_data['target']

                if source_name in entity_name_to_id and target_name in entity_name_to_id:
                    rel_type = RelationshipType(rel_data['type'].lower())
                    rel_id = f"{entity_name_to_id[source_name]}_{rel_type.value}_{entity_name_to_id[target_name]}"

                    relationship = Relationship(
                        id=rel_id,
                        source_entity_id=entity_name_to_id[source_name],
                        target_entity_id=entity_name_to_id[target_name],
                        relationship_type=rel_type,
                        confidence=rel_data.get('confidence', 0.8),
                        first_seen=timestamp,
                        last_seen=timestamp
                    )
                    relationships.append(relationship)

            # Calculate overall confidence
            entity_confidences = [e.confidence for e in entities]
            avg_confidence = sum(entity_confidences) / len(entity_confidences) if entity_confidences else 0.0

            return EntityExtractionResult(
                entities=entities,
                relationships=relationships,
                confidence=avg_confidence,
                extraction_method="llm"
            )

        except Exception as e:
            print(f"Error parsing LLM response: {e}")
            return EntityExtractionResult(
                entities=[],
                relationships=[],
                confidence=0.0,
                extraction_method="parse_error",
                metadata={"error": str(e), "raw_response": response_text[:500]}
            )

    def _get_searchable_text(self, event: Union[Event, dict]) -> str:
        """Get all searchable text from an event."""
        text_parts = []

        # Handle both Event objects and dictionaries
        if isinstance(event, dict):
            # Dictionary from vector store
            summary = event.get('summary', '')
            content = event.get('content', '') or event.get('raw_content', '')
            project = event.get('project', '')
            metadata = event.get('metadata', {})

            if summary:
                text_parts.append(summary)
            if content:
                text_parts.append(content)
            if project:
                text_parts.append(project)

            # Extract text from metadata
            if isinstance(metadata, dict):
                for key, value in metadata.items():
                    if isinstance(value, str):
                        text_parts.append(value)
        else:
            # Event object
            if hasattr(event, 'summary') and event.summary:
                text_parts.append(event.summary)

            if hasattr(event, 'raw_content') and event.raw_content:
                text_parts.append(event.raw_content)

            if hasattr(event, 'project') and event.project:
                text_parts.append(event.project)

            if hasattr(event, 'metadata') and event.metadata:
                # Extract text from metadata
                for key, value in event.metadata.items():
                    if isinstance(value, str):
                        text_parts.append(value)

        return ' '.join(text_parts)

    def _fuzzy_match(self, pattern: str, text: str) -> bool:
        """Check if pattern exists in text with fuzzy matching."""
        text_lower = text.lower()
        pattern_lower = pattern.lower()

        # Exact match
        if pattern_lower in text_lower:
            return True

        # Word boundary match
        if re.search(r'\b' + re.escape(pattern_lower) + r'\b', text_lower):
            return True

        # Handle common variations
        variations = {
            'javascript': ['js', 'node.js', 'nodejs'],
            'typescript': ['ts'],
            'python': ['py'],
            'docker': ['dockerfile', 'containerization'],
            'kubernetes': ['k8s', 'kube'],
            'machine learning': ['ml', 'ai', 'artificial intelligence']
        }

        for canonical, alts in variations.items():
            if pattern_lower == canonical:
                for alt in alts:
                    if alt in text_lower:
                        return True

        return False

    def _merge_entities(self, entities1: List[Entity], entities2: List[Entity]) -> List[Entity]:
        """Merge two lists of entities, handling duplicates."""
        entity_map = {}

        # Add entities from first list
        for entity in entities1:
            entity_map[entity.id] = entity

        # Add entities from second list, merging if duplicate
        for entity in entities2:
            if entity.id in entity_map:
                # Merge entities - keep higher confidence
                existing = entity_map[entity.id]
                if entity.confidence > existing.confidence:
                    entity_map[entity.id] = entity
                else:
                    # Update aliases
                    existing.aliases.add(entity.name)
            else:
                entity_map[entity.id] = entity

        return list(entity_map.values())

    def _deduplicate_entities(self, entities: List[Entity]) -> List[Entity]:
        """Remove duplicate entities from a list."""
        seen_ids = set()
        deduplicated = []

        for entity in entities:
            if entity.id not in seen_ids:
                deduplicated.append(entity)
                seen_ids.add(entity.id)

        return deduplicated

    def extract_entities_batch(self, events: List[Event]) -> List[EntityExtractionResult]:
        """Extract entities from multiple events in batch."""
        results = []

        for event in events:
            try:
                result = self.extract_entities_from_event(event)
                results.append(result)
            except Exception as e:
                print(f"Error processing event {event.timestamp}: {e}")
                # Add empty result to maintain order
                results.append(EntityExtractionResult(
                    entities=[],
                    relationships=[],
                    confidence=0.0,
                    extraction_method="batch_error",
                    metadata={"error": str(e)}
                ))

        return results
