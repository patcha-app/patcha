# Phase 1 Implementation Plan — Visual pre-filter + per-app cache

Companion to `perception.md` (see its "Phase 1" section). This is the concrete,
codebase-grounded plan for the first perception phase.

**Goal:** Replace the 80% text-diff heuristic in `AccessibilityCollector` with
cosine-similarity change detection against a per-app MobileCLIP2 embedding cache.
Keep the existing 5s poll. Target: 40–70% fewer screen events (kills scroll
false-positives + app-return dupes).

---

## Decisions locked in

1. **Embedder: MobileCLIP2** (not CLIP ViT-B-32). Chosen for the stronger encoder
   and because Phase 3 will stand up the helper anyway; accept a one-time
   visual-vector reset if the model is swapped later (embeddings aren't comparable
   across models — bump `_meta.visual_embedder`).

2. **Runtime: persistent Swift Core ML process, NOT the doc's Python+MLX helper.**
   - The app ships via **PyInstaller** (`_FROZEN`/`_MEIPASS` in `accessibility.py`).
     torch/open_clip would balloon the bundle (~2 GB); MLX-in-PyInstaller is unproven.
   - The current ML stack is ONNX-only (`fastembed`); no torch/mlx/coremltools installed.
   - There is an established Swift-helper pattern already (`ax_content.swift`,
     `ocr.swift` compiled via `swiftc`). MobileCLIP2 ships as Core ML → runs on the
     ANE natively.
   - So the doc's `perception/helper.py` + `helper_server.py` collapse into:
     - `patcha/macos/mobileclip.swift` — persistent Core ML process (replaces `helper_server.py`)
     - `patcha/perception/embedder.py` — Python subprocess manager/client (replaces `helper.py`)

3. **Storage: DEFER the Qdrant named-vector migration.** Phase 1 computes the visual
   embedding, drives the per-app cache diff, and stashes the vector in
   `screen_log.jsonl` + event payload — NOT yet as a searchable named Qdrant vector.
   This captures the full event-reduction win (which lives entirely in
   `accessibility.py`) without the breaking `mem`-collection migration. Visual
   search + visual dedup become a clean follow-up.

---

## Spike progress (step 0, in flight)

Verified on this machine:
- Toolchain: `swiftc` 6.3.2, `xcrun`, `coremlc` present; macOS 26.5, arm64; HF reachable.
- **Ready-made Core ML image encoders exist** at `apple/coreml-mobileclip` (HF) — these
  are **MobileCLIP v1**: `mobileclip_s0/s1/s2/blt_image.mlpackage` (+ text encoders).
  Drop-in `.mlpackage`, no torch/coremltools conversion needed.
- `apple/MobileCLIP2-S2` / `MobileCLIP2-S0` host **PyTorch** checkpoints (open_clip-like
  API). Whether they ship a Core ML export was being checked when the spike paused.

**Implication / open call:** If MobileCLIP2 has no published Core ML export, options are:
  - (a) one-time offline conversion PyTorch→Core ML (needs torch + coremltools +
    `mobileclip` pkg in a throwaway env; conversion tooling does NOT ship in the app), or
  - (b) prove the whole Swift+CoreML+512-dim+ANE pipeline first with the already-exported
    **MobileCLIP-v1 S2** `.mlpackage` (identical Swift code; only the model file swaps),
    then decide if the v1→v2 quality delta justifies the conversion work.
  Recommendation: do (b) for the spike to de-risk the pipeline, revisit v2 conversion after.

Spike exit criteria: a throwaway Swift CLI loads the `.mlpackage`, takes a screenshot
`CGImage`, emits a deterministic 512-dim float vector; two similar frames → high cosine,
two different → low; runs on ANE at ~10 ms.

**Model delivery:** download-on-first-run into `config.embedding_cache_dir`
(mirrors how fastembed fetches text weights) — preferred over bundling in Resources.

---

## Work breakdown (sequenced to de-risk the biggest unknown first)

### 0. Spike — MobileCLIP2 Core ML model  *(in progress — see above)*
Prove the model loads in a throwaway Swift CLI and emits a usable 512-dim image
embedding on the ANE. Gate the rest of the phase on this.

### 1. Swift embedder helper — `patcha/macos/mobileclip.swift`
- Persistent process (model load too heavy to spawn-per-call like ax/ocr). Reads image
  paths line-by-line on **stdin**, writes `{"embedding":[...512]}` JSON lines on **stdout**.
- Image preprocessing (resize/normalize) handled inside the Core ML model / via Vision —
  Python never replicates CLIP preprocessing.
- Compiled through the existing `_compile()` swiftc path; `_FROZEN`/`_MEIPASS` lookup for
  the bundled binary, same as ax/ocr.

