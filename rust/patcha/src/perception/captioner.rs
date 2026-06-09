//! FastVLM gist captioner — LLaVA-Qwen2 (0.5B) run in-process via ort.
//!
//! Captions a screenshot in one sentence so image-heavy screens (Figma, video,
//! dashboards) get a meaningful `gist` even when OCR text is sparse. Three ONNX
//! graphs: vision_encoder -> [embed(before) | image features | embed(after)] ->
//! greedy decode over the merged Qwen2 decoder with KV cache. See
//! `perception-phase2-3-plan.md`.
//!
//! Variant note: the CPU execution provider can't run the fp16 contrib ops in the
//! `*_q4f16`/`fp16` decoder, so the decoder is the fp32-activation `q4` graph
//! (the vision/embed `q4f16` graphs do run on CPU and are smaller).

use anyhow::{anyhow, Result};
use image::imageops::FilterType;
use ndarray::{Array, Array3, Axis};
use ort::session::builder::GraphOptimizationLevel;
use ort::session::Session;
use ort::value::{Tensor, Value};
use std::path::{Path, PathBuf};
use tokenizers::Tokenizer;

const IMAGE_SIZE: usize = 1024;
const HIDDEN: usize = 896;
const N_LAYERS: usize = 24;
const KV_HEADS: usize = 2;
const HEAD_DIM: usize = 64;
const EOS: i64 = 151645;

const VISION_FILE: &str = "vision_encoder_q4f16.onnx";
const EMBED_FILE: &str = "embed_tokens_q4f16.onnx";
const DECODER_FILE: &str = "decoder_model_merged_q4.onnx";

struct Loaded {
    tokenizer: Tokenizer,
    vision: Session,
    embed: Session,
    decoder: Session,
}

/// Lazily-loaded captioner. Construction is cheap; the ~0.8 GB of ONNX sessions
/// load on the first `caption()` call so idle daemons don't pay for it.
pub struct FastVlmCaptioner {
    model_dir: PathBuf,
    max_new_tokens: usize,
    inner: Option<Loaded>,
}

impl FastVlmCaptioner {
    pub fn new(model_dir: PathBuf, max_new_tokens: usize) -> Self {
        Self {
            model_dir,
            max_new_tokens,
            inner: None,
        }
    }

    /// Whether the model files are present (so the collector can skip captioning
    /// cleanly when the model hasn't been fetched).
    pub fn available(&self) -> bool {
        ["tokenizer.json", &format!("onnx/{DECODER_FILE}")]
            .iter()
            .all(|f| self.model_dir.join(f).exists())
    }

    fn load(&self) -> Result<Loaded> {
        let dir = &self.model_dir;
        let tokenizer =
            Tokenizer::from_file(dir.join("tokenizer.json")).map_err(|e| anyhow!("{e}"))?;
        let threads = std::thread::available_parallelism()
            .map(|n| n.get())
            .unwrap_or(4);
        let build = || {
            Session::builder()?
                .with_optimization_level(GraphOptimizationLevel::Level3)?
                .with_intra_threads(threads)
        };
        // FASTVLM_VISION_FILE lets us A/B vision-encoder quant variants without a rebuild.
        let vision_file =
            std::env::var("FASTVLM_VISION_FILE").unwrap_or_else(|_| VISION_FILE.to_string());
        let vision = build()?.commit_from_file(dir.join("onnx").join(vision_file))?;
        let embed = build()?.commit_from_file(dir.join("onnx").join(EMBED_FILE))?;
        let decoder = build()?.commit_from_file(dir.join("onnx").join(DECODER_FILE))?;
        Ok(Loaded {
            tokenizer,
            vision,
            embed,
            decoder,
        })
    }

    fn ensure_loaded(&mut self) -> Result<&Loaded> {
        if self.inner.is_none() {
            tracing::info!("loading FastVLM captioner sessions");
            self.inner = Some(self.load()?);
        }
        Ok(self.inner.as_ref().unwrap())
    }

