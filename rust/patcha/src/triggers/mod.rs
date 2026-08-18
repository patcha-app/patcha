//! Event-driven capture triggers (Phase 2).
//!
//! Replaces the fixed poll with instant triggers from a long-lived Swift
//! `observer` process plus an idle detector, funneled through a coordinator that
//! debounces and routes each trigger to a capture depth. See `perception.md`
//! "Trigger system".

pub mod coordinator;
mod idle;
mod observer;

pub use idle::{seconds_since_last_input, IdleDetector, IdleTransition};
pub use observer::ObserverHandle;

use chrono::{DateTime, Utc};

/// What kind of context change produced a trigger.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum TriggerKind {
    AppSwitch,
    TitleChange,
    SpaceSwitch,
    ScreenLock,
    ScreenUnlock,
    IdleResume,
    PollTick,
}

impl TriggerKind {
    pub fn parse(s: &str) -> Option<Self> {
        Some(match s {
            "app_switch" => Self::AppSwitch,
            "title_change" => Self::TitleChange,
            "space_switch" => Self::SpaceSwitch,
            "screen_lock" => Self::ScreenLock,
            "screen_unlock" => Self::ScreenUnlock,
            "idle_resume" => Self::IdleResume,
            "poll_tick" => Self::PollTick,
            _ => return None,
        })
    }

    /// How much of the capture pipeline this trigger warrants.
    pub fn capture_depth(self) -> CaptureDepth {
        match self {
            // Major context boundaries — always full capture.
            Self::AppSwitch | Self::SpaceSwitch | Self::IdleResume | Self::ScreenUnlock => {
                CaptureDepth::Full
            }
            // Might be minor (title refresh) or major (new tab) — let the cache decide.
            Self::TitleChange => CaptureDepth::Medium,
            // Within-window drift detection only.
            Self::PollTick => CaptureDepth::Light,
            // Session end — capture nothing.
            Self::ScreenLock => CaptureDepth::None,
        }
    }
}

/// Capture depth a trigger routes to. The accessibility cascade already chooses
/// drop-vs-store via the per-app cache; this governs whether we capture at all
/// and (in Phase 3) whether the VLM captioner runs.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum CaptureDepth {
    None,
    Light,
    Medium,
    Full,
}

#[derive(Debug, Clone)]
pub struct Trigger {
    pub kind: TriggerKind,
    pub app_name: String,
    pub window_title: String,
    pub timestamp: DateTime<Utc>,
}

impl Trigger {
    pub fn now(kind: TriggerKind) -> Self {
        Self {
            kind,
            app_name: String::new(),
            window_title: String::new(),
            timestamp: Utc::now(),
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn depth_routing_matches_spec() {
        use CaptureDepth::*;
        use TriggerKind::*;
        assert_eq!(AppSwitch.capture_depth(), Full);
        assert_eq!(SpaceSwitch.capture_depth(), Full);
        assert_eq!(IdleResume.capture_depth(), Full);
        assert_eq!(ScreenUnlock.capture_depth(), Full);
        assert_eq!(TitleChange.capture_depth(), Medium);
        assert_eq!(PollTick.capture_depth(), Light);
        assert_eq!(ScreenLock.capture_depth(), None);
    }

    #[test]
    fn parse_roundtrips_known_kinds() {
        assert_eq!(
            TriggerKind::parse("app_switch"),
            Some(TriggerKind::AppSwitch)
        );
        assert_eq!(
            TriggerKind::parse("idle_resume"),
            Some(TriggerKind::IdleResume)
        );
        assert_eq!(TriggerKind::parse("bogus"), None);
    }
}
