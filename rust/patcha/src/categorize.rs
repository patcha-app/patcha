use crate::{
    db::store::VectorStore,
    llm::backend::LlmBackend,
    models::{Category, Event},
};
use std::collections::HashMap;
use std::str::FromStr;
use std::sync::Arc;

pub struct EnhancedCategorizer {
    vector_store: Arc<VectorStore>,
    llm_client: Arc<dyn LlmBackend>,
    model: String,
}

impl EnhancedCategorizer {
    pub fn new(vector_store: Arc<VectorStore>, llm_client: Arc<dyn LlmBackend>) -> Self {
        Self {
            vector_store,
            llm_client,
            model: "gpt-4o-mini".to_owned(),
        }
    }

    // -----------------------------------------------------------------------
    // RAG-based categorization
    // -----------------------------------------------------------------------

    pub async fn categorize_with_rag(
        &self,
        event: &Event,
        k: usize,
        confidence_threshold: f32,
    ) -> (Category, f32, serde_json::Value) {
        let emb = match &event.embedding {
            Some(e) => e,
            None => {
                return (
                    Category::Other,
                    0.0,
                    serde_json::json!({"method":"fallback","reason":"no_embedding"}),
                );
            }
        };

        let similar = match self.vector_store.search_events(emb, k, None) {
            Ok(s) => s,
            Err(_) => {
                return (
                    Category::Other,
                    0.0,
                    serde_json::json!({"method":"fallback","reason":"search_failed"}),
                );
            }
        };

        if similar.is_empty() {
            let ai_cat = self.categorize_with_llm(event, &[], &[]).await;
            return (
                ai_cat,
                0.5,
                serde_json::json!({"method":"ai_fallback","reason":"no_similar_events"}),
            );
        }

        // Compute weighted category vote
        let mut category_weights: HashMap<String, f32> = HashMap::new();
        let mut total_weight = 0.0f32;
        let mut similar_summaries = Vec::new();

        for scored in &similar {
            if let Some(cat) = &scored.event.category {
                let cat_str = cat.to_string();
                *category_weights.entry(cat_str).or_insert(0.0) += scored.score as f32;
                total_weight += scored.score as f32;
            }
            if let Some(ref summary) = scored.event.summary {
                similar_summaries.push(summary.clone());
            }
        }

        if category_weights.is_empty() {
            let ai_cat = self
                .categorize_with_llm(event, &similar_summaries, &[])
                .await;
            return (
                ai_cat,
                0.5,
                serde_json::json!({"method":"ai_fallback","reason":"no_categories_in_similar"}),
            );
        }

        if total_weight > 0.0 {
            for v in category_weights.values_mut() {
                *v /= total_weight;
            }
        }

        let (best_cat_str, base_confidence) = category_weights
            .iter()
            .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())
            .map(|(k, v)| (k.clone(), *v))
            .unwrap();

        // Agreement boost: fraction of similar events with the majority category
        let majority_count = similar
            .iter()
            .filter(|s| {
                s.event.category.as_ref().map(|c| c.to_string()) == Some(best_cat_str.clone())
            })
            .count() as f32;
        let agreement_boost = (majority_count / similar.len() as f32 * 0.2).min(0.2);
        let confidence = base_confidence + agreement_boost;

        let predicted = Category::from_str(&best_cat_str).unwrap_or(Category::Other);

        if confidence >= confidence_threshold {
            return (
                predicted,
                confidence,
                serde_json::json!({
                    "method": "rag",
                    "similar_events_count": similar.len(),
                    "confidence_breakdown": {
                        "base_confidence": base_confidence,
                        "agreement_boost": agreement_boost,
                    }
                }),
            );
        }

        // Low confidence — enhance with LLM
        let suggested: Vec<String> = category_weights.keys().cloned().collect();
        let ai_cat = self
            .categorize_with_llm(
                event,
                &similar_summaries[..similar_summaries.len().min(3)],
                &suggested,
            )
            .await;
        (
            ai_cat,
            confidence + 0.3,
            serde_json::json!({
                "method": "rag_enhanced_ai",
                "rag_confidence": confidence,
                "similar_categories": suggested,
            }),
        )
    }

    pub async fn batch_categorize(
        &self,
        events: &mut [Event],
        k: usize,
        confidence_threshold: f32,
    ) {
        // Process sequentially to avoid owning issues; parallelism via caller if needed
        for event in events.iter_mut() {
            let (cat, _, _) = self
                .categorize_with_rag(event, k, confidence_threshold)
                .await;
            event.category = Some(cat);
        }
    }

    // -----------------------------------------------------------------------
    // LLM fallback
    // -----------------------------------------------------------------------

    async fn categorize_with_llm(
        &self,
        event: &Event,
        similar_summaries: &[String],
        suggested: &[String],
    ) -> Category {
        let mut context = String::new();
        if !similar_summaries.is_empty() {
            context.push_str("\n\nSimilar activities:\n");
            for s in similar_summaries {
                context.push_str(&format!("- {s}\n"));
            }
        }
        if !suggested.is_empty() {
            context.push_str(&format!(
                "\nBased on similar activities, likely categories: {}\n",
                suggested.join(", ")
            ));
        }

        let prompt = format!(
            "Classify this activity into ONE of: Coding, Debugging, Research, Learning, Writing, Communication, Other.\n\
             Activity type: {event_type}\nContent: {content}\n{context}\n\
             Respond with only the category name.",
            event_type = event.event_type,
            content = &event.raw_content[..event.raw_content.len().min(200)],
        );

        match self
            .llm_client
            .chat_completion("You are a precise classifier.", &prompt, &self.model)
            .await
        {
            Ok(resp) => Category::from_str(resp.trim()).unwrap_or(Category::Other),
            Err(_) => rule_based_category(event),
        }
    }
}

// ---------------------------------------------------------------------------
// Rule-based fallback (no LLM, no vector store)
// ---------------------------------------------------------------------------

pub fn rule_based_category(event: &Event) -> Category {
    use crate::models::EventType;
    match event.event_type {
        EventType::GitCommit | EventType::GitStash | EventType::GitStaged => Category::Coding,
        EventType::Terminal => {
            let raw = event.raw_content.to_lowercase();
            if raw.contains("test") || raw.contains("debug") {
                Category::Debugging
            } else {
                Category::Coding
            }
        }
        EventType::Browser => {
            let raw = event.raw_content.to_lowercase();
            if raw.contains("stackoverflow")
                || raw.contains("docs.")
                || raw.contains("github.com")
                || raw.contains("wikipedia")
            {
                Category::Research
            } else {
                Category::Other
            }
        }
        EventType::Window | EventType::Screen => Category::Other,
        EventType::Note => Category::Writing,
    }
}
