//! Library facade for the patcha crate.
//!
//! The binary (`main.rs`) drives the CLI; this `lib` target exposes the same
//! modules so integration tests under `tests/` can exercise the real collectors,
//! OCR path, embedding, and storage pipeline rather than re-implementing them.

pub mod categorize;
pub mod cli;
pub mod collectors;
pub mod compaction;
pub mod config;
pub mod daemon;
pub mod db;
pub mod embedding;
pub mod llm;
pub mod mcp;
pub mod models;
pub mod perception;
pub mod process;
pub mod summary;
pub mod triggers;
