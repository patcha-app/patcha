//! Idle/active tracking via the system input-idle clock.
//!
//! Uses `CGEventSourceSecondsSinceLastEventType`, which reports only *how long*
//! since the last input — never what was typed. No event tap, no Accessibility
//! permission, no keystroke logging.

#[link(name = "CoreGraphics", kind = "framework")]
extern "C" {
    fn CGEventSourceSecondsSinceLastEventType(state: i32, event_type: u32) -> f64;
}

// kCGEventSourceStateCombinedSessionState
const COMBINED_SESSION_STATE: i32 = 0;
// kCGAnyInputEventType
const ANY_INPUT_EVENT: u32 = 0xFFFF_FFFF;

/// Seconds since the last keyboard/mouse input across the session.
pub fn seconds_since_last_input() -> f64 {
    unsafe { CGEventSourceSecondsSinceLastEventType(COMBINED_SESSION_STATE, ANY_INPUT_EVENT) }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum IdleTransition {
    /// Crossed from active into idle (pause the background poll).
    WentIdle,
    /// First input after being idle (fire an idle_resume capture).
    Resumed,
}

/// Edge-detector over the system idle clock. Poll it on a cheap cadence (~1s).
pub struct IdleDetector {
    timeout_secs: f64,
    idle: bool,
}

impl IdleDetector {
    pub fn new(timeout_secs: u64) -> Self {
        Self {
            timeout_secs: timeout_secs as f64,
            idle: false,
        }
    }

    pub fn is_idle(&self) -> bool {
        self.idle
    }

    /// Sample the idle clock; returns an edge only when active<->idle flips.
    pub fn poll(&mut self) -> Option<IdleTransition> {
        self.evaluate(seconds_since_last_input())
    }

    fn evaluate(&mut self, idle_secs: f64) -> Option<IdleTransition> {
        let now_idle = idle_secs >= self.timeout_secs;
        match (self.idle, now_idle) {
            (false, true) => {
                self.idle = true;
                Some(IdleTransition::WentIdle)
            }
            (true, false) => {
                self.idle = false;
                Some(IdleTransition::Resumed)
            }
            _ => None,
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn edges_fire_once_per_transition() {
        let mut d = IdleDetector::new(120);
        assert_eq!(d.evaluate(10.0), None); // active, stays active
        assert_eq!(d.evaluate(130.0), Some(IdleTransition::WentIdle));
        assert_eq!(d.evaluate(200.0), None); // still idle, no repeat
        assert_eq!(d.evaluate(0.5), Some(IdleTransition::Resumed));
        assert_eq!(d.evaluate(1.0), None); // active again, no repeat
        assert!(!d.is_idle());
    }
}
