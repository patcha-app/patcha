# Phase 1 — Visual pre-filter + per-app cache

Implementation plan for the first perception phase. Companion to `perception.md`
("Phase 1"). Targets the Rust codebase at `rust/patcha/` (branch `feat/perception-rust`).

**Goal.** Give `AccessibilityCollector` its first dedup: a per-app MobileCLIP2 embedding
cache that drops redundant frames before they become events. Keep the 5s poll.
**Target: 40–70% fewer screen events** vs today's capture-every-tick behavior.

---

## Status (build in progress)

- **Step 0 — spike: PASS.** MobileCLIP-v1 S2 Core ML (256×256 → `final_emb_1` [1,512]),
  ANE ~2.4 ms/frame after warmup. Same frame cosine 0.999; different content 0.50–0.74. The
  0.97 drop threshold sits cleanly between. (MobileCLIP2-S2 is PyTorch-only on HF — no Core ML
  export — so v1 S2 is what ships until a v2 conversion is justified.)
- **Step 1 — Swift helper: DONE.** `patcha/macos/mobileclip.swift` — persistent stdin/stdout
  process, `{"ready":true}` handshake, survives bad frames. Verified standalone.
- **Step 2 — Rust module: DONE.** `src/perception/{mod,embedder,app_cache}.rs`. Unit tests
  (cosine, TTL) pass; Rust↔Swift roundtrip integration test passes (gated on `MOBILECLIP_TEST_*`).
- **Step 3 — cascade: DONE.** `record_current_screen` embeds the frame, drops on
  `transition=="same" && cosine>=0.97`, persists `visual_embedding` + `transition` +
  `_meta.visual_embedder`. Degrades to capture-every-tick if the helper is absent/fails.
- **Step 4 — storage: DEFERRED (as planned).** Vector lives in `screen_log.jsonl` only.
- **Step 5 — config: DONE.** `enable_visual_prefilter`, `visual_drop_threshold`,
  `visual_cache_ttl_seconds`, `visual_vector_size` in `Config` (Default + env).
- **Step 6 — packaging: DONE.** `build.sh` compiles `mobileclip`, fetches the `.mlpackage`,
  stages helpers + model into `Patcha.app/Contents/Resources/`. `data/` is gitignored.
- **Remaining: real-session validation** — run a multi-hour session, measure the before/after
  event-count reduction against the 40–70% target; tune the 0.97 threshold if needed.

`cargo build` green; `cargo test perception::` green.

---

## Decisions

| # | Decision | Choice |
|---|----------|--------|
| 1 | Visual embedder | **MobileCLIP2** (512-dim image encoder) |
| 2 | Runtime | **Persistent Swift Core ML process**, driven from Rust over stdin/stdout |
| 3 | Storage | **Defer** the visual-vector store — embedding lives in the event JSON only this phase |

**Why MobileCLIP2 via a Swift Core ML helper.** `build.sh` already `swiftc`-compiles
`ax_content.swift` / `ocr.swift` into `Patcha.app/Contents/Resources/`; a `mobileclip.swift`
helper slots into that pattern. Core ML runs MobileCLIP2 on the ANE (~10 ms/frame). Rust has
no mature Core ML binding, so in-process native inference is awkward.

**Alternative on record.** fastembed-rs v4 (already a dependency, used in `src/embedding.rs`
for text) supports `ImageEmbedding` (CLIP ViT-B-32, ONNX, in-process) — zero Swift helper,
mirrors the text path exactly. We chose MobileCLIP2 for encoder quality. If change-detection
quality proves indifferent to the encoder (likely — it's a relative same-vs-different
comparison, not semantic retrieval), fastembed-rs is the lower-surface fallback.

**Why defer storage.** The vector store is SQLite + sqlite-vec (`vec_events` vec0 table,
single `embedding` column). The entire event-reduction win lives in the collector and needs
no store change. Visual *search* / visual *dedup* (a second `vec_events_visual` table) is a
clean, separable follow-up.

---

## Model: MobileCLIP2 Core ML

- **Confirmed available:** `apple/coreml-mobileclip` (HF) ships ready-to-use Core ML image
  encoders for **MobileCLIP v1** — `mobileclip_s0/s1/s2/blt_image.mlpackage` (each a
  `model.mlmodel` + `weights/weight.bin`). No conversion tooling needed.
- **MobileCLIP2** (`apple/MobileCLIP2-S2`) ships PyTorch checkpoints; a published Core ML
  export is **unconfirmed**. If none exists: one-time offline PyTorch→Core ML conversion in a
  throwaway env (torch + coremltools + `mobileclip`); conversion tooling never ships in the app.
- **De-risk path:** prove the full pipeline (Swift load → `CGImage` → 512-dim vector on ANE)
  with **MobileCLIP-v1 S2** first — identical Swift code, only the `.mlpackage` swaps — then
  decide whether the v1→v2 quality delta justifies the conversion work.
- **Delivery:** ship the `.mlpackage` in `Patcha.app/Contents/Resources/` via `build.sh`
  (parallels the ax/ocr binaries; offline-first). Resolved at runtime via `resources_dir()`.

---

## Steps

