//! Visual perception: per-frame image embeddings + per-app change detection.
//!
//! `MobileClipEmbedder` drives a long-lived Swift Core ML helper that turns a
//! screenshot into a 512-d MobileCLIP vector. `AppEmbeddingCache` holds the
//! last-stored vector per app so redundant frames can be dropped before they
//! become events. See `perception-phase1-plan.md`.

mod app_cache;
mod captioner;
mod embedder;

pub use app_cache::{cosine, AppEmbeddingCache};
pub use captioner::FastVlmCaptioner;
pub use embedder::MobileClipEmbedder;
