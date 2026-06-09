use std::{
    collections::HashMap,
    path::PathBuf,
    sync::{Arc, Mutex},
    time::Duration,
};

use anyhow::Result;
use chrono::{DateTime, Utc};
use tokio::{
    signal::unix::{SignalKind, signal},
    time::interval,
};

use crate::{
    collectors::{
        accessibility::AccessibilityCollector,
        browser::BrowserCollector,
        git::GitCollector,
        guard::CollectorGuard,
        terminal::TerminalCollector,
        window::WindowCollector,
    },
    compaction::DailyCompactor,
    config::Config,
    db::{Db, activity_graph::ActivityGraph, store::VectorStore, tasks::TaskStore},
    embedding::Embedder,
    llm::client::PatchaApiClient,
    models::Event,
    process::EventPreprocessor,
};

// ---------------------------------------------------------------------------
// AppleScript for window title detection (same as Python patcha/macos/window_title.applescript)
// ---------------------------------------------------------------------------

const WINDOW_SCRIPT: &str = r#"tell application "System Events"
    set frontApp to first application process whose frontmost is true
    set appName to name of frontApp
    try
        set windowTitle to name of front window of frontApp
    on error
        set windowTitle to ""
    end try
    return appName & "|||" & windowTitle
end tell"#;

// ---------------------------------------------------------------------------
// PID file helpers
// ---------------------------------------------------------------------------

#[derive(serde::Serialize, serde::Deserialize)]
struct PidInfo {
    pid: u32,
    start_time: String,
    last_collection_time: String,
}

fn pid_file(cfg: &Config) -> PathBuf {
    cfg.data_dir.join("daemon.pid")
}

fn write_pid(cfg: &Config, last: &DateTime<Utc>) -> Result<()> {
    let info = PidInfo {
        pid: std::process::id(),
        start_time: Utc::now().to_rfc3339(),
        last_collection_time: last.to_rfc3339(),
    };
    std::fs::create_dir_all(&cfg.data_dir)?;
    let path = pid_file(cfg);
    std::fs::write(&path, serde_json::to_string(&info)?)?;
    Ok(())
}

fn update_pid(cfg: &Config, last: &DateTime<Utc>) {
    let path = pid_file(cfg);
    if let Ok(data) = std::fs::read_to_string(&path) {
        if let Ok(mut info) = serde_json::from_str::<PidInfo>(&data) {
            info.last_collection_time = last.to_rfc3339();
            let _ = std::fs::write(&path, serde_json::to_string(&info).unwrap_or_default());
        }
    }
}

fn remove_pid(cfg: &Config) {
    let _ = std::fs::remove_file(pid_file(cfg));
}

fn read_pid(cfg: &Config) -> Option<PidInfo> {
    let data = std::fs::read_to_string(pid_file(cfg)).ok()?;
    serde_json::from_str(&data).ok()
}

fn process_alive(pid: u32) -> bool {
    std::process::Command::new("kill")
        .args(["-0", &pid.to_string()])
        .stdout(std::process::Stdio::null())
        .stderr(std::process::Stdio::null())
        .status()
        .map(|s| s.success())
        .unwrap_or(false)
}

// ---------------------------------------------------------------------------
// Resources dir (where ax_content and ocr binaries live)
// ---------------------------------------------------------------------------

fn resources_dir() -> PathBuf {
    if let Ok(dir) = std::env::var("PATCHA_RESOURCES") {
        return PathBuf::from(dir);
    }
    if let Ok(exe) = std::env::current_exe() {
        if let Some(parent) = exe.parent() {
            if parent.join("ax_content").exists() {
                return parent.to_path_buf();
            }
        }
    }
    PathBuf::from("data")
}

// ---------------------------------------------------------------------------
// start
// ---------------------------------------------------------------------------

