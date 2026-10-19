use std::sync::Arc;

use axum::{
    extract::{Path, State},
    routing::{delete, get},
    Json, Router,
};
use localai_core::db::now_iso;
use localai_core::AppError;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/memory", get(list).post(add))
        .route("/api/memory/:id", delete(remove))
}

#[derive(Debug, Deserialize)]
pub struct CreateBody {
    pub content: String,
    pub tag: Option<String>,
}

async fn list(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let rows = st
        .db
        .query_all_json("SELECT * FROM memories ORDER BY id DESC", &[])?;
    Ok(Json(json!({ "memories": rows })))
}

async fn add(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<CreateBody>,
) -> ApiResult<Json<Value>> {
    let content = b.content.trim().to_string();
    if content.is_empty() {
        return Err(ApiError(AppError::BadRequest("Contenu vide".into())));
    }
    let tag = b.tag.as_deref().unwrap_or("user");
    let now = now_iso();
    let id = st.db.execute(
        "INSERT INTO memories (content, tag, created_at) VALUES (?,?,?)",
        &[&content.as_str(), &tag, &now.as_str()],
    )?;
    Ok(Json(json!({ "id": id })))
}

async fn remove(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    st.db.execute("DELETE FROM memories WHERE id=?", &[&id])?;
    Ok(Json(json!({ "deleted": id })))
}
