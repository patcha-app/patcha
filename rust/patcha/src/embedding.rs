use crate::config::Config;
use anyhow::{bail, Context, Result};
use fastembed::{EmbeddingModel, InitOptions, TextEmbedding};
use tiktoken_rs::cl100k_base;

// Token safety factor: bge WordPiece tokens run higher than cl100k for the same
// text, so we apply a 0.8 budget to avoid overflowing the model's native limit.
const TOKEN_SAFETY: f64 = 0.8;

// The sqlite-vec schema fixes the embedding column at 768 dims (db/migrations.rs).
// Any backend must produce 768-dim vectors or upserts will fail.
const STORE_VECTOR_DIM: usize = 768;

const MODEL_MAX_TOKENS: &[(&str, usize)] = &[
    ("BAAI/bge-base-en-v1.5", 512),
    ("BAAI/bge-small-en-v1.5", 512),
];

// ---------------------------------------------------------------------------
// Embedder
// ---------------------------------------------------------------------------

/// Pluggable embedding backend. `effective_max_tokens` / `chunk_overlap` stay
/// public fields so existing call sites keep compiling unchanged.
pub struct Embedder {
    backend: Backend,
    /// Effective token budget for chunking (accounts for safety margin).
    pub effective_max_tokens: usize,
    pub chunk_overlap: usize,
}

enum Backend {
    /// On-device ONNX embeddings via fastembed-rs (default — no server needed).
    Fastembed(Box<TextEmbedding>),
    /// Local embeddings served by an Ollama instance.
    Ollama(OllamaBackend),
}

struct OllamaBackend {
    http: ureq::Agent,
    /// Full endpoint, e.g. http://localhost:11434/api/embed
    url: String,
    model: String,
}

impl Embedder {
    pub fn new(cfg: &Config) -> Result<Self> {
        match cfg.embedding_provider.to_lowercase().as_str() {
            "ollama" => Self::new_ollama(cfg),
            _ => Self::new_fastembed(cfg),
        }
    }

    fn new_fastembed(cfg: &Config) -> Result<Self> {
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

        Ok(Self {
            backend: Backend::Fastembed(Box::new(model)),
            effective_max_tokens: effective_tokens(native_limit, cfg),
            chunk_overlap: cfg.embedding_chunk_overlap,
        })
    }

    fn new_ollama(cfg: &Config) -> Result<Self> {
        let http = ureq::AgentBuilder::new()
            .timeout(std::time::Duration::from_secs(120))
            .build();
        let backend = OllamaBackend {
            http,
            url: format!("{}/api/embed", cfg.ollama_url.trim_end_matches('/')),
            model: cfg.ollama_embedding_model.clone(),
        };
        // nomic-embed-text handles ~2048 tokens; cap by the configured budget.
        let embedder = Self {
            backend: Backend::Ollama(backend),
            effective_max_tokens: effective_tokens(2048, cfg),
            chunk_overlap: cfg.embedding_chunk_overlap,
        };

        // Fail fast with an actionable message if Ollama is unreachable or the
        // model's dimension doesn't match the store's fixed 768-dim schema.
        let probe = embedder.embed_one("dimension probe").context(
            "could not embed via Ollama — is `ollama serve` running and the model pulled? \
             try: ollama pull <model>",
        )?;
        if probe.len() != STORE_VECTOR_DIM {
            bail!(
                "Ollama embedding model '{}' returns {}-dim vectors, but patcha's store is \
                 fixed at {}-dim. Use a 768-dim model such as nomic-embed-text.",
                cfg.ollama_embedding_model,
                probe.len(),
                STORE_VECTOR_DIM
            );
        }
        Ok(embedder)
    }

    /// Embed a single text; returns a 768-dim float vector.
    pub fn embed_one(&self, text: &str) -> Result<Vec<f32>> {
        match &self.backend {
            Backend::Fastembed(model) => {
                let mut results = model
                    .embed(vec![text.to_owned()], None)
                    .context("fastembed embed_one failed")?;
                Ok(results.remove(0))
            }
            Backend::Ollama(b) => Ok(b.embed(vec![text.to_owned()])?.remove(0)),
        }
    }

    /// Embed a batch of texts. The returned Vec has the same length as `texts`.
    pub fn embed_many(&self, texts: Vec<String>) -> Result<Vec<Vec<f32>>> {
        match &self.backend {
            Backend::Fastembed(model) => {
                model.embed(texts, None).context("fastembed embed_many failed")
            }
            Backend::Ollama(b) => b.embed(texts),
        }
    }
}

impl OllamaBackend {
    fn embed(&self, texts: Vec<String>) -> Result<Vec<Vec<f32>>> {
        // Ollama's /api/embed accepts a string or an array in `input` and returns
        // one vector per input under `embeddings`.
        #[derive(serde::Serialize)]
        struct Req<'a> {
            model: &'a str,
            input: Vec<String>,
        }
        #[derive(serde::Deserialize)]
        struct Resp {
            embeddings: Vec<Vec<f32>>,
        }

        let body = serde_json::to_string(&Req {
            model: &self.model,
            input: texts,
        })
        .context("serialize ollama embed request")?;

        let resp = match self
            .http
            .post(&self.url)
            .set("content-type", "application/json")
            .send_string(&body)
        {
            Ok(r) => r,
            Err(ureq::Error::Status(code, r)) => {
                let msg = r.into_string().unwrap_or_default();
                bail!("ollama embed HTTP {code}: {msg}");
            }
            Err(e) => {
                return Err(anyhow::Error::new(e))
                    .with_context(|| format!("ollama embed request to {} failed", self.url))
            }
        };

        let parsed: Resp = serde_json::from_reader(resp.into_reader())
            .context("failed to parse ollama embed response")?;
        Ok(parsed.embeddings)
    }
}

fn effective_tokens(native_limit: usize, cfg: &Config) -> usize {
    let budget = ((native_limit as f64) * TOKEN_SAFETY) as usize;
    budget
        .min(cfg.max_embedding_tokens)
        .max(cfg.embedding_chunk_overlap + 1)
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
