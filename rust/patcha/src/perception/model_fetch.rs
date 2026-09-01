//! First-run fetch for the FastVLM captioner model.
//!
//! The model is ~810 MB, so it is downloaded on first run instead of being
//! bundled in the .dmg. It lands in `~/.patcha/models/fastvlm` because the app
//! bundle is code-signed and read-only — writing into `Contents/Resources`
//! would invalidate the signature.

use anyhow::{anyhow, Context, Result};
use serde::Serialize;
use std::io::{Seek, SeekFrom, Write};
use std::path::{Path, PathBuf};

const HF_BASE: &str = "https://huggingface.co/onnx-community/FastVLM-0.5B-ONNX/resolve/main";
const MAX_ATTEMPTS: usize = 3;

/// Files the captioner loads, each with a minimum plausible size. The size floor
/// catches a truncated transfer or an HTML error page saved under the model's
/// name, which would otherwise look present and then fail inside ort with an
/// opaque parse error.
///
/// Note `decoder_model_merged_q4` (not q4f16): the CPU execution provider cannot
/// run the fp16 contrib ops in the q4f16 decoder. See `captioner.rs`.
const FILES: &[(&str, u64)] = &[
    ("config.json", 100),
    ("generation_config.json", 100),
    ("preprocessor_config.json", 100),
    ("processor_config.json", 100),
    ("special_tokens_map.json", 100),
    ("tokenizer_config.json", 100),
    ("tokenizer.json", 1_000_000),
    ("onnx/vision_encoder_q4f16.onnx", 200_000_000),
    ("onnx/embed_tokens_q4f16.onnx", 200_000_000),
    ("onnx/decoder_model_merged_q4.onnx", 250_000_000),
];

fn patcha_dir() -> PathBuf {
    dirs::home_dir()
        .unwrap_or_else(|| PathBuf::from("/tmp"))
        .join(".patcha")
}

/// Where a downloaded model lives.
pub fn user_model_dir() -> PathBuf {
    patcha_dir().join("models").join("fastvlm")
}

/// Resolve the model directory, preferring a copy bundled next to the binary
/// (dev checkouts and any build that still ships one) over the downloaded copy.
pub fn resolve_model_dir(resources_dir: &Path) -> PathBuf {
    let bundled = resources_dir.join("models").join("fastvlm");
    if is_complete(&bundled) {
        return bundled;
    }
    user_model_dir()
}

/// Whether every required file is present and large enough to be real.
pub fn is_complete(dir: &Path) -> bool {
    FILES.iter().all(|(rel, min_size)| {
        std::fs::metadata(dir.join(rel))
            .map(|m| m.len() >= *min_size)
            .unwrap_or(false)
    })
}

#[derive(Serialize)]
#[serde(tag = "state", rename_all = "snake_case")]
enum Status {
    Downloading {
        file: String,
        file_index: usize,
        file_count: usize,
        downloaded_bytes: u64,
        total_bytes: u64,
    },
    Ready,
    Failed {
        error: String,
    },
}

/// Progress is published as a file rather than pushed to the app: the daemon is
/// restarted independently of the menu bar app, so a file lets the UI recover
/// the current state whenever it happens to look.
fn write_status(status: &Status) {
    let path = patcha_dir().join("model_download.json");
    if let Some(parent) = path.parent() {
        let _ = std::fs::create_dir_all(parent);
    }
    if let Ok(json) = serde_json::to_string(status) {
        let _ = std::fs::write(path, json);
    }
}

/// Download any missing model files into `dir`. Idempotent: complete files are
/// left alone, so an interrupted run resumes rather than starting over.
pub async fn ensure_fastvlm(dir: &Path) -> Result<()> {
    if is_complete(dir) {
        write_status(&Status::Ready);
        return Ok(());
    }

    std::fs::create_dir_all(dir.join("onnx"))
        .with_context(|| format!("creating model dir {dir:?}"))?;

    let missing: Vec<_> = FILES
        .iter()
        .filter(|(rel, min_size)| {
            std::fs::metadata(dir.join(rel))
                .map(|m| m.len() < *min_size)
                .unwrap_or(true)
        })
        .collect();

    let total_files = missing.len();
    tracing::info!(
        files = total_files,
        dir = ?dir,
        "fetching FastVLM captioner model (first run, ~810 MB)"
    );

    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(60 * 60))
        .build()?;

    for (index, (rel, min_size)) in missing.iter().enumerate() {
        let mut last_err = None;
        let mut ok = false;

        for attempt in 1..=MAX_ATTEMPTS {
            match fetch_file(&client, dir, rel, *min_size, index, total_files).await {
                Ok(()) => {
                    ok = true;
                    break;
                }
                Err(e) => {
                    tracing::warn!(file = rel, attempt, error = %e, "model file download failed");
                    last_err = Some(e);
                }
            }
        }

        if !ok {
            let err = last_err.unwrap_or_else(|| anyhow!("unknown error"));
            let msg = format!("{rel}: {err}");
            write_status(&Status::Failed { error: msg.clone() });
            return Err(anyhow!(msg));
        }
    }

    write_status(&Status::Ready);
    tracing::info!("FastVLM captioner model ready");
    Ok(())
}

async fn fetch_file(
    client: &reqwest::Client,
    dir: &Path,
    rel: &str,
    min_size: u64,
    index: usize,
    file_count: usize,
) -> Result<()> {
    let dest = dir.join(rel);
    let part = dir.join(format!("{rel}.part"));

    // Resume a partial transfer where the server supports it. A 200 response to
    // a ranged request means the server ignored the range, so the partial file
    // has to be discarded rather than appended to.
    let existing = std::fs::metadata(&part).map(|m| m.len()).unwrap_or(0);
    let url = format!("{HF_BASE}/{rel}");
    let mut req = client.get(&url);
    if existing > 0 {
        req = req.header(reqwest::header::RANGE, format!("bytes={existing}-"));
    }

    let resp = req.send().await?.error_for_status()?;
    let resuming = existing > 0 && resp.status() == reqwest::StatusCode::PARTIAL_CONTENT;
    let mut downloaded = if resuming { existing } else { 0 };
    let total = resp.content_length().unwrap_or(0) + downloaded;

    // truncate(false) because a resumed transfer appends; the non-resume path
    // truncates explicitly below.
    let mut file = std::fs::OpenOptions::new()
        .create(true)
        .write(true)
        .truncate(false)
        .open(&part)?;
    if resuming {
        file.seek(SeekFrom::Start(existing))?;
    } else {
        file.set_len(0)?;
    }

    let mut resp = resp;
    let mut since_report = 0u64;
    while let Some(chunk) = resp.chunk().await? {
        file.write_all(&chunk)?;
        downloaded += chunk.len() as u64;
        since_report += chunk.len() as u64;
        if since_report >= 8 * 1024 * 1024 {
            since_report = 0;
            write_status(&Status::Downloading {
                file: rel.to_string(),
                file_index: index + 1,
                file_count,
                downloaded_bytes: downloaded,
                total_bytes: total,
            });
        }
    }
    file.flush()?;
    drop(file);

    let size = std::fs::metadata(&part)?.len();
    if size < min_size {
        let _ = std::fs::remove_file(&part);
        return Err(anyhow!(
            "downloaded {size} bytes, expected at least {min_size}"
        ));
    }

    std::fs::rename(&part, &dest)?;
    Ok(())
}
