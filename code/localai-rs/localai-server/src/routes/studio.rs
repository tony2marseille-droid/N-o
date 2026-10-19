use std::sync::Arc;

use axum::{extract::State, routing::{get, post}, Json, Router};
use localai_core::ollama::Msg;
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/studio/write", post(write))
        .route("/api/studio/voiceover", post(voiceover))
        .route("/api/studio/voices", get(voices))
        .route("/api/studio/subs", post(subs))
        .route("/api/studio/video", post(video))
        .route("/api/studio/status", get(status))
}

#[derive(Debug, Deserialize)]
pub struct WriteBody {
    pub topic: String,
    pub model: Option<String>,
}

async fn write(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<WriteBody>,
) -> ApiResult<Json<Value>> {
    let model = b.model.as_deref().unwrap_or("localai-prog-latest");
    let text = st
        .ollama
        .chat(model, vec![Msg { role: "user".into(), content: format!("Redige un texte structure sur : {}", b.topic) }], 0.7, 1500)
        .await?;
    Ok(Json(json!({ "text": text })))
}

#[derive(Debug, Deserialize)]
pub struct VoiceBody {
    pub text: String,
    pub voice: Option<String>,
}

async fn voiceover(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<VoiceBody>,
) -> ApiResult<Json<Value>> {
    // Reutilise le moteur TTS via le pont, rend le chemin du wav.
    let t = b.text.replace('\'', "");
    let v = b.voice.unwrap_or_default().replace('\'', "");
    let r = pybridge::py_json(&st.settings.root_dir, &format!(
        "from backend.tts.engine import synthesize_to_wav, new_wav_path, cleanup_cache; from backend.core.config import settings; \
         p = new_wav_path(settings.tts_cache_dir); synthesize_to_wav('{}', p, voice_id='{}', rate=185); cleanup_cache(settings.tts_cache_dir); print(json.dumps({{'wav': str(p)}}))",
        t, v)).await?;
    Ok(Json(r))
}

async fn voices(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.tts.engine import list_voices; print(json.dumps({'voices': list_voices()}))").await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct SubsBody {
    pub text: String,
}

async fn subs(
    State(_st): State<Arc<crate::AppState>>,
    Json(b): Json<SubsBody>,
) -> ApiResult<Json<Value>> {
    // Sous-titres SRT simples : decoupe en phrases, 3 s par bloc.
    let sentences: Vec<&str> = b.text.split(['.', '!', '?']).map(|s| s.trim()).filter(|s| !s.is_empty()).collect();
    let mut srt = String::new();
    for (i, s) in sentences.iter().enumerate() {
        let t0 = i * 3;
        let t1 = t0 + 3;
        srt.push_str(&format!("{}\n00:{:02}:{:02},000 --> 00:{:02}:{:02},000\n{}\n\n",
            i + 1, t0 / 60, t0 % 60, t1 / 60, t1 % 60, s));
    }
    Ok(Json(json!({ "srt": srt, "blocks": sentences.len() })))
}

#[derive(Debug, Deserialize)]
pub struct VideoBody {
    pub image: Option<String>,
    pub audio: Option<String>,
}

async fn video(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<VideoBody>,
) -> ApiResult<Json<Value>> {
    // ffmpeg : image fixe + audio -> mp4 (si ffmpeg dispo).
    let out = st.settings.data_dir.join(format!("studio_{}.mp4", chrono::Local::now().format("%Y%m%d%H%M%S")));
    let ffmpeg = which_ffmpeg();
    let bin = ffmpeg.as_str().unwrap_or("ffmpeg").to_string();
    let mut cmd = tokio::process::Command::new(bin);
    cmd.arg("-y");
    if let Some(img) = b.image { cmd.args(["-loop", "1", "-i", &img]); }
    if let Some(aud) = b.audio { cmd.args(["-i", &aud]); }
    cmd.args(["-shortest", "-pix_fmt", "yuv420p"]);
    cmd.arg(&out);
    let o = cmd.output().await.map_err(|e| ApiError(localai_core::AppError::Internal(format!("ffmpeg: {e}"))))?;
    if !o.status.success() {
        let err = String::from_utf8_lossy(&o.stderr);
        let err: String = err.chars().take(300).collect();
        return Err(ApiError(localai_core::AppError::Internal(format!("ffmpeg: {err}"))));
    }
    Ok(Json(json!({ "video": out.to_string_lossy() })))
}

async fn status(State(_st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let path = which_ffmpeg();
    let bin = path.as_str().unwrap_or("ffmpeg");
    let out = tokio::process::Command::new(bin).arg("-version").output().await;
    let ok = out.map(|o| o.status.success()).unwrap_or(false);
    Ok(Json(json!({
        "ffmpeg": ok,
        "ffmpeg_path": path,
        "tts": true,
        "modele_default": "gemma3:4b",
        "catalogue": ["redaction (write)", "voix off (voiceover)", "sous-titres (subs)", "montage video (video)"],
    })))
}

fn which_ffmpeg() -> Value {
    for p in ["C:\\ProgramData\\chocolatey\\bin\\ffmpeg.exe", "C:\\ffmpeg\\bin\\ffmpeg.exe"] {
        if std::path::Path::new(p).is_file() { return json!(p); }
    }
    if let Ok(paths) = std::env::var("PATH") {
        for dir in std::env::split_paths(&paths) {
            let c = dir.join("ffmpeg.exe");
            if c.is_file() { return json!(c.to_string_lossy()); }
        }
    }
    Value::Null
}
