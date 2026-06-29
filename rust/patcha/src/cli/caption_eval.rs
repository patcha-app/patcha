//! `patcha caption-eval <images_dir>` — run the production FastVLM captioner over a
//! folder of screenshots and print each gist + latency. For judging caption quality
//! across screen types (editor, browser, design tool, terminal, video, …).

use crate::config::Config;
use crate::perception::FastVlmCaptioner;
use anyhow::{anyhow, Result};
use clap::Args;
use std::path::{Path, PathBuf};
use std::process::Command;
use std::time::Instant;

#[derive(Args)]
pub struct CaptionEvalArgs {
    #[arg(help = "A screenshot image, or a directory of png/jpg images, to caption")]
    pub images_dir: PathBuf,

    #[arg(long, help = "FastVLM model dir (default: <resources>/models/fastvlm)")]
    pub model_dir: Option<PathBuf>,

    #[arg(long, help = "OCR helper binary (default: <resources>/ocr)")]
    pub ocr_bin: Option<PathBuf>,

    #[arg(long, help = "App-name context for every image (default: each file's stem)")]
    pub app: Option<String>,

    #[arg(long, help = "Window-title context for every image")]
    pub window: Option<String>,

    #[arg(long, default_value_t = 80, help = "Max new tokens per caption")]
    pub max_new_tokens: usize,

    #[arg(long, help = "Skip OCR grounding (image-only captions)")]
    pub no_ocr: bool,
}

pub async fn run(args: CaptionEvalArgs, _cfg: Config) -> Result<()> {
    let res = resources_dir();
    let model_dir = args
        .model_dir
        .unwrap_or_else(|| res.join("models").join("fastvlm"));
    let ocr_bin = args.ocr_bin.unwrap_or_else(|| res.join("ocr"));

    let mut cap = FastVlmCaptioner::new(model_dir.clone(), args.max_new_tokens);
    if !cap.available() {
        return Err(anyhow!(
            "FastVLM model not found at {model_dir:?} — pass --model-dir or fetch the model"
        ));
    }

    let mut images: Vec<PathBuf> = if args.images_dir.is_file() {
        vec![args.images_dir.clone()]
    } else {
        std::fs::read_dir(&args.images_dir)?
            .filter_map(|e| e.ok().map(|e| e.path()))
            .filter(|p| {
                matches!(
                    p.extension().and_then(|s| s.to_str()).map(|s| s.to_lowercase()).as_deref(),
                    Some("png" | "jpg" | "jpeg")
                )
            })
            .collect()
    };
    images.sort();
    if images.is_empty() {
        return Err(anyhow!("no png/jpg images at {:?}", args.images_dir));
    }

    println!("Captioning {} image(s) from {:?}\n", images.len(), args.images_dir);
    let mut total = 0f32;
    for img in &images {
        let stem = img.file_stem().and_then(|s| s.to_str()).unwrap_or("");
        let app = args.app.clone().unwrap_or_else(|| stem.to_string());
        let window = args.window.clone().unwrap_or_default();
        let ocr = if args.no_ocr {
            String::new()
        } else {
            ocr_text(&ocr_bin, img).unwrap_or_default()
        };

        let t = Instant::now();
        let result = cap.caption(img, &app, &window, &ocr);
        let secs = t.elapsed().as_secs_f32();
        total += secs;

        let name = img.file_name().unwrap().to_string_lossy();
        match result {
            Ok(gist) => println!("── {name}  ({secs:.1}s) ──\n{gist}\n"),
            Err(e) => println!("── {name}  ({secs:.1}s) ──\n[error: {e}]\n"),
        }
    }
    println!("avg {:.1}s/image over {} image(s)", total / images.len() as f32, images.len());
    Ok(())
}

/// OCR an image via the helper binary, returning a top-to-bottom text dump (the
/// captioner snippets it internally — same grounding the collector provides).
fn ocr_text(ocr_bin: &Path, image: &Path) -> Result<String> {
    if !ocr_bin.exists() {
        return Ok(String::new());
    }
    let out = Command::new(ocr_bin).arg(image).output()?;
    let obs: Vec<serde_json::Value> = serde_json::from_slice(&out.stdout).unwrap_or_default();
    let mut items: Vec<(f64, f64, String)> = obs
        .iter()
        .filter_map(|o| {
            Some((
                o.get("y")?.as_f64()?,
                o.get("x")?.as_f64()?,
                o.get("text")?.as_str()?.to_owned(),
            ))
        })
        .collect();
    // Bottom-left origin: higher y = higher on screen, so descending y is top-down.
    items.sort_by(|a, b| b.0.partial_cmp(&a.0).unwrap().then(a.1.partial_cmp(&b.1).unwrap()));
    Ok(items.into_iter().map(|(_, _, t)| t).collect::<Vec<_>>().join(" "))
}

fn resources_dir() -> PathBuf {
    if let Ok(d) = std::env::var("PATCHA_RESOURCES") {
        return PathBuf::from(d);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(p) = exe.parent() {
            if p.join("ocr").exists() {
                return p.to_path_buf();
            }
        }
    }
    PathBuf::from("data")
}
