//! Cross-encoder reranker — BGE reranker (e.g. `bge-reranker-base`) run in-process
//! via `ort`, mirroring the FastVLM captioner's lazy-load pattern.
//!
//! Bi-encoder retrieval (BGE embeddings + sqlite-vec) is fast but scores the query
//! and each document independently. A cross-encoder instead reads the (query,
//! document) pair jointly and emits a single relevance logit, which is far more
//! precise for picking the best few results out of a candidate pool. We run it as
//! the final stage over the fused hybrid candidates.
//!
//! The model is optional: if its files aren't present the reranker reports
//! `available() == false` and callers keep the fused order unchanged.

use anyhow::{anyhow, Result};
use ort::session::builder::GraphOptimizationLevel;
use ort::session::Session;
use ort::value::{Tensor, Value};
use std::path::PathBuf;
use std::sync::Mutex;
use tokenizers::Tokenizer;

/// Cap on document characters fed to the cross-encoder. Keeps sequences bounded
/// (the reranker only needs enough text to judge relevance, not the full diff).
const MAX_DOC_CHARS: usize = 1800;

struct Loaded {
    tokenizer: Tokenizer,
    session: Session,
    /// Names the ONNX graph actually declares (BERT-family wants token_type_ids,
    /// XLM-R-family does not), so we only feed inputs the model expects.
    input_names: Vec<String>,
    output_name: String,
}

/// Lazily-loaded cross-encoder. Construction is cheap; the ONNX session loads on
/// the first `rerank()` call. Interior mutability (Mutex) lets it be shared behind
/// an `Arc` across MCP requests.
pub struct CrossEncoderReranker {
    model_dir: PathBuf,
    inner: Mutex<Option<Loaded>>,
}

impl CrossEncoderReranker {
    pub fn new(model_dir: PathBuf) -> Self {
        Self {
            model_dir,
            inner: Mutex::new(None),
        }
    }

    /// Whether the model files are present, so callers can skip reranking cleanly
    /// when the model hasn't been fetched.
    pub fn available(&self) -> bool {
        self.model_dir.join("tokenizer.json").exists() && self.onnx_path().exists()
    }

    fn onnx_path(&self) -> PathBuf {
        // Accept either a top-level model.onnx or the common onnx/ subdir layout.
        let nested = self.model_dir.join("onnx").join("model.onnx");
        if nested.exists() {
            nested
        } else {
            self.model_dir.join("model.onnx")
        }
    }

    fn load(&self) -> Result<Loaded> {
        let tokenizer = Tokenizer::from_file(self.model_dir.join("tokenizer.json"))
            .map_err(|e| anyhow!("{e}"))?;
        let threads = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);
        let session = Session::builder()?
            .with_optimization_level(GraphOptimizationLevel::Level3)?
            .with_intra_threads(threads)?
            .commit_from_file(self.onnx_path())?;
        let input_names = session.inputs.iter().map(|i| i.name.clone()).collect();
        let output_name = session
            .outputs
            .first()
            .map(|o| o.name.clone())
            .ok_or_else(|| anyhow!("reranker model has no outputs"))?;
        Ok(Loaded {
            tokenizer,
            session,
            input_names,
            output_name,
        })
    }

    /// Score each document against the query. Returns one relevance score per
    /// document (higher = more relevant), in the same order as `docs`.
    pub fn scores(&self, query: &str, docs: &[String]) -> Result<Vec<f32>> {
        let mut guard = self.inner.lock().expect("reranker mutex poisoned");
        if guard.is_none() {
            tracing::info!("loading cross-encoder reranker session");
            *guard = Some(self.load()?);
        }
        let m = guard.as_mut().unwrap();

        let wants_type_ids = m.input_names.iter().any(|n| n == "token_type_ids");
        let mut out = Vec::with_capacity(docs.len());

        for doc in docs {
            let doc: String = doc.chars().take(MAX_DOC_CHARS).collect();
            let enc = m
                .tokenizer
                .encode((query, doc.as_str()), true)
                .map_err(|e| anyhow!("{e}"))?;

            let len = enc.get_ids().len();
            let ids: Vec<i64> = enc.get_ids().iter().map(|&x| x as i64).collect();
            let mask: Vec<i64> = enc.get_attention_mask().iter().map(|&x| x as i64).collect();

            let mut inputs: Vec<(String, Value)> = vec![
                (
                    "input_ids".into(),
                    Tensor::from_array(([1, len], ids))?.into_dyn(),
                ),
                (
                    "attention_mask".into(),
                    Tensor::from_array(([1, len], mask))?.into_dyn(),
                ),
            ];
            if wants_type_ids {
                let type_ids: Vec<i64> = enc.get_type_ids().iter().map(|&x| x as i64).collect();
                inputs.push((
                    "token_type_ids".into(),
                    Tensor::from_array(([1, len], type_ids))?.into_dyn(),
                ));
            }

            let outputs = m.session.run(inputs)?;
            let (_, logits) = outputs[m.output_name.as_str()].try_extract_raw_tensor::<f32>()?;
            // Sequence-classification head emits a single relevance logit.
            out.push(logits.first().copied().unwrap_or(f32::MIN));
        }

        Ok(out)
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use std::fs;

    #[test]
    fn unavailable_when_model_files_missing() {
        let dir = std::env::temp_dir().join(format!("patcha-rerank-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let r = CrossEncoderReranker::new(dir.clone());
        assert!(!r.available(), "empty dir has no model");
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn onnx_path_prefers_nested_layout() {
        let dir = std::env::temp_dir().join(format!("patcha-rerank-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(dir.join("onnx")).unwrap();
        fs::write(dir.join("onnx").join("model.onnx"), b"x").unwrap();
        let r = CrossEncoderReranker::new(dir.clone());
        assert_eq!(r.onnx_path(), dir.join("onnx").join("model.onnx"));
        fs::remove_dir_all(&dir).ok();
    }

    #[test]
    fn onnx_path_falls_back_to_top_level() {
        let dir = std::env::temp_dir().join(format!("patcha-rerank-{}", uuid::Uuid::new_v4()));
        fs::create_dir_all(&dir).unwrap();
        let r = CrossEncoderReranker::new(dir.clone());
        assert_eq!(r.onnx_path(), dir.join("model.onnx"));
        fs::remove_dir_all(&dir).ok();
    }
}
