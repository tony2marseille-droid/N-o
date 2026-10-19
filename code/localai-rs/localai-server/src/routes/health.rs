use std::sync::Arc;

use axum::{routing::get, Json, Router};
use localai_core::{APP_NAME, APP_VERSION};
use serde_json::{json, Value};

use crate::AppState;

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/api/health", get(health))
}

async fn health() -> Json<Value> {
    Json(json!({ "status": "ok", "app": APP_NAME, "version": APP_VERSION }))
}
