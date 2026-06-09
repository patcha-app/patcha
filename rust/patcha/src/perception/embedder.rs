use anyhow::{anyhow, Context, Result};
use std::io::{BufRead, BufReader, Write};
use std::path::{Path, PathBuf};
use std::process::{Child, ChildStdin, ChildStdout, Command, Stdio};

/// Client for the persistent `mobileclip` Swift helper.
///
/// The helper loads the Core ML model once and serves one embedding per stdin
/// line. The child is spawned lazily on first use and respawned automatically
/// if the pipe breaks, so a single bad frame never wedges the collector.
pub struct MobileClipEmbedder {
    binary: PathBuf,
    model: PathBuf,
    running: Option<Running>,
}

struct Running {
    child: Child,
    stdin: ChildStdin,
    stdout: BufReader<ChildStdout>,
}

impl MobileClipEmbedder {
    pub fn new(binary: PathBuf, model: PathBuf) -> Self {
        Self {
            binary,
            model,
            running: None,
        }
    }

    /// Whether the helper binary and model are both present on disk.
    pub fn available(&self) -> bool {
        self.binary.exists() && self.model.exists()
    }

    fn spawn(&mut self) -> Result<()> {
        let mut child = Command::new(&self.binary)
            .arg(&self.model)
            .stdin(Stdio::piped())
            .stdout(Stdio::piped())
            .stderr(Stdio::null())
            .spawn()
            .with_context(|| format!("spawning mobileclip helper {:?}", self.binary))?;

        let stdin = child.stdin.take().ok_or_else(|| anyhow!("no stdin"))?;
        let stdout = child.stdout.take().ok_or_else(|| anyhow!("no stdout"))?;
        let mut stdout = BufReader::new(stdout);

        // The helper emits {"ready":true} once the model is loaded.
        let mut line = String::new();
        stdout.read_line(&mut line)?;
        let v: serde_json::Value = serde_json::from_str(line.trim()).unwrap_or_default();
        if v.get("ready").and_then(|b| b.as_bool()) != Some(true) {
            return Err(anyhow!("mobileclip helper not ready: {}", line.trim()));
        }

        self.running = Some(Running {
            child,
            stdin,
            stdout,
        });
        Ok(())
    }

    /// Embed the image at `image_path`, returning a 512-d vector.
    /// On any IO/protocol error the child is dropped so the next call respawns.
    pub fn embed(&mut self, image_path: &Path) -> Result<Vec<f32>> {
        if self.running.is_none() {
            self.spawn()?;
        }
        let result = self.embed_once(image_path);
        if result.is_err() {
            self.running = None;
        }
        result
    }

    fn embed_once(&mut self, image_path: &Path) -> Result<Vec<f32>> {
        let r = self.running.as_mut().ok_or_else(|| anyhow!("no helper"))?;
        writeln!(r.stdin, "{}", image_path.display())?;
        r.stdin.flush()?;

        let mut line = String::new();
        if r.stdout.read_line(&mut line)? == 0 {
            return Err(anyhow!("mobileclip helper closed its output"));
        }
        let v: serde_json::Value = serde_json::from_str(line.trim())?;
        if let Some(err) = v.get("error").and_then(|e| e.as_str()) {
            return Err(anyhow!("mobileclip: {err}"));
        }
        let arr = v
            .get("embedding")
            .and_then(|e| e.as_array())
            .ok_or_else(|| anyhow!("mobileclip: response missing embedding"))?;
        Ok(arr
            .iter()
            .filter_map(|x| x.as_f64().map(|f| f as f32))
            .collect())
    }
}

impl Drop for MobileClipEmbedder {
    fn drop(&mut self) {
        if let Some(mut r) = self.running.take() {
            let _ = r.child.kill();
            let _ = r.child.wait();
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::perception::cosine;

    // Round-trip against the real Swift helper. Skips unless the staged artifacts
    // are pointed to by env (so normal `cargo test` runs without a model present):
    //   MOBILECLIP_TEST_BIN, MOBILECLIP_TEST_MODEL, MOBILECLIP_TEST_IMG
    #[test]
    fn roundtrip_against_helper() {
        let (Ok(bin), Ok(model), Ok(img)) = (
            std::env::var("MOBILECLIP_TEST_BIN"),
            std::env::var("MOBILECLIP_TEST_MODEL"),
            std::env::var("MOBILECLIP_TEST_IMG"),
        ) else {
            eprintln!("skipping: MOBILECLIP_TEST_* not set");
            return;
        };

        let mut emb = MobileClipEmbedder::new(PathBuf::from(bin), PathBuf::from(model));
        assert!(emb.available(), "helper binary/model not found");

        let img = Path::new(&img);
        let a = emb.embed(img).expect("embed a");
        let b = emb.embed(img).expect("embed b (reused helper)");
        assert_eq!(a.len(), 512, "expected 512-d vector");
        // Same image twice -> identical vector (and the helper stayed resident).
        assert!(cosine(&a, &b) > 0.999, "self-cosine = {}", cosine(&a, &b));
    }
}
