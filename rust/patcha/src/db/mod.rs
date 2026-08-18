pub mod activity_graph;
pub mod entities;
pub mod graph;
pub mod migrations;
pub mod retrieval;
pub mod store;
pub mod tasks;

use anyhow::Result;
use rusqlite::Connection;
use std::path::Path;
use std::sync::{Arc, Mutex, MutexGuard, OnceLock};

static SQLITE_VEC_REGISTERED: OnceLock<()> = OnceLock::new();

/// Shared database handle. All subsystems hold an `Arc<Db>`.
pub struct Db {
    conn: Mutex<Connection>,
}

impl Db {
    pub fn open(path: &Path) -> Result<Arc<Self>> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)?;
        }

        // sqlite3_auto_extension must be called BEFORE Connection::open so that the
        // extension init function is invoked when the connection is established.
        // sqlite-vec and rusqlite define ffi types in separate -sys crates, so the
        // init fn pointer must be transmuted across the equivalent signatures.
        type ExtensionInit = unsafe extern "C" fn(
            *mut rusqlite::ffi::sqlite3,
            *mut *const std::os::raw::c_char,
            *const rusqlite::ffi::sqlite3_api_routines,
        ) -> std::os::raw::c_int;
        SQLITE_VEC_REGISTERED.get_or_init(|| unsafe {
            rusqlite::ffi::sqlite3_auto_extension(Some(
                std::mem::transmute::<usize, ExtensionInit>(sqlite_vec::sqlite3_vec_init as usize),
            ));
        });

        let conn = Connection::open(path)?;

        conn.execute_batch(
            "PRAGMA journal_mode=WAL;
             PRAGMA synchronous=NORMAL;
             PRAGMA foreign_keys=ON;
             PRAGMA cache_size=-32000;",
        )?;

        migrations::run(&conn)?;

        Ok(Arc::new(Self {
            conn: Mutex::new(conn),
        }))
    }

    /// Acquire the SQLite connection lock.
    pub fn conn(&self) -> MutexGuard<'_, Connection> {
        self.conn.lock().expect("db mutex poisoned")
    }
}

/// Serialize a f32 slice to little-endian bytes for sqlite-vec.
pub fn vec_to_bytes(v: &[f32]) -> Vec<u8> {
    v.iter().flat_map(|f| f.to_le_bytes()).collect()
}