pub async fn start(cfg: Config) -> Result<()> {
    std::fs::create_dir_all(&cfg.data_dir)?;

    let script_path = cfg.data_dir.join("window_title.applescript");
    if !script_path.exists() {
        std::fs::write(&script_path, WINDOW_SCRIPT)?;
    }

    let res_dir = resources_dir();
    let now = Utc::now();
    write_pid(&cfg, &now)?;

    tracing::info!(
        pid = std::process::id(),
        poll_interval = cfg.poll_interval_seconds,
        ax_interval = cfg.ax_poll_interval_seconds,
        "patcha daemon starting"
    );

    // -----------------------------------------------------------------------
    // Subsystems
    // -----------------------------------------------------------------------
    let db = Db::open(&cfg.db_path)?;
    let store = Arc::new(VectorStore::new(db.clone()));
    let task_store = Arc::new(TaskStore::new(db.clone(), cfg.data_dir.clone()));
    let graph: Option<Arc<ActivityGraph>> = if cfg.enable_activity_graph {
        Some(Arc::new(ActivityGraph::new(db.clone(), cfg.session_gap_seconds)))
    } else {
        None
    };
    let embedder = Arc::new(Embedder::new(&cfg)?);
    let preprocessor = EventPreprocessor::new(&cfg, Arc::clone(&embedder));
    let llm_client = Arc::new(PatchaApiClient::new(&cfg));
    let compactor = DailyCompactor::new(
        Arc::clone(&store),
        Arc::clone(&task_store),
        Arc::clone(&llm_client),
        &cfg,
    );

    // -----------------------------------------------------------------------
    // Collectors
    // -----------------------------------------------------------------------
    let git = if cfg.enable_git_collector {
        Some(Mutex::new(GitCollector::new(cfg.data_dir.clone())))
    } else {
        None
    };
    let browser = if cfg.enable_browser_collector {
        Some(Mutex::new(BrowserCollector::new()))
    } else {
        None
    };
    let terminal = if cfg.enable_terminal_collector {
        Some(Mutex::new(TerminalCollector::new()))
    } else {
        None
    };
    let window = if cfg.enable_window_collector {
        Some(Mutex::new(WindowCollector::new(&cfg.data_dir, script_path)))
    } else {
        None
    };
    let ax: Option<Arc<Mutex<AccessibilityCollector>>> = if cfg.enable_accessibility_collector {
        Some(Arc::new(Mutex::new(AccessibilityCollector::new(
            &cfg.data_dir,
            &res_dir,
            &cfg,
        ))))
    } else {
        None
    };

    let mut guards: HashMap<&'static str, CollectorGuard> = HashMap::from([
        ("git", CollectorGuard::new("git", 3)),
        ("browser", CollectorGuard::new("browser", 3)),
        ("terminal", CollectorGuard::new("terminal", 3)),
        ("window", CollectorGuard::new("window", 3)),
        ("ax", CollectorGuard::new("accessibility", 3)),
    ]);

    // -----------------------------------------------------------------------
    // Accessibility recording: event-driven coordinator (Phase 2), or a fixed
    // poll fallback when triggers are disabled or the observer binary is absent.
    // -----------------------------------------------------------------------
    if let Some(ax_arc) = ax.as_ref().cloned() {
        let observer_bin = res_dir.join("observer");
        let use_triggers = cfg.enable_event_triggers
            && crate::triggers::ObserverHandle::available(&observer_bin);

        if use_triggers {
            let coord_cfg = crate::triggers::coordinator::CoordinatorConfig {
                observer_binary: observer_bin,
                debounce: Duration::from_millis(cfg.trigger_debounce_ms),
                background_poll: Duration::from_secs(cfg.background_poll_interval_seconds),
                idle_timeout_secs: cfg.idle_timeout_seconds,
            };
            tracing::info!("accessibility: event-driven triggers enabled");
            tokio::spawn(crate::triggers::coordinator::run(coord_cfg, ax_arc));
        } else {
            if cfg.enable_event_triggers {
                tracing::warn!(
                    "event triggers enabled but observer binary not found at {:?}; \
                     falling back to fixed {}s poll",
                    observer_bin,
                    cfg.ax_poll_interval_seconds
                );
            }
            let ax_interval = cfg.ax_poll_interval_seconds;
            tokio::spawn(async move {
                let mut tick = interval(Duration::from_secs(ax_interval));
                tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
                loop {
                    tick.tick().await;
                    let arc = ax_arc.clone();
                    tokio::task::spawn_blocking(move || {
                        if let Ok(mut col) = arc.lock() {
                            if let Err(e) = col.record_current_screen() {
                                tracing::debug!(error=%e, "ax recording skipped");
                            }
                        }
                    })
                    .await
                    .ok();
                }
            });
        }
    }

    // -----------------------------------------------------------------------
    // Signal handling + main poll loop
    // -----------------------------------------------------------------------
    let mut sigterm = signal(SignalKind::terminate())?;
    let mut poll_tick = interval(Duration::from_secs(cfg.poll_interval_seconds));
    poll_tick.set_missed_tick_behavior(tokio::time::MissedTickBehavior::Skip);
    let mut last_collection = Utc::now();

    loop {
        tokio::select! {
            biased;
            _ = tokio::signal::ctrl_c() => { tracing::info!("SIGINT — shutting down"); break; }
            _ = sigterm.recv() => { tracing::info!("SIGTERM — shutting down"); break; }
            _ = poll_tick.tick() => {}
        }

        let since = last_collection;
        last_collection = Utc::now();
        tracing::info!(since = %since.format("%Y-%m-%d %H:%M:%S UTC"), "collection cycle");

        // -----------------------------------------------------------------------
        // Collect (sync, blocking)
        // -----------------------------------------------------------------------
        let all_events: Vec<Event> = tokio::task::block_in_place(|| {
            let mut events: Vec<Event> = Vec::new();

            // Window snapshot
            if let (Some(win), Some(guard)) = (&window, guards.get("window")) {
                if guard.ok() {
                    if let Ok(mut w) = win.lock() {
                        let _ = w.record_current_window();
                    }
                }
            }

            // Git staging snapshot
            if let (Some(g), Some(guard)) = (&git, guards.get("git")) {
                if guard.ok() {
                    if let Ok(mut gc) = g.lock() {
                        gc.record_staging_snapshot();
                    }
                }
            }

            // Git commits + staged events
            collect_source("git", &git, &mut guards, |g: &mut GitCollector| {
                let mut ev = g.collect_commits(Some(since));
                ev.extend(g.collect_staging_events(since));
                ev
            }, &mut events);

            // Browser
            collect_source("browser", &browser, &mut guards, |b: &mut BrowserCollector| {
                b.collect_all(Some(since))
            }, &mut events);

            // Terminal
            collect_source("terminal", &terminal, &mut guards, |t: &mut TerminalCollector| {
                t.collect_all(Some(since))
            }, &mut events);

            // Window sessions
            collect_source("window", &window, &mut guards, |w: &mut WindowCollector| {
                w.collect_windows(since)
            }, &mut events);

            // Accessibility (collect already-recorded entries)
            if let Some(ax_arc) = &ax {
                let guard = guards.get_mut("ax").unwrap();
                if guard.ok() {
                    match ax_arc.lock() {
                        Ok(ac) => {
                            let ev = ac.collect_screen_text(since);
                            tracing::debug!(count = ev.len(), "ax collected");
                            guard.success();
                            events.extend(ev);
                        }
                        Err(e) => guard.fail(&anyhow::anyhow!("{e}")),
                    }
                }
            }

            events
        });

        if all_events.is_empty() {
            tracing::debug!("no new events");
            update_pid(&cfg, &last_collection);
            continue;
        }

        tracing::info!(count = all_events.len(), "processing events");

        // -----------------------------------------------------------------------
        // Embed + process
        // -----------------------------------------------------------------------
        let pending = tokio::task::block_in_place(|| preprocessor.process_pending())
            .unwrap_or_default();
        let processed = tokio::task::block_in_place(|| preprocessor.process_events(all_events));

        // -----------------------------------------------------------------------
        // Store
        // -----------------------------------------------------------------------
        if !pending.is_empty() {
            if let Err(e) = store.store_events(&pending) {
                tracing::error!(error=%e, "failed to store pending events");
            }
        }
        if !processed.is_empty() {
            if let Err(e) = store.store_events(&processed) {
                tracing::error!(error=%e, "failed to store new events");
            }
        }
        tracing::info!(pending = pending.len(), new = processed.len(), "stored");

        // -----------------------------------------------------------------------
        // Activity graph
        // -----------------------------------------------------------------------
        if let Some(g) = &graph {
            let mut all_stored: Vec<Event> = pending.clone();
            all_stored.extend(processed.iter().cloned());
            let mut logical: Vec<&Event> = all_stored
                .iter()
                .filter(|e| {
                    e.metadata
                        .get("chunk_index")
                        .and_then(|v| v.as_u64())
                        .unwrap_or(0)
                        == 0
                })
                .collect();
            logical.sort_by_key(|e| e.timestamp);
            for event in logical {
                if let Err(e) = g.upsert_event(event) {
                    tracing::debug!(error=%e, "graph upsert skipped");
                }
            }
        }

        // -----------------------------------------------------------------------
        // Daily compaction
        // -----------------------------------------------------------------------
        if let Err(e) = compactor.maybe_compact_previous_days().await {
            tracing::error!(error=%e, "compaction error");
        }

        update_pid(&cfg, &last_collection);
    }

    remove_pid(&cfg);
    tracing::info!("daemon stopped");
    Ok(())
}

