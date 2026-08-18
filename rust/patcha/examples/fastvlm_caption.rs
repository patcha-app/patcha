// FastVLM caption A/B: old (crop, terse, no OCR) vs new (pad, OCR-grounded, detailed).
// Run: cargo run --release --example fastvlm_caption -- <model_dir> <image.png> [ocr_bin]

use anyhow::{anyhow, Result};
use image::imageops::FilterType;
use ndarray::{Array, Array3, Array4, Axis};
use ort::session::builder::GraphOptimizationLevel;
use ort::session::Session;
use ort::value::{Tensor, Value};
use std::process::Command;
use std::time::Instant;
use tokenizers::Tokenizer;

const IMAGE_SIZE: usize = 1024;
const HIDDEN: usize = 896;
const N_LAYERS: usize = 24;
const KV_HEADS: usize = 2;
const HEAD_DIM: usize = 64;
const EOS: i64 = 151645;

struct Opts {
    pad: bool,
    max_new: usize,
    detailed: bool,
    ocr: Option<String>,
}

fn main() -> Result<()> {
    let mut args = std::env::args().skip(1);
    let dir = args
        .next()
        .ok_or_else(|| anyhow!("usage: <model_dir> <image> [ocr_bin]"))?;
    let image_path = args.next().ok_or_else(|| anyhow!("missing image"))?;
    let ocr_bin = args
        .next()
        .unwrap_or_else(|| "/Users/xtanion/patcha-app/patcha/data/ocr".into());

    let app = "Zed";
    let window = "patcha";

    let tokenizer =
        Tokenizer::from_file(format!("{dir}/tokenizer.json")).map_err(|e| anyhow!("{e}"))?;
    let build = || Session::builder()?.with_optimization_level(GraphOptimizationLevel::Disable);
    let vision = build()?.commit_from_file(format!("{dir}/onnx/vision_encoder_q4f16.onnx"))?;
    let embed = build()?.commit_from_file(format!("{dir}/onnx/embed_tokens_q4f16.onnx"))?;
    let decoder = build()?.commit_from_file(format!("{dir}/onnx/decoder_model_merged_q4.onnx"))?;

    let ocr = ocr_snippet(&ocr_bin, &image_path).ok();
    if let Some(o) = &ocr {
        eprintln!(
            "OCR snippet ({} chars): {}…\n",
            o.len(),
            &o[..o.len().min(160)]
        );
    }

    let v1 = Opts {
        pad: false,
        max_new: 40,
        detailed: false,
        ocr: None,
    };
    let v2 = Opts {
        pad: true,
        max_new: 100,
        detailed: true,
        ocr: ocr.clone(),
    };

    for (label, opts) in [
        ("V1 OLD (crop, terse, no OCR)", v1),
        ("V2 NEW (pad, OCR, detailed)", v2),
    ] {
        let t = Instant::now();
        let gist = caption(
            &vision,
            &embed,
            &decoder,
            &tokenizer,
            &image_path,
            app,
            window,
            &opts,
        )?;
        println!(
            "\n=== {label} | {:.1}s ===\n{gist}",
            t.elapsed().as_secs_f32()
        );
    }
    Ok(())
}

fn ocr_snippet(ocr_bin: &str, image: &str) -> Result<String> {
    let out = Command::new(ocr_bin).arg(image).output()?;
    let obs: Vec<serde_json::Value> = serde_json::from_slice(&out.stdout)?;
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
    // Top-to-bottom (y is bottom-left origin, so descending), then left-to-right.
    items.sort_by(|a, b| {
        b.0.partial_cmp(&a.0)
            .unwrap()
            .then(a.1.partial_cmp(&b.1).unwrap())
    });
    let text: String = items
        .iter()
        .map(|(_, _, t)| t.as_str())
        .collect::<Vec<_>>()
        .join(" ");
    Ok(text.chars().take(600).collect())
}