    /// Caption the screen in 2-3 sentences, grounded in the active app/window and
    /// the OCR'd on-screen text so the gist names concrete files/content.
    pub fn caption(
        &mut self,
        image_path: &Path,
        app: &str,
        window: &str,
        ocr_text: &str,
    ) -> Result<String> {
        let max_new = self.max_new_tokens;
        let m = self.ensure_loaded()?;

        let ocr = ocr_snippet(ocr_text, 600);
        let prompt = format!(
            "The active app is {app}, window '{window}'. Visible on-screen text includes: {ocr}. \
             In 2-3 sentences, describe specifically what the user is doing — mention the files, \
             code, UI panels, or content actually visible. Only describe what is visible; do not \
             guess details that are not shown."
        );
        let before =
            "<|im_start|>system\nYou are observing a user's screen.<|im_end|>\n<|im_start|>user\n";
        let after = format!("\n{prompt}<|im_end|>\n<|im_start|>assistant\n");

        // 1. Vision features.
        let pixels = preprocess(image_path)?;
        let v_out = m
            .vision
            .run(ort::inputs![ "pixel_values" => Tensor::from_array(pixels)? ]?)?;
        let (vshape, vdata) = v_out["image_features"].try_extract_raw_tensor::<f32>()?;
        let n_img = vshape[1] as usize;
        let image_features = Array3::from_shape_vec((1, n_img, HIDDEN), vdata.to_vec())?;

        // 2. inputs_embeds = [embed(before) | image features | embed(after)].
        let before_ids = encode(&m.tokenizer, before)?;
        let after_ids = encode(&m.tokenizer, &after)?;
        let e_before = run_embed(&m.embed, &before_ids)?;
        let e_after = run_embed(&m.embed, &after_ids)?;
        let mut embeds = ndarray::concatenate(
            Axis(1),
            &[e_before.view(), image_features.view(), e_after.view()],
        )?;

        // 3. Greedy decode with KV cache.
        let mut past = empty_kv()?;
        let mut past_len = 0usize;
        let mut generated: Vec<u32> = Vec::new();

        for _ in 0..max_new {
            let cur_seq = embeds.shape()[1];
            let total = past_len + cur_seq;
            let attn: Vec<i64> = vec![1; total];
            let pos: Vec<i64> = (past_len as i64..total as i64).collect();

            let embeds_t = Tensor::from_array((
                [1, cur_seq, HIDDEN],
                embeds.as_standard_layout().iter().copied().collect::<Vec<f32>>(),
            ))?;
            let mut inputs: Vec<(String, Value)> = vec![
                ("inputs_embeds".into(), embeds_t.into_dyn()),
                ("attention_mask".into(), Tensor::from_array(([1, total], attn))?.into_dyn()),
                ("position_ids".into(), Tensor::from_array(([1, cur_seq], pos))?.into_dyn()),
            ];
            inputs.extend(past.drain(..));

            let outputs = m.decoder.run(inputs)?;

            let (lshape, logits) = outputs["logits"].try_extract_raw_tensor::<f32>()?;
            let vocab = lshape[2] as usize;
            let last = (lshape[1] as usize - 1) * vocab;
            let next = logits[last..last + vocab]
                .iter()
                .enumerate()
                .max_by(|a, b| a.1.partial_cmp(b.1).unwrap())
                .map(|(i, _)| i as i64)
                .unwrap();

            if next == EOS {
                break;
            }
            generated.push(next as u32);

            past_len = total;
            let mut new_past: Vec<(String, Value)> = Vec::with_capacity(N_LAYERS * 2);
            for i in 0..N_LAYERS {
                for which in ["key", "value"] {
                    let (sh, d) = outputs[format!("present.{i}.{which}").as_str()]
                        .try_extract_raw_tensor::<f32>()?;
                    let dims: Vec<usize> = sh.iter().map(|&x| x as usize).collect();
                    new_past.push((
                        format!("past_key_values.{i}.{which}"),
                        Tensor::from_array((dims, d.to_vec()))?.into_dyn(),
                    ));
                }
            }
            past = new_past;

            embeds = run_embed(&m.embed, &[next])?;
        }

        let caption = m
            .tokenizer
            .decode(&generated, true)
            .map_err(|e| anyhow!("{e}"))?;
        Ok(trim_to_sentence(caption.trim()))
    }
}

/// Drop a trailing incomplete sentence (the token cap can cut mid-clause).
fn trim_to_sentence(s: &str) -> String {
    match s.rfind(['.', '!', '?']) {
        Some(i) => s[..=i].to_string(),
        None => s.to_string(),
    }
}

/// Collapse whitespace and cap length, for grounding the prompt in OCR text.
fn ocr_snippet(text: &str, max_chars: usize) -> String {
    text.split_whitespace()
        .collect::<Vec<_>>()
        .join(" ")
        .chars()
        .take(max_chars)
        .collect()
}

fn encode(tok: &Tokenizer, text: &str) -> Result<Vec<i64>> {
    Ok(tok
        .encode(text, false)
        .map_err(|e| anyhow!("{e}"))?
        .get_ids()
        .iter()
        .map(|&x| x as i64)
        .collect())
}

fn preprocess(path: &Path) -> Result<Array<f32, ndarray::Ix4>> {
    // Letterbox the whole screen into 1024x1024 (model's "pad" aspect ratio): resize
    // longest edge to 1024, center on a black canvas. Preserves the full screen —
    // center-cropping would discard ~40% of a wide display's width. /255, RGB, CHW.
    let img = image::open(path)?.to_rgb8();
    let (w, h) = img.dimensions();
    let scale = IMAGE_SIZE as f32 / w.max(h) as f32;
    let nw = ((w as f32 * scale).round() as u32).max(1);
    let nh = ((h as f32 * scale).round() as u32).max(1);
    let resized = image::imageops::resize(&img, nw, nh, FilterType::CatmullRom);
    let ox = (IMAGE_SIZE as u32 - nw) / 2;
    let oy = (IMAGE_SIZE as u32 - nh) / 2;
    let mut arr = Array::zeros((1, 3, IMAGE_SIZE, IMAGE_SIZE));
    for y in 0..nh {
        for x in 0..nw {
            let px = resized.get_pixel(x, y);
            for c in 0..3 {
                arr[[0, c, (oy + y) as usize, (ox + x) as usize]] = px[c] as f32 / 255.0;
            }
        }
    }
    Ok(arr)
}

