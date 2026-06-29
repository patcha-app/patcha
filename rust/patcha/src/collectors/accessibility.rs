use crate::{
    collectors::filters::{is_banking_domain, is_incognito_window},
    config::Config,
    models::{Event, EventType},
    perception::{AppEmbeddingCache, FastVlmCaptioner, MobileClipEmbedder},
};
use anyhow::Result;
use chrono::{DateTime, Utc};
use std::io::Write;
use std::path::{Path, PathBuf};
use std::process::Command;

const MAX_LOG_ROWS: usize = 100_000;
const TRIM_EVERY: usize = 1_000;
const MIN_CONTENT_LEN: usize = 60;
const MIN_DURATION_SECS: i64 = 4;
const VISUAL_EMBEDDER_TAG: &str = "mobileclip_s2";
const CAPTIONER_TAG: &str = "fastvlm_0.5b";

// Apps skipped during screen capture
const SKIP_APPS: &[&str] = &[
    "Finder", "System Preferences", "System Settings", "loginwindow",
    "Dock", "", "1Password", "1Password 7 - Password Manager", "Bitwarden",
    "LastPass", "Dashlane", "Keychain Access", "KeePassXC", "NordPass",
];

pub struct AccessibilityCollector {
    log_file: PathBuf,
    ax_binary: PathBuf,
    ocr_binary: PathBuf,
    write_count: usize,
    // Visual pre-filter (Phase 1). `None` when disabled or the helper/model is absent.
    embedder: Option<MobileClipEmbedder>,
    cache: AppEmbeddingCache,
    drop_threshold: f32,
    last_key: Option<(String, String)>,
    // Gist captioner (Phase 3). `None` when disabled or the model is absent.
    captioner: Option<FastVlmCaptioner>,
}

impl AccessibilityCollector {
    pub fn new(data_dir: &Path, resources_dir: &Path, cfg: &Config) -> Self {
        let embedder = if cfg.enable_visual_prefilter {
            let candidate = MobileClipEmbedder::new(
                resources_dir.join("mobileclip"),
                resources_dir.join("mobileclip_s2_image.mlpackage"),
            );
            if candidate.available() {
                Some(candidate)
            } else {
                tracing::warn!(
                    "visual pre-filter enabled but mobileclip helper/model not found in {:?}; \
                     falling back to capture-every-tick",
                    resources_dir
                );
                None
            }
        } else {
            None
        };

        let captioner = if cfg.enable_captioner {
            let model_dir = resources_dir.join("models").join("fastvlm");
            let candidate = FastVlmCaptioner::new(model_dir.clone(), cfg.caption_max_new_tokens);
            if candidate.available() {
                Some(candidate)
            } else {
                tracing::warn!(
                    "captioner enabled but FastVLM model not found at {:?}; gist disabled",
                    model_dir
                );
                None
            }
        } else {
            None
        };

        Self {
            log_file: data_dir.join("screen_log.jsonl"),
            ax_binary: resources_dir.join("ax_content"),
            ocr_binary: resources_dir.join("ocr"),
            write_count: 0,
            embedder,
            cache: AppEmbeddingCache::new(cfg.visual_cache_ttl_seconds),
            drop_threshold: cfg.visual_drop_threshold,
            last_key: None,
            captioner,
        }
    }

    // -----------------------------------------------------------------------
    // Recording — called periodically by the daemon
    // -----------------------------------------------------------------------

