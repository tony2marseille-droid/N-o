//! TTS : pont vers le moteur Python (pyttsx3/SAPI) en attendant un moteur natif.
//! Le Rust possede la couche HTTP, le Python ne fait que synthetiser.
use std::sync::Arc;

use axum::{
    body::Body,
    extract::State,
    http::{header, StatusCode},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::process::Command;

use crate::error::{ApiError, ApiResult};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/tts/voices", get(voices))
        .route("/api/tts", post(synthesize))
}

#[derive(Debug, Deserialize)]
pub struct TtsBody {
    pub text: String,
    pub voice: Option<String>,
    pub rate: Option<i32>,
}

fn venv_python(root: &std::path::Path) -> std::path::PathBuf {
    root.join(".venv").join("Scripts").join("python.exe")
}

async fn voices(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let py = venv_python(&st.settings.root_dir);
    let code = "from backend.tts.engine import list_voices; import json; print(json.dumps(list_voices()))";
    let out = Command::new(&py)
        .args(["-c", code])
        .current_dir(&st.settings.root_dir)
        .output()
        .await
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("tts python: {e}"))))?;
    if !out.status.success() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(ApiError(localai_core::AppError::Internal(format!("tts voices: {err}"))));
    }
    let v: Value = serde_json::from_slice(&out.stdout)
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    Ok(Json(json!({ "voices": v })))
}

async fn synthesize(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<TtsBody>,
) -> ApiResult<Response> {
    if b.text.trim().is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("texte vide".into())));
    }
    let voice = b.voice.clone().unwrap_or_default();
    let rate = b.rate.unwrap_or(185).clamp(50, 400);
    let cache = st.settings.data_dir.join("tts");
    let _ = std::fs::create_dir_all(&cache);
    let fname = format!("tts_{}.wav", chrono::Local::now().format("%Y%m%d_%H%M%S_%f"));
    let out_path = cache.join(&fname);
    let py = venv_python(&st.settings.root_dir);
    // Le texte passe par un fichier pour eviter les problemes de quoting.
    let txt_path = cache.join(format!("{fname}.txt"));
    std::fs::write(&txt_path, &b.text)
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let code = format!(
        "from backend.tts.engine import synthesize_to_wav; from pathlib import Path; \
         synthesize_to_wav(Path(r'{}').read_text(encoding='utf-8'), Path(r'{}'), voice_id=r'{}', rate={})",
        txt_path.display(), out_path.display(), voice.replace('\'', ""), rate
    );
    let out = Command::new(&py)
        .args(["-c", &code])
        .current_dir(&st.settings.root_dir)
        .output()
        .await
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("tts python: {e}"))))?;
    let _ = std::fs::remove_file(&txt_path);
    if !out.status.success() || !out_path.is_file() {
        let err = String::from_utf8_lossy(&out.stderr);
        return Err(ApiError(localai_core::AppError::Internal(format!("tts synthese: {err}"))));
    }
    let bytes = std::fs::read(&out_path)
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    Ok(Response::builder()
        .status(StatusCode::OK)
        .header(header::CONTENT_TYPE, "audio/wav")
        .body(Body::from(bytes))
        .unwrap())
}
