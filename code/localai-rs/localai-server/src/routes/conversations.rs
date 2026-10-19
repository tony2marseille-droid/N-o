use std::sync::Arc;

use axum::{
    extract::{Path, State},
    routing::{get, patch},
    Json, Router,
};
use localai_core::db::now_iso;
use crate::error::{ApiError, ApiResult};
use localai_core::AppError;
use serde::{Deserialize, Serialize};
use serde_json::{json, Value};

use crate::AppState;

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/api/conversations", get(list).post(create))
        .route("/api/conversations/:id/messages", get(messages))
        .route("/api/conversations/:id", patch(rename).delete(remove))
}

#[derive(Debug, Deserialize)]
pub struct CreateBody {
    pub title: Option<String>,
    pub model: Option<String>,
}

async fn list(State(st): State<Arc<AppState>>) -> ApiResult<Json<Value>> {
    let rows = st.db.query_all_json(
        "SELECT c.*, (SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) AS message_count FROM conversations c ORDER BY c.updated_at DESC",
        &[],
    )?;
    Ok(Json(json!({ "conversations": rows })))
}

async fn create(
    State(st): State<Arc<AppState>>,
    Json(b): Json<CreateBody>,
) -> ApiResult<Json<Value>> {
    let now = now_iso();
    let title = b.title.as_deref().unwrap_or("Nouvelle conversation");
    let model = b.model.as_deref().unwrap_or("");
    let id = st.db.execute(
        "INSERT INTO conversations (title, model, created_at, updated_at) VALUES (?,?,?,?)",
        &[&title, &model, &now.as_str(), &now.as_str()],
    )?;
    Ok(Json(json!({ "id": id })))
}

async fn messages(
    State(st): State<Arc<AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    let conv = st.db.query_one_json("SELECT * FROM conversations WHERE id=?", &[&id])?;
    let conv = conv.ok_or_else(|| ApiError(AppError::NotFound("Conversation introuvable".into())))?;
    let msgs = st.db.query_all_json(
        "SELECT * FROM messages WHERE conversation_id=? ORDER BY id",
        &[&id],
    )?;
    Ok(Json(json!({ "conversation": conv, "messages": msgs })))
}

#[derive(Debug, Deserialize, Serialize)]
pub struct TitleBody {
    pub title: String,
}

async fn rename(
    State(st): State<Arc<AppState>>,
    Path(id): Path<i64>,
    Json(b): Json<TitleBody>,
) -> ApiResult<Json<Value>> {
    let t = b.title.trim();
    let t: String = t.chars().take(120).collect();
    st.db.execute("UPDATE conversations SET title=? WHERE id=?", &[&t.as_str(), &id])?;
    Ok(Json(json!({ "ok": true })))
}

async fn remove(
    State(st): State<Arc<AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    st.db.execute("DELETE FROM messages WHERE conversation_id=?", &[&id])?;
    st.db.execute("DELETE FROM conversations WHERE id=?", &[&id])?;
    Ok(Json(json!({ "deleted": id })))
}