    /// Capture the current screen state and append to screen_log.jsonl.
    pub fn record_current_screen(&mut self) -> Result<()> {
        // 1. Get accessibility info (window frame, active app, focused input)
        let ax_info = self.run_ax_content()?;
        let app_name = ax_info
            .get("app_name")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_owned();

        if app_name.is_empty() || SKIP_APPS.contains(&app_name.as_str()) {
            return Ok(());
        }

        let window_title = ax_info
            .get("window_title")
            .and_then(|v| v.as_str())
            .unwrap_or("")
            .to_owned();

        if is_incognito_window(&window_title) {
            return Ok(());
        }

        // 2. Capture screenshot of the active window
        let screenshot = match self.capture_window(&ax_info) {
            Ok(path) => path,
            Err(e) => {
                tracing::debug!(error=%e, "screenshot failed");
                return Ok(());
            }
        };

        // 3. Run OCR
        let ocr_text = match self.run_ocr(&screenshot) {
            Ok(text) if text.len() >= MIN_CONTENT_LEN => text,
            Ok(_) => {
                let _ = std::fs::remove_file(&screenshot);
                return Ok(());
            }
            Err(e) => {
                tracing::debug!(error=%e, "OCR failed");
                let _ = std::fs::remove_file(&screenshot);
                return Ok(());
            }
        };

        // 4. Visual pre-filter: embed the frame, then decide via the per-app cache.
        //    Title change -> always store (context switch). Same window -> drop if the
        //    frame is visually unchanged vs the cached embedding for this app.
        let key = (app_name.clone(), window_title.clone());
        let transition = match &self.last_key {
            None => "new",
            Some((a, _)) if *a != app_name => "switch",
            Some(k) if *k != key => "new",
            Some(_) => "same",
        };

        let now = Utc::now();
        let mut visual_embedding: Option<Vec<f32>> = None;
        if let Some(embedder) = self.embedder.as_mut() {
            match embedder.embed(&screenshot) {
                Ok(vec) => {
                    if transition == "same" {
                        if let Some(cos) = self.cache.fresh_cosine(&app_name, &vec, now) {
                            if cos >= self.drop_threshold {
                                tracing::debug!(app = %app_name, cosine = cos, "visual pre-filter: drop");
                                let _ = std::fs::remove_file(&screenshot);
                                self.last_key = Some(key);
                                return Ok(());
                            }
                        }
                    }
                    self.cache.update(&app_name, vec.clone(), now);
                    visual_embedding = Some(vec);
                }
                Err(e) => {
                    tracing::debug!(error = %e, "visual embed failed; storing without pre-filter");
                }
            }
        }

        // 5. Gist: caption only on context switches (new app / new window), while the
        //    screenshot is still on disk. Within-window drift ("same") skips the VLM.
        let gist = if transition != "same" {
            self.captioner.as_mut().and_then(|c| {
                match c.caption(&screenshot, &app_name, &window_title, &ocr_text) {
                    Ok(g) => Some(g),
                    Err(e) => {
                        tracing::debug!(error = %e, "captioner failed");
                        None
                    }
                }
            })
        } else {
            None
        };

        let _ = std::fs::remove_file(&screenshot);
        self.last_key = Some(key);

        // 6. Append to log
        self.append_log_entry(
            &app_name,
            &window_title,
            &ocr_text,
            transition,
            visual_embedding.as_deref(),
            gist.as_deref(),
        )
    }

    // -----------------------------------------------------------------------
    // Collection — reads the log and produces events
    // -----------------------------------------------------------------------

    pub fn collect_screen_text(&self, since: DateTime<Utc>) -> Vec<Event> {
        let entries = match self.read_log(since) {
            Ok(e) => e,
            Err(_) => return Vec::new(),
        };

        let now = Utc::now();
        let mut events = Vec::new();

        for i in 0..entries.len() {
            let (ts, ref obj) = entries[i];
            let end_ts = entries.get(i + 1).map(|(t, _)| *t).unwrap_or(now);
            let duration = (end_ts - ts).num_seconds();

            if duration < MIN_DURATION_SECS {
                continue;
            }

            let text = obj
                .get("raw_text")
                .or_else(|| obj.get("text"))
                .and_then(|v| v.as_str())
                .unwrap_or("");

            if text.is_empty() {
                continue;
            }

            let app = obj
                .get("app_name")
                .or_else(|| obj.get("app"))
                .and_then(|v| v.as_str())
                .unwrap_or("");
            let window_title = obj.get("window_title").and_then(|v| v.as_str()).unwrap_or("");

            if is_banking_domain(app) || is_incognito_window(window_title) {
                continue;
            }

            let raw_content = if window_title.is_empty() {
                format!("{app}: {text}")
            } else {
                format!("{app} — {window_title}: {text}")
            };

            let mut e = Event::new(EventType::Screen, raw_content);
            e.timestamp = ts;
            e.source = Some("accessibility".to_owned());
            e.source_doc_id = obj.get("source_doc_id").and_then(|v| v.as_str()).map(|s| s.to_owned());
            e.metadata.insert("app_name".into(), serde_json::json!(app));
            e.metadata.insert("window_title".into(), serde_json::json!(window_title));
            e.metadata.insert("duration_seconds".into(), serde_json::json!(duration));
            e.metadata.insert(
                "raw_text_source".into(),
                obj.get("raw_text_source")
                    .cloned()
                    .unwrap_or(serde_json::json!("ax")),
            );
            if let Some(gist) = obj.get("gist") {
                e.metadata.insert("gist".into(), gist.clone());
            }

            events.push(e);
        }

        events
    }

    // -----------------------------------------------------------------------
    // Subprocess helpers
    // -----------------------------------------------------------------------