fn run_embed(embed: &Session, ids: &[i64]) -> Result<Array3<f32>> {
    if ids.is_empty() {
        return Ok(Array3::zeros((1, 0, HIDDEN)));
    }
    let input = Tensor::from_array(([1, ids.len()], ids.to_vec()))?;
    let outputs = embed.run(ort::inputs![ "input_ids" => input ]?)?;
    let (shape, data) = outputs["inputs_embeds"].try_extract_raw_tensor::<f32>()?;
    Ok(Array3::from_shape_vec((1, shape[1] as usize, HIDDEN), data.to_vec())?)
}

fn empty_kv() -> Result<Vec<(String, Value)>> {
    let mut kv = Vec::new();
    for i in 0..N_LAYERS {
        for which in ["key", "value"] {
            let a = ndarray::Array4::<f32>::from_shape_vec((1, KV_HEADS, 0, HEAD_DIM), Vec::new())?;
            kv.push((format!("past_key_values.{i}.{which}"), Tensor::from_array(a)?.into_dyn()));
        }
    }
    Ok(kv)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn trims_trailing_incomplete_sentence() {
        assert_eq!(trim_to_sentence("Editing main.rs. Then the tail"), "Editing main.rs.");
        assert_eq!(trim_to_sentence("Done!"), "Done!");
        assert_eq!(trim_to_sentence("Reviewing the PR? yes"), "Reviewing the PR?");
        // No terminator -> returned unchanged.
        assert_eq!(trim_to_sentence("no period here"), "no period here");
    }

    #[test]
    fn ocr_snippet_collapses_whitespace_and_truncates() {
        assert_eq!(ocr_snippet("  foo\n\t bar   baz  ", 100), "foo bar baz");
        assert_eq!(ocr_snippet("aaaa bbbb cccc", 6), "aaaa b");
        assert_eq!(ocr_snippet("", 10), "");
    }

    #[test]
    fn empty_kv_has_one_entry_per_layer_and_side() {
        let kv = empty_kv().unwrap();
        assert_eq!(kv.len(), N_LAYERS * 2);
        assert!(kv.iter().any(|(n, _)| n == "past_key_values.0.key"));
        assert!(kv.iter().any(|(n, _)| n == "past_key_values.23.value"));
        // Each is a [1, KV_HEADS, 0, HEAD_DIM] empty tensor.
        let (shape, data) = kv[0].1.try_extract_raw_tensor::<f32>().unwrap();
        assert_eq!(shape, &[1, KV_HEADS as i64, 0, HEAD_DIM as i64]);
        assert!(data.is_empty());
    }

    #[test]
    fn preprocess_letterboxes_wide_image_and_normalizes() {
        // A wide solid-red image: pad must center it vertically, leaving black bands
        // top and bottom, and normalize pixels to [0,1].
        let img = image::RgbImage::from_pixel(400, 100, image::Rgb([255, 0, 0]));
        let path = std::env::temp_dir().join("patcha_preprocess_test.png");
        img.save(&path).unwrap();

        let arr = preprocess(&path).unwrap();
        assert_eq!(arr.shape(), &[1, 3, IMAGE_SIZE, IMAGE_SIZE]);
        assert!(arr.iter().all(|&v| (0.0..=1.0).contains(&v)));

        // 400x100 -> scaled to 1024x256, centered -> rows [384,640) hold content.
        let mid = IMAGE_SIZE / 2;
        assert!(arr[[0, 0, mid, mid]] > 0.9, "center should be red (R~1)");
        assert!(arr[[0, 1, mid, mid]] < 0.1, "center G should be ~0");
        assert_eq!(arr[[0, 0, 0, mid]], 0.0, "top band should be black padding");
        assert_eq!(arr[[0, 0, IMAGE_SIZE - 1, mid]], 0.0, "bottom band should be black padding");

        let _ = std::fs::remove_file(&path);
    }

    // Runs the real production captioner end-to-end. Skips unless the model dir +
    // a test image are pointed to by env: CAPTIONER_TEST_DIR, CAPTIONER_TEST_IMG.
    #[test]
    fn caption_produces_text() {
        let (Ok(dir), Ok(img)) = (
            std::env::var("CAPTIONER_TEST_DIR"),
            std::env::var("CAPTIONER_TEST_IMG"),
        ) else {
            eprintln!("skipping: CAPTIONER_TEST_* not set");
            return;
        };
        let mut cap = FastVlmCaptioner::new(PathBuf::from(dir), 40);
        assert!(cap.available(), "model files not found");
        let ocr = std::env::var("CAPTIONER_TEST_OCR").unwrap_or_default();
        let gist = cap
            .caption(Path::new(&img), "Visual Studio Code", "accessibility.rs — patcha", &ocr)
            .expect("caption");
        eprintln!("GIST: {gist}");
        assert!(gist.split_whitespace().count() >= 3, "gist too short: {gist:?}");
    }
}
