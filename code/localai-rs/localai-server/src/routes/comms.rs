use std::sync::Arc;

use axum::{extract::State, routing::{get, put, post}, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/telegram/status", get(status))
        .route("/api/telegram/config", put(config))
        .route("/api/telegram/check", post(check))
        .route("/api/livekit/status", get(lk_status))
        .route("/api/livekit/config", put(lk_config))
        .route("/api/livekit/token", get(lk_token).post(lk_token))
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = crate::pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.api.routes_telegram import telegram_status; print(json.dumps(asyncio.run(telegram_status()), default=str))").await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct TgConfig {
    pub token: Option<String>,
    pub enabled: Option<bool>,
    pub model: Option<String>,
}

async fn config(State(st): State<Arc<crate::AppState>>, Json(b): Json<TgConfig>) -> ApiResult<Json<Value>> {
    if let Some(t) = b.token { st.db.set_setting("telegram_token", &t)?; }
    if let Some(e) = b.enabled { st.db.set_setting("telegram_enabled", if e { "1" } else { "0" })?; }
    if let Some(m) = b.model { st.db.set_setting("telegram_model", &m)?; }
    Ok(Json(json!({ "ok": true })))
}

async fn check(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let token = st.db.get_setting("telegram_token", "");
    if token.is_empty() {
        return Ok(Json(json!({ "ok": false, "error": "pas de token configure" })));
    }
    let url = format!("https://api.telegram.org/bot{token}/getMe");
    let r = reqwest::get(&url).await.map_err(localai_core::AppError::from)?;
    Ok(Json(json!({ "ok": r.status().is_success() })))
}

async fn lk_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = crate::pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.api.routes_livekit import livekit_status; print(json.dumps(asyncio.run(livekit_status()), default=str))").await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct LkConfig {
    pub url: Option<String>,
    pub api_key: Option<String>,
    pub api_secret: Option<String>,
}

async fn lk_config(State(st): State<Arc<crate::AppState>>, Json(b): Json<LkConfig>) -> ApiResult<Json<Value>> {
    if let Some(v) = b.url { st.db.set_setting("livekit_url", &v)?; }
    if let Some(v) = b.api_key { st.db.set_setting("livekit_api_key", &v)?; }
    if let Some(v) = b.api_secret { st.db.set_setting("livekit_api_secret", &v)?; }
    Ok(Json(json!({ "ok": true })))
}

async fn lk_token(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    use base64::{engine::general_purpose::URL_SAFE_NO_PAD, Engine};
    use hmac::{Hmac, Mac};
    let key = st.db.get_setting("livekit_api_key", "");
    let secret = st.db.get_setting("livekit_api_secret", "");
    if key.is_empty() || secret.is_empty() {
        return Ok(Json(json!({ "ok": false, "error": "livekit non configure" })));
    }
    let now = chrono::Utc::now().timestamp();
    let header = URL_SAFE_NO_PAD.encode(r#"{"alg":"HS256","typ":"JWT"}"#);
    let payload = URL_SAFE_NO_PAD.encode(format!(
        "{{\"iss\":\"{key}\",\"sub\":\"assistant\",\"iat\":{now},\"exp\":{},\"video\":{{\"room\":\"localai\",\"roomJoin\":true}}}}",
        now + 3600));
    let msg = format!("{header}.{payload}");
    let mut mac = Hmac::<sha2::Sha256>::new_from_slice(secret.as_bytes())
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    mac.update(msg.as_bytes());
    let sig = URL_SAFE_NO_PAD.encode(mac.finalize().into_bytes());
    Ok(Json(json!({ "ok": true, "token": format!("{msg}.{sig}") })))
}
