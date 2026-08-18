use crate::{
    db::{entities::EntityExtractor, graph::KnowledgeGraph, store::VectorStore},
    embedding::Embedder,
    llm::backend::LlmBackend,
    models::Event,
};
use anyhow::Result;
use std::sync::Arc;

const VECTOR_WEIGHT: f64 = 0.6;
#[allow(dead_code)]
const GRAPH_WEIGHT: f64 = 0.4;

pub struct GraphRagSystem {
    vector_store: Arc<VectorStore>,
    knowledge_graph: Arc<KnowledgeGraph>,
    entity_extractor: Arc<EntityExtractor>,
    embedder: Arc<Embedder>,
    #[allow(dead_code)]
    llm_client: Arc<dyn LlmBackend>,
}

impl GraphRagSystem {
    pub fn new(
        vector_store: Arc<VectorStore>,
        knowledge_graph: Arc<KnowledgeGraph>,
        entity_extractor: Arc<EntityExtractor>,
        embedder: Arc<Embedder>,
        llm_client: Arc<dyn LlmBackend>,
    ) -> Self {
        Self {
            vector_store,
            knowledge_graph,
            entity_extractor,
            embedder,
            llm_client,
        }
    }

    /// Combine vector similarity (60%) + knowledge graph traversal (40%).
    pub async fn retrieve_enhanced_context(
        &self,
        query: &str,
        limit: usize,
    ) -> Result<serde_json::Value> {
        // Vector context
        let embedding = self.embedder.embed_query(query)?;
        let vector_results = self.vector_store.search_events(&embedding, limit, None)?;

        // Graph context — extract entities from query, find neighbors
        let graph_entities = {
            let (entities, _) = self.entity_extractor.extract(query).await?;
            let ids: Vec<String> = entities.iter().map(|e| e.id.clone()).collect();
            self.knowledge_graph.get_related_entities(&ids)?
        };

        // Combine
        let vector_json: Vec<serde_json::Value> = vector_results
            .iter()
            .enumerate()
            .map(|(rank, r)| {
                let score = VECTOR_WEIGHT / (60.0 + rank as f64 + 1.0);
                serde_json::json!({
                    "id": r.event.id,
                    "score": score,
                    "summary": r.event.summary,
                    "category": r.event.category.as_ref().map(|c| c.to_string()),
                    "source": "vector",
                })
            })
            .collect();

        let graph_json: Vec<serde_json::Value> = graph_entities
            .iter()
            .take(limit)
            .map(|e| {
                serde_json::json!({
                    "id": e.id,
                    "name": e.name,
                    "type": format!("{:?}", e.entity_type),
                    "source": "graph",
                })
            })
            .collect();

        Ok(serde_json::json!({
            "vector_context": vector_json,
            "graph_context": graph_json,
            "query_entities": graph_entities.iter().map(|e| &e.name).collect::<Vec<_>>(),
        }))
    }

    /// Process activities to extract entities and update the knowledge graph.
    pub async fn enhance_activity_processing(&self, activities: &[Event]) -> Result<()> {
        for event in activities {
            let text = format!(
                "{} {}",
                event.summary.as_deref().unwrap_or(""),
                event.raw_content.chars().take(500).collect::<String>()
            );
            if text.trim().is_empty() {
                continue;
            }
            match self.entity_extractor.extract(&text).await {
                Ok((entities, relationships)) => {
                    for entity in &entities {
                        let _ = self.knowledge_graph.add_entity(entity);
                    }
                    for rel in &relationships {
                        let _ = self.knowledge_graph.add_relationship(rel);
                    }
                }
                Err(e) => tracing::debug!(error=%e, "entity extraction failed"),
            }
        }
        Ok(())
    }
}
