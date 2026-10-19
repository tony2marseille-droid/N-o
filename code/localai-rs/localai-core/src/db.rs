//! SQLite — meme schema que backend/core/database.py.
use rusqlite::Connection;
use std::path::Path;
use std::sync::{Arc, Mutex};

use crate::error::{AppError, AppResult};

const SCHEMA: &str = r#"
CREATE TABLE IF NOT EXISTS conversations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL DEFAULT 'Nouvelle conversation',
    model TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    role TEXT NOT NULL,
    content TEXT NOT NULL,
    model TEXT NOT NULL DEFAULT '',
    prompt_tokens INTEGER NOT NULL DEFAULT 0,
    completion_tokens INTEGER NOT NULL DEFAULT 0,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS app_settings (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS memories (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    content TEXT NOT NULL,
    tag TEXT NOT NULL DEFAULT 'user',
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS documents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    filename TEXT NOT NULL,
    size_bytes INTEGER NOT NULL DEFAULT 0,
    chunk_count INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS document_chunks (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    document_id INTEGER NOT NULL REFERENCES documents(id) ON DELETE CASCADE,
    position INTEGER NOT NULL DEFAULT 0,
    content TEXT NOT NULL,
    embedding BLOB
);
CREATE TABLE IF NOT EXISTS learned_knowledge (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    question TEXT NOT NULL,
    answer TEXT NOT NULL,
    q_embedding BLOB,
    hits INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS prompts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    content TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS tool_traces (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    seq INTEGER NOT NULL DEFAULT 0,
    tool_name TEXT NOT NULL,
    arguments TEXT NOT NULL DEFAULT '{}',
    result_preview TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'ok',
    reason TEXT NOT NULL DEFAULT ''
);
CREATE TABLE IF NOT EXISTS telegram_sessions (
    chat_id INTEGER PRIMARY KEY,
    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_conversation ON messages(conversation_id);
CREATE INDEX IF NOT EXISTS idx_chunks_document ON document_chunks(document_id);
CREATE INDEX IF NOT EXISTS idx_tool_traces_conversation ON tool_traces(conversation_id);
"#;

fn migrate(conn: &Connection) -> AppResult<()> {
    for (table, col, decl) in [
        ("messages", "quality_score", "INTEGER"),
        ("conversations", "summary", "TEXT NOT NULL DEFAULT ''"),
        ("tool_traces", "status", "NOT NULL DEFAULT 'ok'"),
        ("tool_traces", "reason", "NOT NULL DEFAULT ''"),
    ] {
        let mut stmt = conn.prepare(&format!("PRAGMA table_info({table})"))?;
        let existing: Vec<String> = stmt
            .query_map([], |r| r.get::<_, String>(1))?
            .filter_map(|r| r.ok())
            .collect();
        if !existing.iter().any(|c| c == col) {
            conn.execute(&format!("ALTER TABLE {table} ADD COLUMN {col} {decl}"), [])?;
        }
    }
    Ok(())
}

#[derive(Clone)]
pub struct Db {
    inner: Arc<Mutex<Connection>>,
}

impl Db {
    pub fn open(path: &Path) -> AppResult<Self> {
        if let Some(parent) = path.parent() {
            std::fs::create_dir_all(parent)
                .map_err(|e| AppError::Db(e.to_string()))?;
        }
        let conn = Connection::open(path)?;
        conn.execute_batch("PRAGMA foreign_keys = ON")?;
        // Concurrence inter-processus (Python :8320 + Rust :8321 sur la meme base).
        conn.execute_batch("PRAGMA journal_mode = WAL")?;
        conn.busy_timeout(std::time::Duration::from_secs(30))?;
        conn.execute_batch(SCHEMA)?;
        migrate(&conn)?;
        Ok(Self {
            inner: Arc::new(Mutex::new(conn)),
        })
    }

    fn lock(&self) -> AppResult<std::sync::MutexGuard<'_, Connection>> {
        self.inner.lock().map_err(|e| AppError::Db(e.to_string()))
    }

    pub fn execute(&self, sql: &str, p: &[&dyn rusqlite::ToSql]) -> AppResult<i64> {
        let conn = self.lock()?;
        conn.execute(sql, p)?;
        Ok(conn.last_insert_rowid())
    }

    pub fn query_all_json(&self, sql: &str, p: &[&dyn rusqlite::ToSql]) -> AppResult<Vec<serde_json::Value>> {
        let conn = self.lock()?;
        let mut stmt = conn.prepare(sql)?;
        let cols: Vec<String> = stmt.column_names().iter().map(|s| s.to_string()).collect();
        let rows = stmt.query_map(p, |row| {
            let mut map = serde_json::Map::new();
            for (i, name) in cols.iter().enumerate() {
                let v: rusqlite::types::Value = row.get(i)?;
                map.insert(name.clone(), sql_value_to_json(v));
            }
            Ok(serde_json::Value::Object(map))
        })?;
        let mut out = Vec::new();
        for r in rows {
            out.push(r?);
        }
        Ok(out)
    }

    pub fn query_one_json(&self, sql: &str, p: &[&dyn rusqlite::ToSql]) -> AppResult<Option<serde_json::Value>> {
        let mut rows = self.query_all_json(sql, p)?;
        Ok(rows.pop())
    }

    pub fn get_setting(&self, key: &str, default: &str) -> String {
        let r: AppResult<Option<serde_json::Value>> = self.query_one_json(
            "SELECT value FROM app_settings WHERE key=?",
            &[&key],
        );
        match r {
            Ok(Some(v)) => v.get("value").and_then(|x| x.as_str()).unwrap_or(default).to_string(),
            _ => default.to_string(),
        }
    }

    pub fn set_setting(&self, key: &str, value: &str) -> AppResult<()> {
        self.execute(
            "INSERT INTO app_settings(key,value) VALUES(?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
            &[&key, &value],
        )?;
        Ok(())
    }
}

fn sql_value_to_json(v: rusqlite::types::Value) -> serde_json::Value {
    use rusqlite::types::Value::*;
    match v {
        Null => serde_json::Value::Null,
        Integer(i) => serde_json::Value::from(i),
        Real(f) => serde_json::Number::from_f64(f)
            .map(serde_json::Value::Number)
            .unwrap_or(serde_json::Value::Null),
        Text(s) => serde_json::Value::from(s),
        Blob(b) => serde_json::Value::from(format!("<blob {}o>", b.len())),
    }
}

pub fn now_iso() -> String {
    chrono::Local::now().format("%Y-%m-%dT%H:%M:%S").to_string()
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn open_avec_schema_et_migrate() {
        let tmp = std::env::temp_dir()
            .join(format!("localai_test_db_{}.sqlite", std::process::id()));
        let _ = std::fs::remove_file(&tmp);
        let db = Db::open(&tmp).expect("open db temporaire");

        db.set_setting("cle_test", "valeur_42").expect("set_setting");
        assert_eq!(db.get_setting("cle_test", "fallback"), "valeur_42");
        assert_eq!(db.get_setting("absente", "defaut"), "defaut");

        // FK tool_traces -> conversations : on cree la conversation d'abord.
        let now0 = now_iso();
        let cid = db
            .execute(
                "INSERT INTO conversations (title, model, created_at, updated_at) VALUES ('test', '', ?, ?)",
                &[&now0, &now0],
            )
            .expect("insert conversation");
        let now = now_iso();
        db.execute(
            "INSERT INTO tool_traces (conversation_id, seq, tool_name, arguments, result_preview, created_at, status, reason) VALUES (?, 0, 'outil_test', '{}', 'prev', ?, 'denied', 'refus_test')",
            &[&cid, &now],
        )
        .expect("insert tool_traces");
        let rows = db
            .query_all_json(
                "SELECT status, reason FROM tool_traces WHERE tool_name='outil_test'",
                &[],
            )
            .expect("select tool_traces");
        assert_eq!(rows.len(), 1);
        assert_eq!(rows[0]["status"].as_str(), Some("denied"));
        assert_eq!(rows[0]["reason"].as_str(), Some("refus_test"));

        drop(db);
        let _ = std::fs::remove_file(&tmp);
        let _ = std::fs::remove_file(tmp.with_extension("sqlite-wal"));
        let _ = std::fs::remove_file(tmp.with_extension("sqlite-shm"));
    }

    #[test]
    fn now_iso_format() {
        let s = now_iso();
        assert!(s.contains("-"), "formats : {s}");
        assert!(s.len() >= 19);
    }
}
