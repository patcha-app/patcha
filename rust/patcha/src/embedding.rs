use crate::config::Config;
use anyhow::{Context, Result};
use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};
use tiktoken_rs::cl100k_base;

// Token safety factor: bge WordPiece tokens run higher than cl100k for the same
// text, so we apply a 0.8 budget to avoid overflowing the model's native limit.
const TOKEN_SAFETY: f64 = 0.8;

const MODEL_MAX_TOKENS: &[(&str, usize)] = &[
    ("BAAI/bge-base-en-v1.5", 512),
    ("BAAI/bge-small-en-v1.5", 512),
];

// ---------------------------------------------------------------------------
// Embedder
// ---------------------------------------------------------------------------

pub struct Embedder {
    model: TextEmbedding,
    /// Effective token budget for chunking (accounts for safety margin).
    pub effective_max_tokens: usize,
    pub chunk_overlap: usize,
    /// Instruction prepended to user queries (BGE asymmetric retrieval).
    query_prefix: String,
}

impl Embedder {
    pub fn new(cfg: &Config) -> Result<Self> {
        let model_enum = resolve_model(&cfg.embedding_model_name);

        let init_opts = InitOptions::new(model_enum)
            .with_cache_dir(cfg.embedding_cache_dir.clone())
            .with_show_download_progress(true);

        let model = TextEmbedding::try_new(init_opts)
            .context("failed to load fastembed model")?;

        let native_limit = MODEL_MAX_TOKENS
            .iter()
            .find(|(name, _)| *name == cfg.embedding_model_name.as_str())
            .map(|(_, lim)| *lim)
            .unwrap_or(512);

        let budget = ((native_limit as f64) * TOKEN_SAFETY) as usize;
        let effective = budget
            .min(cfg.max_embedding_tokens)
            .max(cfg.embedding_chunk_overlap + 1);

        Ok(Self {
            model,
            effective_max_tokens: effective,
            chunk_overlap: cfg.embedding_chunk_overlap,
            query_prefix: cfg.query_instruction_prefix.clone(),
        })
    }

    /// Embed a single document text; returns a 768-dim float vector.
    /// Use this for stored content and document-to-document similarity — it does
    /// NOT apply the query instruction prefix.
    pub fn embed_one(&self, text: &str) -> Result<Vec<f32>> {
        let mut results = self.model.embed(vec![text.to_owned()], None)
            .context("fastembed embed_one failed")?;
        Ok(results.remove(0))
    }

    /// Embed a user search query. Prepends the BGE instruction prefix so the
    /// query lands in the same region of vector space as the matching documents.
    pub fn embed_query(&self, query: &str) -> Result<Vec<f32>> {
        let prefixed = format!("{}{}", self.query_prefix, query);
        let mut results = self.model.embed(vec![prefixed], None)
            .context("fastembed embed_query failed")?;
        Ok(results.remove(0))
    }

    /// Embed a batch of texts. The returned Vec has the same length as `texts`.
    pub fn embed_many(&self, texts: Vec<String>) -> Result<Vec<Vec<f32>>> {
        self.model.embed(texts, None).context("fastembed embed_many failed")
    }
}

fn resolve_model(name: &str) -> EmbeddingModel {
    match name {
        "BAAI/bge-base-en-v1.5" => EmbeddingModel::BGEBaseENV15,
        "BAAI/bge-small-en-v1.5" => EmbeddingModel::BGESmallENV15,
        _ => EmbeddingModel::BGEBaseENV15,
    }
}

// ---------------------------------------------------------------------------
// Text chunking (tiktoken cl100k_base, same as Python)
// ---------------------------------------------------------------------------

/// Split `text` into overlapping token-bounded chunks.
/// Overlap keeps adjacent chunks close in vector space.
pub fn chunk_text(text: &str, max_tokens: usize, overlap: usize) -> Result<Vec<String>> {
    assert!(overlap < max_tokens, "overlap must be less than max_tokens");

    let bpe = cl100k_base().context("failed to init tiktoken cl100k_base")?;
    let tokens = bpe.encode_with_special_tokens(text);

    if tokens.len() <= max_tokens {
        return Ok(vec![text.to_owned()]);
    }

    let step = max_tokens - overlap;
    let mut chunks = Vec::new();
    let mut start = 0usize;

    while start < tokens.len() {
        let end = (start + max_tokens).min(tokens.len());
        let chunk_tokens = tokens[start..end].to_vec();
        // decode_bytes is fallible; fall back to lossy utf8 on error
        let chunk = bpe.decode(chunk_tokens)?;
        chunks.push(chunk);
        if end == tokens.len() {
            break;
        }
        start += step;
    }

    Ok(chunks)
}

// ---------------------------------------------------------------------------
// Cosine similarity
// ---------------------------------------------------------------------------

pub fn cosine_similarity(a: &[f32], b: &[f32]) -> f32 {
    let dot: f32 = a.iter().zip(b.iter()).map(|(x, y)| x * y).sum();
    let norm_a: f32 = a.iter().map(|x| x * x).sum::<f32>().sqrt();
    let norm_b: f32 = b.iter().map(|x| x * x).sum::<f32>().sqrt();
    if norm_a == 0.0 || norm_b == 0.0 {
        0.0
    } else {
        (dot / (norm_a * norm_b)).clamp(-1.0, 1.0)
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn chunk_text_short_is_passthrough() {
        let chunks = chunk_text("hello world", 512, 100).unwrap();
        assert_eq!(chunks, vec!["hello world"]);
    }

    #[test]
    fn chunk_text_splits_long_text() {
        // Build a text that's clearly longer than 10 tokens
        let text = "one two three four five six seven eight nine ten eleven twelve ".repeat(3);
        let chunks = chunk_text(&text, 10, 2).unwrap();
        assert!(chunks.len() > 1, "expected multiple chunks, got {}", chunks.len());
        // Each chunk (when tokenised) should be at most max_tokens
        let bpe = tiktoken_rs::cl100k_base().unwrap();
        for chunk in &chunks {
            let toks = bpe.encode_with_special_tokens(chunk);
            assert!(toks.len() <= 10, "chunk too long: {} tokens", toks.len());
        }
    }

    #[test]
    fn cosine_similarity_identical_vectors() {
        let v = vec![1.0f32, 0.0, 0.0];
        assert!((cosine_similarity(&v, &v) - 1.0).abs() < 1e-6);
    }

    #[test]
    fn cosine_similarity_orthogonal_vectors() {
        let a = vec![1.0f32, 0.0];
        let b = vec![0.0f32, 1.0];
        assert!((cosine_similarity(&a, &b)).abs() < 1e-6);
    }
}
