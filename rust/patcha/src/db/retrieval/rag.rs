use crate::{db::store::VectorStore, embedding::Embedder, llm::backend::LlmBackend, models::Event};
use anyhow::Result;
use std::sync::Arc;

pub struct RagSystem {
    vector_store: Arc<VectorStore>,
    #[allow(dead_code)]
    embedder: Arc<Embedder>,
    #[allow(dead_code)]
    llm_client: Arc<dyn LlmBackend>,
}

impl RagSystem {
    pub fn new(
        vector_store: Arc<VectorStore>,
        embedder: Arc<Embedder>,
        llm_client: Arc<dyn LlmBackend>,
    ) -> Self {
        Self {
            vector_store,
            embedder,
            llm_client,
        }
    }

    /// Retrieve similar activities and workflow patterns for task analysis context.
    pub fn retrieve_context_for_task_analysis(
        &self,
        activities: &[Event],
        context_limit: usize,
    ) -> Result<serde_json::Value> {
        let composite_emb = self.composite_embedding(activities);
        if composite_emb.is_empty() {
            return Ok(serde_json::json!({"similar_activities": [], "patterns": []}));
        }

        let similar = self
            .vector_store
            .search_events(&composite_emb, context_limit, None)?;

        let similar_json: Vec<serde_json::Value> = similar
            .iter()
            .map(|r| {
                serde_json::json!({
                    "summary": r.event.summary,
                    "category": r.event.category.as_ref().map(|c| c.to_string()),
                    "project": r.event.project,
                    "score": r.score,
                })
            })
            .collect();

        Ok(serde_json::json!({
            "similar_activities": similar_json,
            "activity_count": activities.len(),
        }))
    }

    /// Return recent activity context as formatted text for LLM prompts.
    pub fn retrieve_context_for_summary(
        &self,
        activities: &[Event],
        context_limit: usize,
    ) -> Result<String> {
        let ctx = self.retrieve_context_for_task_analysis(activities, context_limit)?;
        let similar = ctx["similar_activities"]
            .as_array()
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v["summary"].as_str())
                    .take(5)
                    .map(|s| format!("- {s}"))
                    .collect::<Vec<_>>()
                    .join("\n")
            })
            .unwrap_or_default();

        if similar.is_empty() {
            return Ok(String::new());
        }

        Ok(format!(
            "Historical context (similar past work):\n{similar}"
        ))
    }

    // -----------------------------------------------------------------------
    // Helpers
    // -----------------------------------------------------------------------

    fn composite_embedding(&self, activities: &[Event]) -> Vec<f32> {
        let embeddings: Vec<&Vec<f32>> = activities
            .iter()
            .filter_map(|a| a.embedding.as_ref())
            .collect();

        if embeddings.is_empty() {
            return Vec::new();
        }

        let dim = embeddings[0].len();
        let mut sum = vec![0.0f32; dim];
        for emb in &embeddings {
            for (s, &v) in sum.iter_mut().zip(emb.iter()) {
                *s += v;
            }
        }
        let n = embeddings.len() as f32;
        sum.iter_mut().for_each(|v| *v /= n);
        sum
    }
}
