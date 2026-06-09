//! CaptureCoordinator — the single funnel between trigger sources and capture.
//!
//! Sits between the `observer` process + idle detector and the accessibility
//! collector. Debounces bursty triggers (alt-tabbing), gates captures on idle
//! and screen-lock state, and injects a relaxed background poll for within-window
//! drift. The drop-vs-store decision still lives in the collector's per-app cache;
//! this decides *whether and when* to capture at all.

use crate::collectors::accessibility::AccessibilityCollector;
use crate::triggers::{
    CaptureDepth, IdleDetector, IdleTransition, ObserverHandle, Trigger, TriggerKind,
};
use std::path::PathBuf;
use std::sync::{Arc, Mutex};
use std::time::{Duration, Instant};
use tokio::sync::mpsc;
use tokio::time::interval;

pub struct CoordinatorConfig {
    pub observer_binary: PathBuf,
    pub debounce: Duration,
    pub background_poll: Duration,
    pub idle_timeout_secs: u64,
}

/// Run the coordinator until the trigger channel closes (observer died). The
/// caller can then fall back to plain polling.
pub async fn run(cfg: CoordinatorConfig, ax: Arc<Mutex<AccessibilityCollector>>) {
    let (tx, mut rx) = mpsc::unbounded_channel::<Trigger>();
    let _observer = match ObserverHandle::spawn(cfg.observer_binary.clone(), tx) {
        Ok(h) => h,
        Err(e) => {
            tracing::error!(error = %e, "observer spawn failed; event triggers disabled");
            return;
        }
    };

    let mut idle = IdleDetector::new(cfg.idle_timeout_secs);
    let mut paused = false; // screen locked
    let mut pending: Option<Trigger> = None;
    let mut pending_at: Option<Instant> = None;
    let mut last_capture = Instant::now();
    // Coarse heartbeat servicing debounce, idle edges, and the background poll.
    let mut tick = interval(Duration::from_millis(100));

    tracing::info!("event-driven capture coordinator started");

    loop {
        tokio::select! {
            maybe = rx.recv() => {
                let Some(trigger) = maybe else {
                    tracing::warn!("trigger channel closed; coordinator exiting");
                    break;
                };
                match trigger.kind {
                    TriggerKind::ScreenLock => {
                        paused = true;
                        pending = None;
                        pending_at = None;
                        tracing::debug!("screen locked; capture paused");
                    }
                    TriggerKind::ScreenUnlock => {
                        paused = false;
                        capture(&ax, trigger).await;
                        last_capture = Instant::now();
                    }
                    // Every other trigger is a context change -> debounce it.
                    _ if !paused => {
                        pending = Some(trigger);
                        pending_at = Some(Instant::now());
                    }
                    _ => {}
                }
            }
            _ = tick.tick() => {
                // Debounce: fire the latest trigger once the burst settles.
                if let Some(at) = pending_at {
                    if at.elapsed() >= cfg.debounce {
                        pending_at = None;
                        if let Some(t) = pending.take() {
                            if !paused {
                                capture(&ax, t).await;
                                last_capture = Instant::now();
                            }
                        }
                    }
                }

                // Idle edges: pause the poll on idle, capture on resume.
                if let Some(edge) = idle.poll() {
                    match edge {
                        IdleTransition::WentIdle => {
                            tracing::debug!("user idle; background poll paused");
                        }
                        IdleTransition::Resumed => {
                            if !paused {
                                capture(&ax, Trigger::now(TriggerKind::IdleResume)).await;
                                last_capture = Instant::now();
                            }
                        }
                    }
                }

                // Relaxed background poll (within-window drift) while active.
                if !paused
                    && !idle.is_idle()
                    && last_capture.elapsed() >= cfg.background_poll
                {
                    capture(&ax, Trigger::now(TriggerKind::PollTick)).await;
                    last_capture = Instant::now();
                }
            }
        }
    }
}

async fn capture(ax: &Arc<Mutex<AccessibilityCollector>>, trigger: Trigger) {
    if trigger.kind.capture_depth() == CaptureDepth::None {
        return;
    }
    tracing::debug!(kind = ?trigger.kind, depth = ?trigger.kind.capture_depth(), "capture");
    let arc = ax.clone();
    // record_current_screen is blocking (screencapture + OCR + embed).
    let _ = tokio::task::spawn_blocking(move || {
        if let Ok(mut c) = arc.lock() {
            if let Err(e) = c.record_current_screen() {
                tracing::debug!(error = %e, "capture failed");
            }
        }
    })
    .await;
}