// Helper to reduce boilerplate for sync collectors
fn collect_source<C, F>(
    name: &'static str,
    col: &Option<Mutex<C>>,
    guards: &mut HashMap<&'static str, CollectorGuard>,
    mut f: F,
    events: &mut Vec<Event>,
) where
    F: FnMut(&mut C) -> Vec<Event>,
{
    let Some(col) = col else { return };
    let guard = guards.get_mut(name).unwrap();
    if !guard.ok() {
        return;
    }
    match col.lock() {
        Ok(mut c) => {
            let ev = f(&mut c);
            tracing::debug!(count = ev.len(), collector = name, "collected");
            guard.success();
            events.extend(ev);
        }
        Err(e) => guard.fail(&anyhow::anyhow!("{e}")),
    }
}

// ---------------------------------------------------------------------------
// stop
// ---------------------------------------------------------------------------

pub async fn stop(cfg: Config) -> Result<()> {
    let Some(info) = read_pid(&cfg) else {
        println!("Daemon is not running (no PID file).");
        return Ok(());
    };
    if !process_alive(info.pid) {
        println!("Daemon is not running (stale PID file, removing).");
        remove_pid(&cfg);
        return Ok(());
    }
    println!("Stopping daemon (PID {})...", info.pid);
    std::process::Command::new("kill")
        .args(["-TERM", &info.pid.to_string()])
        .status()
        .ok();

    for _ in 0..10 {
        tokio::time::sleep(Duration::from_millis(500)).await;
        if !process_alive(info.pid) {
            println!("Daemon stopped.");
            return Ok(());
        }
    }
    println!("Did not stop within 5s — sending SIGKILL.");
    std::process::Command::new("kill")
        .args(["-KILL", &info.pid.to_string()])
        .status()
        .ok();
    remove_pid(&cfg);
    Ok(())
}

// ---------------------------------------------------------------------------
// status
// ---------------------------------------------------------------------------

pub async fn status(cfg: Config) -> Result<()> {
    match read_pid(&cfg) {
        None => println!("Daemon: stopped"),
        Some(info) => {
            if process_alive(info.pid) {
                println!("Daemon: running");
                println!("  PID:             {}", info.pid);
                println!("  Started:         {}", info.start_time);
                println!("  Last collection: {}", info.last_collection_time);
            } else {
                println!("Daemon: stopped (stale PID file removed)");
                remove_pid(&cfg);
            }
        }
    }
    Ok(())
}