#[allow(clippy::too_many_arguments)]
fn caption(
    vision: &Session,
    embed: &Session,
    decoder: &Session,
    tokenizer: &Tokenizer,
    image: &str,
    app: &str,
    window: &str,
    opts: &Opts,
) -> Result<String> {
    let prompt = if opts.detailed {
        let ocr = opts.ocr.as_deref().unwrap_or("");
        format!(
            "The active app is {app}, window '{window}'. Visible on-screen text includes: {ocr}. \
             In 2-3 sentences, describe specifically what the user is doing — mention the files, \
             code, UI panels, or content actually visible."
        )
    } else {
        format!(
            "The active app is {app} and the window title is {window}. \
             In one sentence, describe what the user is doing."
        )
    };
    let before =
        "<|im_start|>system\nYou are observing a user's screen.<|im_end|>\n<|im_start|>user\n";
    let after = format!("\n{prompt}<|im_end|>\n<|im_start|>assistant\n");

    // Vision.
    let pixels = preprocess(image, opts.pad)?;
    let v_out = vision.run(ort::inputs![ "pixel_values" => Tensor::from_array(pixels)? ]?)?;
    let (vshape, vdata) = v_out["image_features"].try_extract_raw_tensor::<f32>()?;
    let n_img = vshape[1] as usize;
    let image_features = Array3::from_shape_vec((1, n_img, HIDDEN), vdata.to_vec())?;

    let before_ids = encode(tokenizer, before)?;
    let after_ids = encode(tokenizer, &after)?;
    let e_before = run_embed(embed, &before_ids)?;
    let e_after = run_embed(embed, &after_ids)?;
    let mut embeds = ndarray::concatenate(
        Axis(1),
        &[e_before.view(), image_features.view(), e_after.view()],
    )?;

    let mut past = empty_kv()?;
    let mut past_len = 0usize;
    let mut generated: Vec<u32> = Vec::new();

    for _ in 0..opts.max_new {
        let cur_seq = embeds.shape()[1];
        let total = past_len + cur_seq;
        let attn: Vec<i64> = vec![1; total];
        let pos: Vec<i64> = (past_len as i64..total as i64).collect();
        let embeds_t = Tensor::from_array((
            [1, cur_seq, HIDDEN],
            embeds
                .as_standard_layout()
                .iter()
                .copied()
                .collect::<Vec<f32>>(),
        ))?;
        let mut inputs: Vec<(String, Value)> = vec![
            ("inputs_embeds".into(), embeds_t.into_dyn()),
            (
                "attention_mask".into(),
                Tensor::from_array(([1, total], attn))?.into_dyn(),
            ),
            (
                "position_ids".into(),
                Tensor::from_array(([1, cur_seq], pos))?.into_dyn(),
            ),
        ];
        inputs.append(&mut past);
        let outputs = decoder.run(inputs)?;
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
        embeds = run_embed(embed, &[next])?;
    }
    Ok(tokenizer
        .decode(&generated, true)
        .map_err(|e| anyhow!("{e}"))?
        .trim()
        .to_string())
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

// pad=true: letterbox the whole image into 1024² (preserves the full screen).
// pad=false: resize shortest edge to 1024, center-crop (loses screen edges).
fn preprocess(path: &str, pad: bool) -> Result<Array4<f32>> {
    let img = image::open(path)?.to_rgb8();
    let (w, h) = img.dimensions();
    let mut arr = Array::zeros((1, 3, IMAGE_SIZE, IMAGE_SIZE));
    if pad {
        let scale = IMAGE_SIZE as f32 / w.max(h) as f32;
        let nw = ((w as f32 * scale).round() as u32).max(1);
        let nh = ((h as f32 * scale).round() as u32).max(1);
        let resized = image::imageops::resize(&img, nw, nh, FilterType::CatmullRom);
        let ox = (IMAGE_SIZE as u32 - nw) / 2;
        let oy = (IMAGE_SIZE as u32 - nh) / 2;
        for y in 0..nh {
            for x in 0..nw {
                let px = resized.get_pixel(x, y);
                for c in 0..3 {
                    arr[[0, c, (oy + y) as usize, (ox + x) as usize]] = px[c] as f32 / 255.0;
                }
            }
        }
    } else {
        let scale = IMAGE_SIZE as f32 / w.min(h) as f32;
        let nw = (w as f32 * scale).round() as u32;
        let nh = (h as f32 * scale).round() as u32;
        let resized = image::imageops::resize(&img, nw, nh, FilterType::CatmullRom);
        let cx = (nw - IMAGE_SIZE as u32) / 2;
        let cy = (nh - IMAGE_SIZE as u32) / 2;
        for y in 0..IMAGE_SIZE {
            for x in 0..IMAGE_SIZE {
                let px = resized.get_pixel(cx + x as u32, cy + y as u32);
                for c in 0..3 {
                    arr[[0, c, y, x]] = px[c] as f32 / 255.0;
                }
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
    Ok(Array3::from_shape_vec(
        (1, shape[1] as usize, HIDDEN),
        data.to_vec(),
    )?)
}

fn empty_kv() -> Result<Vec<(String, Value)>> {
    let mut kv = Vec::new();
    for i in 0..N_LAYERS {
        for which in ["key", "value"] {
            let a = Array4::<f32>::from_shape_vec((1, KV_HEADS, 0, HEAD_DIM), Vec::new())?;
            kv.push((
                format!("past_key_values.{i}.{which}"),
                Tensor::from_array(a)?.into_dyn(),
            ));
        }
    }
    Ok(kv)
}