### 0. Spike — prove the model *(gate)*
Throwaway Swift CLI: load the `.mlpackage`, take a screenshot `CGImage`, emit a deterministic
512-dim float vector. **Exit criteria:** similar frames → high cosine, different frames → low;
runs on ANE at ~10 ms. Everything below is gated on this.

*Status: toolchain verified (swiftc 6.3.2, coremlc, macOS 26.5, arm64). v1 `.mlpackage`s
located on HF. v2 Core ML export status outstanding.*

### 1. Swift helper — `patcha/macos/mobileclip.swift`
Persistent process (model load is too heavy to spawn per call). Reads image paths line-by-line
on **stdin**, writes `{"embedding":[...512]}` JSON lines on **stdout**. Image preprocessing
(resize/normalize) handled inside the Core ML model / via Vision. Add a
`swiftc patcha/macos/mobileclip.swift -o data/mobileclip` step to `build.sh` step 2.

### 2. Embedder + cache — new Rust module `src/perception/`
`mod.rs`, `embedder.rs`, `app_cache.rs`:
- **`MobileClipEmbedder`** — owns the long-lived Swift child (`std::process::Child`, piped
  stdin/stdout). `embed(image_path) -> Result<Vec<f32>>`. Lazy-spawn; relaunch on broken pipe;
  `Mutex`-guarded (runs on the daemon loop).
- **`AppEmbeddingCache`** — `HashMap<String, CacheEntry>` where
  `CacheEntry { visual_embedding: Vec<f32>, window_title, timestamp, source_doc_id }`.
  `compare(app, &vec) -> f32` (cosine), `update(...)`, TTL eviction (`cfg.cache_ttl`, 2h),
  invalidate on `window_title` change, bounded, cleared on restart.
- Construct in `src/daemon/loop.rs` (resolve the `mobileclip` binary via `resources_dir()`,
  like ax/ocr) and hand to `AccessibilityCollector::new`.

### 3. Cascade — `src/collectors/accessibility.rs` *(core change)*
The screenshot PNG already exists at OCR time (`screencapture` → `path.keep()` → `run_ocr`).
Embed it in that flow, then in `record_current_screen`, before `append_log_entry`:

1. **Title check** — app or window_title changed vs last → `transition = switch/new` →
   store, no drop, update cache.
2. **Cache check** (same title) — `cosine = cache.compare(app, &vec)`:
   - `>= 0.97` → **drop** (return early; do *not* append, do *not* update cache — preserves
     gradual-drift detection).
   - `< 0.97` → append event (OCR text path stays; the 0.85 VLM tier is Phase 3), update cache.

Persist `visual_embedding` + `_meta.visual_embedder` + restore the `transition` field in the
jsonl entry. Behind `cfg.enable_visual_prefilter`; if the embedder is unavailable, degrade to
capture-every-tick (optionally a cheap md5 frame-hash skip) with a logged warning.

### 4. Carry through to storage — `read_log` / `process.rs`
Surface `visual_embedding` from the jsonl entry into the in-memory event metadata in
`read_log`. Stays in the event JSON only — no `vec_events_visual` table this phase.

### 5. Config — `src/config.rs`
Add to `Config` (`Default` impl **and** the `from_env` block):
`visual_vector_size: usize = 512`, `cache_ttl: u64 = 7200`, `visual_drop_threshold: f32 = 0.97`,
`enable_visual_prefilter: bool = true`, visual model name/path.

### 6. Packaging + validation
`build.sh`: compile `mobileclip.swift`, copy the binary + `.mlpackage` into Resources.
**Validation:** event count over a 4-hour session, before/after → expect 40–70% reduction.
Unit tests: `AppEmbeddingCache` (cosine, TTL, title-invalidation) + the embedder protocol
(mock child process).

**Effort:** ~3–4 days; step 0 carries most of the risk.

---

## Code references (`rust/patcha/`)

- `src/collectors/accessibility.rs` — `AccessibilityCollector` (~23), `new` (~31),
  `record_current_screen` (~46, no dedup today), screencapture→`path.keep()` (~201–222),
  `run_ocr` (~224), `run_ax_content` (~173), `append_log_entry` (~243, `gist` currently a
  placeholder from `ax_info.focused_text`), `read_log` (~277).
- `src/embedding.rs` — text via fastembed-rs `TextEmbedding` (bge-base, 768, `OnceLock`);
  v4 also exposes `ImageEmbedding`.
- `src/db/store.rs` — `VectorStore`; sqlite-vec `vec_events(event_id, embedding)` (~68, KNN ~183).
  `vec_to_bytes` in `src/db/mod.rs`.
- `src/config.rs` — `Config` (~14), `Default` (~49), `from_env` (~128).
- `src/daemon/loop.rs` — `resources_dir()` (~108), `start()` (~127).
- `build.sh` — step 1 Swift menu-bar app, step 2 `swiftc` ax/ocr → `data/`, step 3
  `cargo build --release`, stage into `Resources/` + DMG.

## Already done (context, not Phase 1)
Local text embeddings (`src/embedding.rs`), Activity graph Phase A
(`src/db/activity_graph.rs`), knowledge graph + entity extraction + hybrid retrieval
(`src/db/entities.rs`, `src/db/retrieval/graphrag.rs`).