    fn run_ax_content(&self) -> Result<serde_json::Value> {
        if !self.ax_binary.exists() {
            return Ok(serde_json::json!({}));
        }

        let out = Command::new(&self.ax_binary).output()?;
        if out.status.success() {
            let json_str = String::from_utf8_lossy(&out.stdout);
            Ok(serde_json::from_str(json_str.trim()).unwrap_or(serde_json::json!({})))
        } else {
            Ok(serde_json::json!({}))
        }
    }

    fn capture_window(&self, ax_info: &serde_json::Value) -> Result<PathBuf> {
        let tmp = tempfile::Builder::new().suffix(".png").tempfile()?;
        let path = tmp.into_temp_path();

        // Try to get window bounds from ax_info for a precise crop
        let frame = ax_info.get("window_frame").and_then(|f| {
            Some((
                f.get("x")?.as_f64()?,
                f.get("y")?.as_f64()?,
                f.get("w")?.as_f64()?,
                f.get("h")?.as_f64()?,
            ))
        });

        let mut cmd = Command::new("screencapture");
        cmd.arg("-x").arg("-o");

        if let Some((x, y, w, h)) = frame {
            cmd.arg("-R").arg(format!("{x},{y},{w},{h}"));
        } else {
            // Fall back to full-screen capture
            cmd.arg("-m");
        }

        cmd.arg(path.as_os_str());

        let status = cmd.status()?;
        if !status.success() {
            anyhow::bail!("screencapture exited with status {}", status);
        }

        // Keep the file alive by moving it to a named path
        let out_path = path.to_path_buf();
        path.keep().map_err(|e| anyhow::anyhow!("tempfile keep: {e}"))?;
        Ok(out_path)
    }

    fn run_ocr(&self, image_path: &Path) -> Result<String> {
        if !self.ocr_binary.exists() {
            return Ok(String::new());
        }

        let out = Command::new(&self.ocr_binary)
            .arg(image_path)
            .output()?;

        if out.status.success() {
            Ok(String::from_utf8_lossy(&out.stdout).trim().to_owned())
        } else {
            anyhow::bail!(
                "ocr binary failed: {}",
                String::from_utf8_lossy(&out.stderr)
            )
        }
    }

    fn append_log_entry(
        &mut self,
        app_name: &str,
        window_title: &str,
        text: &str,
        transition: &str,
        visual_embedding: Option<&[f32]>,
        gist: Option<&str>,
    ) -> Result<()> {
        if let Some(parent) = self.log_file.parent() {
            std::fs::create_dir_all(parent)?;
        }

        let entry = serde_json::json!({
            "timestamp": Utc::now().to_rfc3339(),
            "app_name": app_name,
            "window_title": window_title,
            "raw_text": text,
            "raw_text_source": "ocr",
            "trigger": "timer",
            "transition": transition,
            "gist": gist,
            "visual_embedding": visual_embedding,
            "_meta": {
                "visual_embedder": visual_embedding.map(|_| VISUAL_EMBEDDER_TAG),
                "captioner": gist.map(|_| CAPTIONER_TAG),
            },
        });

        let mut f = std::fs::OpenOptions::new()
            .create(true)
            .append(true)
            .open(&self.log_file)?;
        writeln!(f, "{}", entry)?;

        self.write_count += 1;
        if self.write_count % TRIM_EVERY == 0 {
            trim_jsonl(&self.log_file, MAX_LOG_ROWS)?;
        }

        Ok(())
    }

    fn read_log(&self, since: DateTime<Utc>) -> Result<Vec<(DateTime<Utc>, serde_json::Value)>> {
        if !self.log_file.exists() {
            return Ok(Vec::new());
        }

        let content = std::fs::read_to_string(&self.log_file)?;
        let mut entries = Vec::new();

        for line in content.lines() {
            let Ok(obj) = serde_json::from_str::<serde_json::Value>(line.trim()) else {
                continue;
            };
            let Some(ts_str) = obj.get("timestamp").and_then(|v| v.as_str()) else {
                continue;
            };
            let Ok(ts) = DateTime::parse_from_rfc3339(ts_str) else {
                continue;
            };
            let ts = ts.with_timezone(&Utc);
            if ts >= since {
                entries.push((ts, obj));
            }
        }

        Ok(entries)
    }
}

fn trim_jsonl(path: &Path, max_rows: usize) -> Result<()> {
    let content = std::fs::read_to_string(path)?;
    let lines: Vec<&str> = content.lines().collect();
    if lines.len() <= max_rows {
        return Ok(());
    }
    let keep = &lines[lines.len() - max_rows..];
    let tmp = path.with_extension("jsonl.tmp");
    std::fs::write(&tmp, keep.join("\n") + "\n")?;
    std::fs::rename(tmp, path)?;
    Ok(())
}
