# Phase 2 & 3 — Triggers + Gist captioning

Follows `perception-phase1-plan.md`. Phase 1 (visual pre-filter + per-app cache) is
done. Targets `rust/patcha/` (branch `feat/perception-rust`).

---

## Phase 2 — Event-driven triggers — DONE

Replaced the fixed 5s accessibility poll with instant event-driven capture + a relaxed
background poll, debounced and idle-gated.

**Built & verified:**
- **`patcha/macos/observer.swift`** — persistent Swift process; emits one JSON trigger per
  line: `app_switch` (`NSWorkspace.didActivateApplication`), `space_switch`
  (`activeSpaceDidChange`), `title_change` (AXObserver on the focused window, retargeted on
  each app switch), `screen_lock`/`screen_unlock` (`DistributedNotificationCenter`).
  Smoke-tested live: real app switches + title changes with correct window titles.
- **`src/triggers/idle.rs`** — `IdleDetector` over `CGEventSourceSecondsSinceLastEventType`
  (seconds-since-last-input only; no event tap, no permission, no keystroke access). Edge
  detector: `WentIdle` / `Resumed`. (Cleaner than the doc's `CGEventTap`.)
- **`src/triggers/observer.rs`** — `ObserverHandle`: spawns observer.swift, drains stdout on
  a thread, parses lines → `Trigger` on an mpsc channel; thread exits on EOF (crash signal).
- **`src/triggers/mod.rs`** — `TriggerKind`, `CaptureDepth`, `Trigger`, and the depth-routing
  table (app_switch/space_switch/idle_resume/unlock → Full; title_change → Medium; poll_tick
  → Light; screen_lock → None).
- **`src/triggers/coordinator.rs`** — `coordinator::run`: 100ms heartbeat servicing
  (a) debounce (500ms, collapses alt-tab bursts), (b) idle edges (pause poll on idle, capture
  on resume), (c) relaxed background poll (15s) for within-window drift while active.
  screen_lock pauses; screen_unlock resumes + captures. Each capture calls
  `record_current_screen` via `spawn_blocking`; the per-app cache still owns drop-vs-store.
- **`src/daemon/loop.rs`** — picks the coordinator when `enable_event_triggers` and the
  `observer` binary exists; otherwise falls back to the fixed poll (degraded mode, logged).
- **`src/config.rs`** — `enable_event_triggers` (true), `idle_timeout_seconds` (120),
  `trigger_debounce_ms` (500), `background_poll_interval_seconds` (15), + env overrides.
- **`build.sh`** — compiles `observer`, stages it into `Resources/`.

**Tests:** 15 pass (idle edges, observer JSON parse, depth routing, + Phase 1 cache/embedder).
`cargo build` green.

**Remaining (manual):** live-daemon validation — run a full session, confirm context switches
captured <500ms, ~60% fewer captures, zero captures while idle. Not run here (would write to
the user's real event store).

**Design note:** the coordinator decides *whether/when* to capture; the Phase 1 cascade inside
`record_current_screen` still decides drop-vs-store. Depth routing currently only gates capture
(None vs not); it becomes meaningful in Phase 3 when Full/Medium trigger the VLM captioner.

---

## Phase 3 — Gist captioning — DONE (ONNX in-process)

FastVLM-0.5B (LLaVA-Qwen2) runs in-process via `ort` — no MLX, no SwiftPM, no Python. A spike
proved a coherent caption; the production module was ported from it.

**Built:**
- `src/perception/captioner.rs` — `FastVlmCaptioner`: lazy-loads 3 ort sessions;
  `caption(image, app, window) -> String`. Full decode loop: preprocess (1024px, /255) →
  vision_encoder → `[embed(before) | 256 image feats | embed(after)]` (splice avoids the OOB
  `<image>`=151646 sentinel) → greedy KV-cache decode over the Qwen2 merged decoder (24 layers,
  2 KV heads, head_dim 64) → detokenize at EOS 151645.
- `src/collectors/accessibility.rs` — captioner invoked on context switches
  (`transition != "same"`), while the screenshot is still on disk; `gist` persisted to
  `screen_log.jsonl` with `_meta.captioner`. Degrades cleanly (gist=None) if the model is absent.
- `src/process.rs` — screen-event embedding text is now `gist | app — window: ocr_text`.
- `src/config.rs` — `enable_captioner`, `caption_max_new_tokens`; model resolved from
  `resources_dir/models/fastvlm`.
- `build.sh` — fetches the FastVLM ONNX graphs + tokenizer, stages into `Resources/models/fastvlm`.
- `Cargo.toml` — `ort`/`tokenizers`/`image` promoted to direct deps (`ort = "=2.0.0-rc.9"` to
  reuse fastembed's working ort-sys; rc.12's download build is broken).

**Verified:** `cargo build` green; 16 tests pass incl. a gated end-to-end test
(`CAPTIONER_TEST_*`) running the real module → coherent gist (~7s incl. load).

**Findings / caveats:**
- **CPU EP can't run fp16 contrib ops.** `q4f16`/`fp16` decoder → `Missing Input` on
  `SkipSimplifiedLayerNormalization`. Use fp32-activation graphs: vision/embed `q4f16` run on
  CPU fine, but the **decoder must be `q4`** (fp32 KV cache). Set: vision_q4f16 + embed_q4f16 +
  decoder_q4 (~0.8 GB).
- **Latency ~6s/caption**, dominated by the 1024² vision encoder (~3.5s); TTFT ~1s, decode
  ~20 tok/s. Runs only on context switches on a background thread, but the vision encoder is the
  optimization target (smaller input → fewer image tokens, int8 vision, or CoreML EP).
- **Bundling ~0.8 GB in the DMG** bloats the installer; download-on-first-run is the better
  distribution strategy (noted in build.sh).
- **Captioning is synchronous** in `record_current_screen` (~6s per switch); debounce collapses
  bursts, but offloading captions async is a future improvement.

**Caption depth tuning (done).** Three changes made gists meaningfully deeper:
1. **Preprocess crop → pad** — center-crop discarded ~40% of a wide screen's width; letterbox
   ("pad" aspect ratio) preserves the whole screen.
2. **OCR-grounded prompt** — the OCR text (already captured) is fed into the prompt so the gist
   names concrete files/code/UI, not guesses.
3. **Detailed prompt + 80 tokens + sentence-trim** — "2-3 sentences, only describe what's
   visible"; `caption_max_new_tokens` 40 → 80; trailing incomplete sentence trimmed.
   Before: *"opening a new window titled patcha."* After: *"working on a project involving Swift
   and Rust, as indicated by the code snippets in the terminal."* Residual imprecision is the
   0.5B model; FastVLM-1.5B is the lever if needed. Cost: ~7s/caption vs ~4.5s (more decode).

**Remaining (manual):** live-daemon run to confirm gists are written on real app switches and
flow into retrieval; tune the vision-latency optimization before it's load-bearing.

---

## Model delivery — download-on-first-run (TODO)

Bundling the models in the DMG adds ~0.9 GB (FastVLM ~0.8 GB + MobileCLIP ~68 MB), almost all
captioner. Instead, **ship binaries only and fetch the models after signup**, behind an
onboarding loading screen. Replaces the current `build.sh` "stage models into Resources" step.

**Flow.**
1. User installs the lean DMG (helper binaries only, no model weights).
2. Onboarding: sign in → grant Screen Recording / Accessibility → **"Setting up on-device
   intelligence…"** loading step.
3. That step runs the model download; capture/daemon starts once it completes (or starts
   immediately with perception features dark — see graceful degradation below).

**Where models land.** `~/Library/Application Support/patcha/models/` — same root as the
fastembed text models (`embedding_cache_dir`). Layout: `models/fastvlm/...`,
`models/mobileclip_s2_image.mlpackage`. Resolution order in the collectors becomes
**Application Support `models/` first, then `resources_dir` fallback** (so a bundled-model dev
build still works). Update `FastVlmCaptioner` (currently `resources_dir/models/fastvlm`) and the
MobileCLIP path accordingly.

**Downloader.** A `patcha download-models [--force]` CLI subcommand in Rust using `reqwest`
(already a dep):
- Reads a shipped `models.manifest` (JSON): per-file `{ url, dest, size, sha256 }` for the
  FastVLM graphs + tokenizer/configs and the MobileCLIP `.mlpackage`, plus a `model_version`.
- Streams each file to a temp path with a progress callback, verifies size + sha256, atomic
  rename into place. Idempotent: skips files already present and valid (resume-friendly).
- Emits progress as stdout JSON lines — `{"file":"decoder_model_merged_q4.onnx","pct":42,
  "overall_pct":18}` — so the Swift onboarding UI can drive a progress bar.
- On failure: retry with backoff; surface a clear error + a "Retry" affordance in Settings.

**Onboarding UI (Swift app).** The loading step spawns `patcha download-models`, parses the
progress lines, renders a determinate progress bar with the current file + overall %. Total is
~0.9 GB, so show MB-downloaded and allow it to continue in the background ("You can start using
Patcha while models finish downloading").

**Graceful degradation (already in place).** The collector already no-ops cleanly when models
are absent: `enable_visual_prefilter` falls back to capture-every-tick, and the captioner
yields `gist=None`. So perception features simply switch on once the download lands — the app
is fully usable meanwhile. The loading screen is UX polish, not a hard gate.

**Versioning / upgrades.** `model_version` in the manifest; on app update, re-run
`download-models` to fetch changed files and prune superseded ones. Ties into the
`_meta.visual_embedder` / `_meta.captioner` tags already written on events.

**build.sh change.** Drop the "copy models into `Resources/`" steps; instead generate/ship
`models.manifest` next to the binary. Keep compiling + staging the helper binaries.

**Auth (optional).** Since this runs post-signup, the download can be gated/authenticated
against the account if we want to control distribution; default is anonymous HF fetch.
