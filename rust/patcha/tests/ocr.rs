//! OCR tests: render mock app-window screenshots and run the real `ocr` Swift
//! helper over them, asserting the recognized text matches what we drew.
//!
//! These exercise the exact perception path the daemon uses. They skip cleanly
//! (with a printed note) when the `ocr` helper hasn't been built — run
//! `./build.sh` or point `PATCHA_RESOURCES` at a dir containing `ocr`.

mod common;

use common::{load_font, norm, run_ocr, MockWindow};

/// Number of `needles` that appear in `haystack` (case-insensitive).
fn hits(haystack: &str, needles: &[&str]) -> usize {
    let h = norm(haystack);
    needles.iter().filter(|n| h.contains(&norm(n))).count()
}

macro_rules! skip_if_no_ocr {
    ($img:expr) => {
        match run_ocr($img) {
            Some(t) => t,
            None => {
                eprintln!("skipping: `ocr` helper not built (run ./build.sh or set PATCHA_RESOURCES)");
                return;
            }
        }
    };
}

#[test]
fn ocr_reads_vscode_window() {
    let font = load_font();
    let win = MockWindow::new(
        "Visual Studio Code",
        "accessibility.rs — patcha",
        &[
            "pub fn record current screen",
            "let ocr text equals self run ocr",
            "capture window of the active app",
            "append log entry to screen log",
        ],
    );
    let (_dir, path) = win.render(&font);
    let text = skip_if_no_ocr!(&path);

    let expected = ["accessibility", "patcha", "record", "screen", "capture", "window"];
    let found = hits(&text, &expected);
    assert!(
        found >= expected.len() - 1,
        "expected most of {expected:?} in OCR output, found {found}.\nOCR: {text}"
    );
}

#[test]
fn ocr_reads_terminal_window() {
    let font = load_font();
    let win = MockWindow::new(
        "Terminal",
        "zsh — patcha",
        &[
            "cargo test ocr reads terminal",
            "git status shows modified files",
            "running the full pipeline suite",
        ],
    );
    let (_dir, path) = win.render(&font);
    let text = skip_if_no_ocr!(&path);

    let expected = ["cargo", "test", "terminal", "git", "status", "pipeline"];
    let found = hits(&text, &expected);
    assert!(
        found >= expected.len() - 1,
        "expected most of {expected:?} in OCR output, found {found}.\nOCR: {text}"
    );
}

#[test]
fn ocr_reads_browser_window() {
    let font = load_font();
    let win = MockWindow::new(
        "Google Chrome",
        "Pull Request — GitHub",
        &[
            "Merge feature perception rust into main",
            "Add Rust OCR and pipeline test suite",
            "Reviewers approved the changes",
        ],
    );
    let (_dir, path) = win.render(&font);
    let text = skip_if_no_ocr!(&path);

    let expected = ["merge", "perception", "rust", "github", "pipeline", "test"];
    let found = hits(&text, &expected);
    assert!(
        found >= expected.len() - 1,
        "expected most of {expected:?} in OCR output, found {found}.\nOCR: {text}"
    );
}

/// Different app windows must produce clearly different OCR text — guards against
/// a regression where capture/OCR returns stale or empty content.
#[test]
fn ocr_distinguishes_different_apps() {
    let font = load_font();

    let editor = MockWindow::new("Xcode", "ContentView.swift", &["struct ContentView some View"]);
    let chat = MockWindow::new("Slack", "engineering channel", &["shipping the rust rewrite today"]);

    let (_d1, p1) = editor.render(&font);
    let (_d2, p2) = chat.render(&font);

    let t1 = skip_if_no_ocr!(&p1);
    let t2 = norm(&match run_ocr(&p2) {
        Some(t) => t,
        None => return,
    });
    let t1 = norm(&t1);

    assert!(t1.contains("contentview") || t1.contains("swift"), "editor OCR: {t1}");
    assert!(t2.contains("rust") || t2.contains("slack") || t2.contains("rewrite"), "chat OCR: {t2}");
    assert_ne!(t1, t2, "distinct windows produced identical OCR text");
}

/// Opt-in: open a real macOS window (TextEdit), screenshot it, and OCR it. Needs
/// a GUI session + Screen Recording permission, so it's ignored by default.
/// Run with: `cargo test -- --ignored live_window_ocr`
#[test]
#[ignore]
fn live_window_ocr() {
    use std::process::Command;
    use std::time::Duration;

    let dir = tempfile::tempdir().unwrap();
    let doc = dir.path().join("patcha_live_ocr.txt");
    std::fs::write(&doc, "patcha live window ocr smoke test\nrust pipeline verification").unwrap();

    Command::new("open").arg("-a").arg("TextEdit").arg(&doc).status().unwrap();
    std::thread::sleep(Duration::from_secs(2));

    let shot = dir.path().join("shot.png");
    let status = Command::new("screencapture").arg("-x").arg(&shot).status().unwrap();
    assert!(status.success(), "screencapture failed");

    let text = norm(&run_ocr(&shot).expect("ocr helper required for live test"));
    Command::new("osascript")
        .arg("-e")
        .arg("tell application \"TextEdit\" to close every window saving no")
        .status()
        .ok();

    assert!(
        text.contains("patcha") || text.contains("pipeline") || text.contains("smoke"),
        "live OCR did not read the TextEdit content: {text}"
    );
}
