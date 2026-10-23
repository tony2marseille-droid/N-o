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
        .route("/api/telegram/send", post(send))
        .route("/api/livekit/status", get(lk_status))
        .route("/api/livekit/config", put(lk_config))
        .route("/api/livekit/token", get(lk_token).post(lk_token))
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    // Etat reel du poller RUST (l'ancien statut Python pointait sur un
    // poller supprime -> poller_running toujours faux).
    let configured = !st.db.get_setting("telegram_token", "").is_empty();
    let enabled = st.db.get_setting("telegram_enabled", "0") == "1";
    let model = st.db.get_setting("telegram_model", "");
    let model = if model.is_empty() { "auto".to_string() } else { model };
    let sessions = st
        .db
        .query_one_json("SELECT COUNT(*) AS n FROM telegram_sessions", &[])?;
    let n = sessions
        .as_ref()
        .and_then(|s| s.get("n"))
        .and_then(|v| v.as_i64())
        .unwrap_or(0);
    Ok(Json(json!({
        "configured": configured,
        "enabled": enabled,
        "model": model,
        "poller_running": crate::telegram::poller_actif(),
        "last_error": "",
        "sessions": n,
    })))
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

#[derive(Debug, Deserialize)]
pub struct TgSend {
    pub text: Option<String>,
    pub images: Option<Vec<String>>,
    pub chat_id: Option<i64>,
}

/// Envoi manuel depuis l'interface web : POST /api/telegram/send
/// { text, images?: [chemins], chat_id? } -> derniere session par defaut.
async fn send(State(st): State<Arc<crate::AppState>>, Json(b): Json<TgSend>) -> ApiResult<Json<Value>> {
    let token = st.db.get_setting("telegram_token", "");
    if token.is_empty() {
        return Ok(Json(json!({ "ok": false, "error": "pas de token configure" })));
    }
    if st.db.get_setting("telegram_enabled", "0") != "1" {
        return Ok(Json(json!({ "ok": false, "error": "telegram desactive" })));
    }
    let text = b.text.unwrap_or_default();
    let images = b.images.unwrap_or_default();
    if text.trim().is_empty() && images.is_empty() {
        return Ok(Json(json!({ "ok": false, "error": "rien a envoyer" })));
    }
    let cid = match b.chat_id {
        Some(c) => Some(c),
        None => st
            .db
            .query_one_json("SELECT chat_id FROM telegram_sessions ORDER BY created_at DESC LIMIT 1", &[])?
            .and_then(|r| r.get("chat_id").and_then(|v| v.as_i64())),
    };
    let Some(cid) = cid else {
        return Ok(Json(json!({ "ok": false, "error": "aucune session telegram (envoyez /start au bot)" })));
    };
    let client = reqwest::Client::new();
    let mut envoyes = 0usize;
    if !text.trim().is_empty() {
        let texte: String = text.chars().take(4000).collect();
        let r = client
            .post(format!("https://api.telegram.org/bot{token}/sendMessage"))
            .json(&json!({ "chat_id": cid, "text": texte }))
            .send()
            .await
            .map_err(localai_core::AppError::from)?;
        if !r.status().is_success() {
            return Ok(Json(json!({ "ok": false, "error": format!("sendMessage HTTP {}", r.status()) })));
        }
        envoyes += 1;
    }
    // Images : garde-fou anti path-traversal (meme regle que /api/sd/file).
    let root = st
        .settings
        .root_dir
        .canonicalize()
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("root: {e}"))))?;
    let mut images_ok = 0usize;
    for chemin in images.iter().take(10) {
        let cand = root.join(chemin.trim_start_matches(['/', '\\'].as_slice()));
        let Ok(canon) = cand.canonicalize() else { continue };
        if !canon.starts_with(&root) || !canon.is_file() {
            continue;
        }
        let ext_image = canon
            .extension()
            .and_then(|e| e.to_str())
            .map(|e| matches!(e.to_ascii_lowercase().as_str(), "png" | "jpg" | "jpeg" | "webp"))
            .unwrap_or(false);
        if !ext_image {
            continue;
        }
        crate::telegram::envoyer_image(&client, &token, cid, &canon.to_string_lossy(), "Envoye depuis Neo").await;
        images_ok += 1;
    }
    if envoyes == 0 && images_ok == 0 {
        return Ok(Json(json!({ "ok": false, "error": "aucune image valide" })));
    }
    Ok(Json(json!({ "ok": true, "sent": envoyes, "images": images_ok })))
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
