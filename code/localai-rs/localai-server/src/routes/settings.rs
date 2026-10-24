use std::sync::Arc;

use axum::{extract::State, routing::{get}, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::ApiResult;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/settings", get(get_all).put(set_one))
        .route("/api/settings/:key", get(get_one))
}

const ALLOWED: &[&str] = &[
    "tool_level", "tts_voice", "tts_rate", "default_model", "inference_backend",
    "auto_learn", "tts_autoplay", "tts_followup", "theme", "reflection_mode",
    "voice_model", "stt_model", "telegram_token", "telegram_enabled",
    "telegram_model", "livekit_url", "livekit_api_key", "livekit_api_secret",
    "style_auto", "telegram_dangerous_tools",
    // Produit Néo : langue, premiere installation, nom de l'assistant,
    // centres d'interet (apprentissage par categorie), securite API.
    "language", "onboarded", "assistant_name", "interests",
    "api_token", "listen_lan", "image_format",
];

async fn get_all(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let rows = st.db.query_all_json("SELECT key, value FROM app_settings", &[])?;
    let mut map = serde_json::Map::new();
    for r in rows {
        if let (Some(k), Some(v)) = (
            r.get("key").and_then(|x| x.as_str()),
            r.get("value").cloned(),
        ) {
            map.insert(k.to_string(), v);
        }
    }
    Ok(Json(json!({ "settings": Value::Object(map) })))
}

#[derive(Debug, Deserialize)]
pub struct SetBody {
    pub key: String,
    pub value: String,
}

async fn set_one(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<SetBody>,
) -> ApiResult<Json<Value>> {
    if !ALLOWED.contains(&b.key.as_str()) {
        return Ok(Json(json!({ "ok": false, "error": format!("Cle non autorisee: {}", b.key) })));
    }
    st.db.set_setting(&b.key, &b.value)?;
    Ok(Json(json!({ "ok": true })))
}

async fn get_one(
    State(st): State<Arc<crate::AppState>>,
    axum::extract::Path(key): axum::extract::Path<String>,
) -> ApiResult<Json<Value>> {
    let v = st.db.get_setting(&key, "");
    Ok(Json(json!({ "key": key, "value": v })))
}
