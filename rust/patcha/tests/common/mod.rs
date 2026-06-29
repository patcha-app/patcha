//! Shared helpers for the OCR + pipeline integration tests.
//!
//! These build synthetic "app window" screenshots (different apps, real rendered
//! text) and drive the same `ocr` Swift helper the daemon uses, so the tests
//! cover the real perception path end to end without needing a live screen.

#![allow(dead_code)]

use ab_glyph::{FontVec, PxScale};
use image::{Rgb, RgbImage};
use imageproc::drawing::draw_text_mut;
use std::path::{Path, PathBuf};
use std::process::Command;

/// macOS system fonts to try, in order. The first that loads wins. Vision OCR
/// reads these cleanly at the sizes we render.
const FONT_CANDIDATES: &[&str] = &[
    "/System/Library/Fonts/Supplemental/Arial.ttf",
    "/System/Library/Fonts/Supplemental/Verdana.ttf",
    "/System/Library/Fonts/SFNSMono.ttf",
    "/System/Library/Fonts/SFNS.ttf",
    "/Library/Fonts/Arial.ttf",
    "/System/Library/Fonts/Helvetica.ttc",
];

/// Repo root, derived from the crate manifest dir (`<root>/rust/patcha`).
pub fn repo_root() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("../..")
        .canonicalize()
        .expect("resolve repo root")
}

/// Locate a built resource (the `ocr` / `ax_content` Swift helpers).
///
/// Honors `PATCHA_RESOURCES` (same env the daemon reads) and falls back to the
/// repo's `data/` dir where `build.sh` writes the compiled helpers.
pub fn resource_path(name: &str) -> Option<PathBuf> {
    if let Ok(dir) = std::env::var("PATCHA_RESOURCES") {
        let p = Path::new(&dir).join(name);
        if p.exists() {
            return Some(p);
        }
    }
    let p = repo_root().join("data").join(name);
    p.exists().then_some(p)
}

/// Load the first available system font.
pub fn load_font() -> FontVec {
    for path in FONT_CANDIDATES {
        let Ok(bytes) = std::fs::read(path) else {
            continue;
        };
        if let Ok(font) = FontVec::try_from_vec(bytes.clone()) {
            return font;
        }
        // `.ttc` collections need an explicit face index.
        if let Ok(font) = FontVec::try_from_vec_and_index(bytes, 0) {
            return font;
        }
    }
    panic!("no usable system font found in {FONT_CANDIDATES:?}");
}

/// A synthetic screenshot of an application window.
pub struct MockWindow {
    pub app: String,
    pub title: String,
    pub body: Vec<String>,
    pub width: u32,
    pub height: u32,
}

impl MockWindow {
    pub fn new(app: &str, title: &str, body: &[&str]) -> Self {
        Self {
            app: app.to_string(),
            title: title.to_string(),
            body: body.iter().map(|s| s.to_string()).collect(),
            width: 1280,
            height: 800,
        }
    }

    /// Render the window to a PNG and return the path. The file lives in a unique
    /// temp dir the caller is responsible for keeping alive (via the returned
    /// `tempfile::TempDir`).
    pub fn render(&self, font: &FontVec) -> (tempfile::TempDir, PathBuf) {
        let dir = tempfile::tempdir().expect("tempdir");
        let path = dir.path().join(format!(
            "{}.png",
            self.app.replace(|c: char| !c.is_alphanumeric(), "_")
        ));

        let mut img = RgbImage::from_pixel(self.width, self.height, Rgb([250, 250, 250]));

        // Title bar: dark band with the app name + window title.
        for y in 0..72 {
            for x in 0..self.width {
                img.put_pixel(x, y, Rgb([40, 44, 52]));
            }
        }
        let title_text = format!("{}  —  {}", self.app, self.title);
        draw_line(&mut img, font, &title_text, 24, 18, 34.0, Rgb([235, 235, 235]));

        // Body: dark text on the light canvas, one line per entry.
        let mut y = 110i32;
        for line in &self.body {
            draw_line(&mut img, font, line, 32, y, 30.0, Rgb([20, 20, 20]));
            y += 46;
        }

        img.save(&path).expect("save mock window png");
        (dir, path)
    }
}

fn draw_line(img: &mut RgbImage, font: &FontVec, text: &str, x: i32, y: i32, px: f32, color: Rgb<u8>) {
    draw_text_mut(img, color, x, y, PxScale::from(px), font, text);
}

/// Recognized text observation from the `ocr` Swift helper.
#[derive(serde::Deserialize)]
struct TextObs {
    text: String,
}

/// Run the real `ocr` helper on an image and return the concatenated recognized
/// text. Returns `None` if the helper isn't built (so callers can skip cleanly).
pub fn run_ocr(image_path: &Path) -> Option<String> {
    let bin = resource_path("ocr")?;
    let out = Command::new(&bin)
        .arg(image_path)
        .output()
        .expect("spawn ocr binary");
    assert!(
        out.status.success(),
        "ocr binary failed: {}",
        String::from_utf8_lossy(&out.stderr)
    );
    let stdout = String::from_utf8_lossy(&out.stdout);
    let obs: Vec<TextObs> = serde_json::from_str(stdout.trim()).unwrap_or_default();
    Some(
        obs.into_iter()
            .map(|o| o.text)
            .collect::<Vec<_>>()
            .join(" "),
    )
}

/// Lowercase a string for case-insensitive substring assertions.
pub fn norm(s: &str) -> String {
    s.to_lowercase()
}
