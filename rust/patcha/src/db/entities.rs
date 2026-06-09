use crate::{
    config::Config,
    llm::client::PatchaApiClient,
    models::{Entity, EntityType, Relationship, RelationshipType},
};
use anyhow::Result;
use chrono::Utc;
use regex::Regex;
use std::sync::{Arc, OnceLock};
use uuid::Uuid;

// ---------------------------------------------------------------------------
// Known technology patterns (same list as Python)
// ---------------------------------------------------------------------------

const TECH_PATTERNS: &[&str] = &[
    "python", "rust", "javascript", "typescript", "go", "java", "kotlin",
    "swift", "c++", "c#", "ruby", "php", "scala", "haskell",
    "react", "vue", "angular", "svelte", "nextjs", "nuxt",
    "django", "flask", "fastapi", "express", "rails",
    "postgres", "mysql", "sqlite", "mongodb", "redis", "elasticsearch",
    "docker", "kubernetes", "terraform", "ansible",
    "aws", "gcp", "azure",
    "git", "github", "gitlab", "jira", "linear",
    "openai", "anthropic", "claude", "gpt", "llm",
    "numpy", "pandas", "pytorch", "tensorflow", "sklearn",
];

const CONCEPT_PATTERNS: &[&str] = &[
    "api", "rest", "graphql", "grpc", "websocket",
    "authentication", "authorization", "oauth", "jwt",
    "microservices", "monolith", "serverless",
    "ci/cd", "devops", "mlops",
    "testing", "debugging", "refactoring",
    "performance", "security", "scalability",
];

// ---------------------------------------------------------------------------
// EntityExtractor
// ---------------------------------------------------------------------------

pub struct EntityExtractor {
    #[allow(dead_code)]
    cfg: Config,
    llm_client: Arc<PatchaApiClient>,
}

impl EntityExtractor {
    pub fn new(cfg: Config, llm_client: Arc<PatchaApiClient>) -> Self {
        Self { cfg, llm_client }
    }

    pub async fn extract(&self, text: &str) -> Result<(Vec<Entity>, Vec<Relationship>)> {
        let mut entities = self.extract_rule_based(text);

        // Try LLM extraction if text is long enough to be meaningful
        if text.len() > 100 {
            if let Ok((llm_entities, llm_rels)) = self.extract_llm(text).await {
                let existing_names: std::collections::HashSet<String> =
                    entities.iter().map(|e| e.name.to_lowercase()).collect();
                for e in llm_entities {
                    if !existing_names.contains(&e.name.to_lowercase()) {
                        entities.push(e);
                    }
                }
                return Ok((entities, llm_rels));
            }
        }

        Ok((entities, Vec::new()))
    }

    // -----------------------------------------------------------------------
    // Rule-based extraction
    // -----------------------------------------------------------------------

    fn extract_rule_based(&self, text: &str) -> Vec<Entity> {
        let text_lower = text.to_lowercase();
        let mut entities = Vec::new();
        let now = Utc::now();

        // Known tech patterns
        for &tech in TECH_PATTERNS {
            if text_lower.contains(tech) {
                entities.push(Entity {
                    id: Uuid::new_v4().to_string(),
                    name: tech.to_owned(),
                    entity_type: EntityType::Technology,
                    aliases: Vec::new(),
                    confidence: 0.9,
                    first_seen: now,
                    last_seen: now,
                    mention_count: 1,
                    metadata: Default::default(),
                });
            }
        }

        // Concept patterns
        for &concept in CONCEPT_PATTERNS {
            if text_lower.contains(concept) {
                entities.push(Entity {
                    id: Uuid::new_v4().to_string(),
                    name: concept.to_owned(),
                    entity_type: EntityType::Concept,
                    aliases: Vec::new(),
                    confidence: 0.8,
                    first_seen: now,
                    last_seen: now,
                    mention_count: 1,
                    metadata: Default::default(),
                });
            }
        }

        // Capitalized words as potential project/org names
        let cap_re = capitalized_word_regex();
        for m in cap_re.find_iter(text) {
            let word = m.as_str();
            if word.len() >= 3 && !is_sentence_start_word(word) {
                entities.push(Entity {
                    id: Uuid::new_v4().to_string(),
                    name: word.to_owned(),
                    entity_type: EntityType::Project,
                    aliases: Vec::new(),
                    confidence: 0.5,
                    first_seen: now,
                    last_seen: now,
                    mention_count: 1,
                    metadata: Default::default(),
                });
            }
        }

        entities
    }