### 2. Perception module — `patcha/perception/`
- `__init__.py`
- `embedder.py` — `MobileClipEmbedder`: spawns/owns the long-lived Swift process,
  `embed(image_path) -> list[float]`, thread-safe (collector calls from its capture
  thread), lazy-spawn on first use, health-check + relaunch on pipe failure.
- `app_cache.py` — `AppEmbeddingCache`: per-app
  `{visual_embedding, window_title, timestamp, source_doc_id}`; `compare(app, vec) ->
  cosine`, `update(...)`, TTL eviction (`config.cache_ttl`, default 2h), invalidate entry
  on `window_title` change, bounded size, cleared on restart.

### 3. Rewire the diff cascade in `accessibility.py`  *(core change)*
- **Plumb the PNG to the embedder.** Today `_take_ocr_screenshot` deletes the tmp PNG in
  its `finally` (lines ~467–471). Compute the visual embedding there while the file
  exists, return it in the `data` dict alongside `text`.
- **Replace the text-diff block** (lines ~545–577: `_content_diff` / `_last_text` /
  `_FULL_REPLACE_RATIO`) with:
  1. **Title check** (`_last_active_key`): app or window_title changed → `transition =
     switch/new` → store, no drop, update cache.
  2. **Cache check** (same title): `cosine = app_cache.compare(app, vec)`
     - `>= 0.97` → **drop** (no event, cache NOT updated — so gradual drift is still caught).
     - `< 0.97` → store event (Phase 1 keeps the existing AX/OCR text path; the 0.85 VLM
       tier lands in Phase 3), update cache.
- Keep the existing md5 screenshot short-circuit (lines ~422–427) as a free pre-check
  before embedding (identical bytes → skip).
- **Persist `visual_embedding`** into the `screen_log.jsonl` entry + `_meta.visual_embedder`
  version tag.
- **Resilience:** behind an `enable_visual_prefilter` flag; if the embedder fails to
  load/crashes, log a warning and **fall back to the legacy text-diff branch** (keep
  `_content_diff`/`_last_text` intact for one release, per the doc's risk note).

### 4. Carry the embedding to storage — `collect_screen_text()` + `process.py`
- Add `visual_embedding` into the event dict/metadata in `collect_screen_text`
  (lines ~693–716). (Storage stays in payload/metadata only — migration deferred.)

### 5. Config — `config/config.py` + `config/settings.py`
- `visual_vector_size = 512`, `cache_ttl = 7200`, `visual_drop_threshold = 0.97`,
  `enable_visual_prefilter` (default on, with legacy fallback), visual model name/path.

### 6. Packaging + validation
- PyInstaller spec: include the compiled `mobileclip` binary + the `.mlpackage` in the
  bundle (alongside the ax/ocr binaries and applescript).
- **Validation:** event count over a 4-hour session, before/after → expect 40–70%
  reduction. Unit tests for `app_cache` (TTL, cosine, title-invalidation) and the embedder
  protocol (mocked subprocess).

**Realistic effort:** doc estimates 2–3 days; the Core ML spike + persistent Swift helper
push it to ~3–4 days, with step 0 carrying most of the risk.

---

## Key code references (as of this branch)

- `patcha/collectors/accessibility.py`
  - text-diff to replace: `_content_diff` (~522), diff block (~545–577), `_FULL_REPLACE_RATIO` (48)
  - screenshot capture (PNG lifecycle): `_take_ocr_screenshot` (~378–471)
  - jsonl entry written: `record_current_screen` (~586–606)
  - readback for storage: `collect_screen_text` (~651–718)
  - poll loop: `_run_loop` (~628), `_POLL_INTERVAL = settings.get("ax_poll_interval")` (36)
  - swiftc compile pattern: `_compile` (~116–136)
- `patcha/process.py` — `_build_embedding_text` (55), embed flow (~109–137). Text embeds
  in-process via `patcha/embedding.py` (fastembed, bge-base, 768-dim, lru_cache).
- `patcha/db/store.py` — `mem` collection uses a **single unnamed vector**
  (`_ensure_collection_exists` ~60, `store_event` ~134). Named-vector migration deferred.
- `patcha/config/config.py` — `collection_name="mem"`, `vector_size=768`,
  `embedding_model_name="BAAI/bge-base-en-v1.5"`, `embedding_cache_dir=.../models`.
- `patcha/config/settings.py` — sqlite-backed settings, `get`/`put`; defaults dataclass.

## What's already done (not Phase 1, for context)
- Local text embeddings via fastembed/ONNX (`embedding.py`).
- Activity graph Phase A (`db/activity_graph.py`, SQLite not FalkorDB).
- Knowledge graph + entity extraction + hybrid retrieval (`db/entities.py`,
  `db/retrieval/graphrag.py`).
