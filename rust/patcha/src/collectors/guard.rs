/// Disables a collector after `max_failures` consecutive errors.
pub struct CollectorGuard {
    pub name: String,
    max_failures: u32,
    failures: u32,
    disabled: bool,
}

impl CollectorGuard {
    pub fn new(name: impl Into<String>, max_failures: u32) -> Self {
        Self {
            name: name.into(),
            max_failures,
            failures: 0,
            disabled: false,
        }
    }

    pub fn ok(&self) -> bool {
        !self.disabled
    }

    pub fn success(&mut self) {
        self.failures = 0;
    }

    pub fn fail(&mut self, err: &anyhow::Error) {
        if self.disabled {
            return;
        }
        self.failures += 1;
        if self.failures >= self.max_failures {
            self.disabled = true;
            tracing::warn!(
                collector = %self.name,
                failures = self.failures,
                error = %err,
                "collector disabled after consecutive failures"
            );
        } else {
            tracing::warn!(
                collector = %self.name,
                failures = self.failures,
                error = %err,
                "collector error"
            );
        }
    }
}