    // -----------------------------------------------------------------------
    // LLM-based extraction
    // -----------------------------------------------------------------------

    async fn extract_llm(&self, text: &str) -> Result<(Vec<Entity>, Vec<Relationship>)> {
        let prompt = format!(
            "Extract entities and relationships from this text as JSON.\n\
             Return: {{\"entities\": [{{\"name\": ..., \"type\": \"technology|project|person|concept\"}}], \
             \"relationships\": [{{\"source\": ..., \"target\": ..., \"type\": \"uses|works_on|relates_to\"}}]}}\n\n\
             Text: {}",
            &text[..text.len().min(500)]
        );

        let response = self
            .llm_client
            .chat_completion(
                "You are an entity extractor. Return only valid JSON.",
                &prompt,
                "gpt-4o-mini",
            )
            .await?;

        // Extract JSON from response
        let json_str = extract_json_block(&response);
        let parsed: serde_json::Value = serde_json::from_str(json_str)?;

        let now = Utc::now();
        let mut entities = Vec::new();

        if let Some(arr) = parsed.get("entities").and_then(|v| v.as_array()) {
            for item in arr {
                let name = item.get("name").and_then(|v| v.as_str()).unwrap_or("").to_owned();
                let type_str = item.get("type").and_then(|v| v.as_str()).unwrap_or("concept");
                if name.is_empty() { continue; }
                let entity_type = match type_str {
                    "technology" => EntityType::Technology,
                    "project" => EntityType::Project,
                    "person" => EntityType::Person,
                    "organization" => EntityType::Organization,
                    _ => EntityType::Concept,
                };
                entities.push(Entity {
                    id: Uuid::new_v4().to_string(),
                    name,
                    entity_type,
                    aliases: Vec::new(),
                    confidence: 0.8,
                    first_seen: now,
                    last_seen: now,
                    mention_count: 1,
                    metadata: Default::default(),
                });
            }
        }

        let mut relationships = Vec::new();
        if let Some(arr) = parsed.get("relationships").and_then(|v| v.as_array()) {
            for item in arr {
                let src = item.get("source").and_then(|v| v.as_str()).unwrap_or("").to_owned();
                let tgt = item.get("target").and_then(|v| v.as_str()).unwrap_or("").to_owned();
                let rel_type_str = item.get("type").and_then(|v| v.as_str()).unwrap_or("relates_to");
                if src.is_empty() || tgt.is_empty() { continue; }

                let rel_type = match rel_type_str {
                    "uses" => RelationshipType::Uses,
                    "works_on" => RelationshipType::WorksOn,
                    _ => RelationshipType::RelatesTo,
                };

                // Find entity IDs by name
                let src_id = entities.iter().find(|e| e.name == src).map(|e| e.id.clone());
                let tgt_id = entities.iter().find(|e| e.name == tgt).map(|e| e.id.clone());

                if let (Some(sid), Some(tid)) = (src_id, tgt_id) {
                    relationships.push(Relationship {
                        id: Uuid::new_v4().to_string(),
                        source_entity_id: sid,
                        target_entity_id: tid,
                        relationship_type: rel_type,
                        confidence: 0.7,
                        first_seen: now,
                        last_seen: now,
                        strength: 1.0,
                        supporting_activity_ids: Vec::new(),
                        metadata: Default::default(),
                    });
                }
            }
        }

        Ok((entities, relationships))
    }
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

fn capitalized_word_regex() -> &'static Regex {
    static RE: OnceLock<Regex> = OnceLock::new();
    RE.get_or_init(|| Regex::new(r"\b[A-Z][a-z]{2,}\b").unwrap())
}

fn is_sentence_start_word(word: &str) -> bool {
    // Common words that appear capitalized but aren't entity names
    const SKIP: &[&str] = &["The", "This", "That", "It", "We", "They", "He", "She", "You", "I", "And", "But", "Or", "So"];
    SKIP.contains(&word)
}

fn extract_json_block(text: &str) -> &str {
    if let Some(start) = text.find('{') {
        if let Some(end) = text.rfind('}') {
            return &text[start..=end];
        }
    }
    text
}
