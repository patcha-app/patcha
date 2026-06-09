use chrono::{DateTime, Utc};
use std::collections::HashMap;

struct CacheEntry {
    embedding: Vec<f32>,
    timestamp: DateTime<Utc>,
}

/// Per-app store of the last-stored visual embedding, used to short-circuit
/// captures when an app hasn't changed visually. Entries older than the TTL are
/// treated as unseen (apps change while backgrounded). Cleared on restart.
pub struct AppEmbeddingCache {
    entries: HashMap<String, CacheEntry>,
    ttl_seconds: i64,
}

impl AppEmbeddingCache {
    pub fn new(ttl_seconds: u64) -> Self {
        Self {
            entries: HashMap::new(),
            ttl_seconds: ttl_seconds as i64,
        }
    }

    /// Cosine similarity of `vec` against the cached embedding for `app`,
    /// or `None` if there is no fresh entry (missing or past TTL).
    pub fn fresh_cosine(&self, app: &str, vec: &[f32], now: DateTime<Utc>) -> Option<f32> {
        let entry = self.entries.get(app)?;
        if (now - entry.timestamp).num_seconds() > self.ttl_seconds {
            return None;
        }
        Some(cosine(&entry.embedding, vec))
    }

    /// Record the embedding stored for `app`. Called only when an event is
    /// actually written (dropped frames must not refresh the cache, so gradual
    /// drift is still eventually caught).
    pub fn update(&mut self, app: &str, vec: Vec<f32>, now: DateTime<Utc>) {
        self.entries.insert(
            app.to_string(),
            CacheEntry {
                embedding: vec,
                timestamp: now,
            },
        );
    }
}

/// Cosine similarity of two equal-length vectors. Returns 0.0 if either is zero.
pub fn cosine(a: &[f32], b: &[f32]) -> f32 {
    let n = a.len().min(b.len());
    let mut dot = 0f32;
    let mut na = 0f32;
    let mut nb = 0f32;
    for i in 0..n {
        dot += a[i] * b[i];
        na += a[i] * a[i];
        nb += b[i] * b[i];
    }
    if na == 0.0 || nb == 0.0 {
        return 0.0;
    }
    dot / (na.sqrt() * nb.sqrt())
}

#[cfg(test)]
mod tests {
    use super::*;
    use chrono::Duration;

    #[test]
    fn cosine_identity_and_orthogonal() {
        let a = vec![1.0, 2.0, 3.0];
        assert!((cosine(&a, &a) - 1.0).abs() < 1e-6);
        assert!(cosine(&[1.0, 0.0], &[0.0, 1.0]).abs() < 1e-6);
    }

    #[test]
    fn fresh_cosine_respects_presence_and_ttl() {
        let now = Utc::now();
        let mut c = AppEmbeddingCache::new(7200);
        assert!(c.fresh_cosine("VSCode", &[1.0, 0.0], now).is_none());

        c.update("VSCode", vec![1.0, 0.0], now);
        let cos = c.fresh_cosine("VSCode", &[1.0, 0.0], now).unwrap();
        assert!((cos - 1.0).abs() < 1e-6);

        // Past the TTL the entry is treated as unseen.
        let later = now + Duration::seconds(7201);
        assert!(c.fresh_cosine("VSCode", &[1.0, 0.0], later).is_none());
    }
}
