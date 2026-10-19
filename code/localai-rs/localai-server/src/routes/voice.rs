use std::sync::Arc;

use axum::{
    body::Body,
    extract::{Multipart, Path, State},
    http::{header, StatusCode},
    response::Response,
    routing::{get, post},
    Json, Router,
};
use serde_json::Value;

use crate::error::{ApiError, ApiResult};
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/voice/status", get(status))
        .route("/api/voice/transcribe", post(transcribe))
        .route("/api/voice/wav/:filename", get(wav))
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.api.routes_voice import status; print(json.dumps(asyncio.run(status()), default=str))").await?;
    Ok(Json(v))
}

async fn transcribe(
    State(st): State<Arc<crate::AppState>>,
    mut mp: Multipart,
) -> ApiResult<Json<Value>> {
    let cache = st.settings.data_dir.join("stt");
    let _ = std::fs::create_dir_all(&cache);
    while let Some(field) = mp
        .next_field()
        .await
        .map_err(|e| ApiError(localai_core::AppError::BadRequest(e.to_string())))?
    {
        let bytes = field
            .bytes()
            .await
            .map_err(|e| ApiError(localai_core::AppError::BadRequest(e.to_string())))?;
        if bytes.is_empty() {
            continue;
        }
        let tmp = cache.join(format!("up_{}.wav", chrono::Local::now().format("%Y%m%d%H%M%S%f")));
        std::fs::write(&tmp, &bytes)
            .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
        let v = pybridge::py_json(&st.settings.root_dir, &format!(
            "from backend.core.stt import transcribe; print(json.dumps({{'text': transcribe(r'{}')}}))",
            tmp.display())).await;
        let _ = std::fs::remove_file(&tmp);
        let v = v?;
        return Ok(Json(v));
    }
    Err(ApiError(localai_core::AppError::BadRequest("aucun audio".into())))
}

async fn wav(
    State(st): State<Arc<crate::AppState>>,
    Path(filename): Path<String>,
) -> ApiResult<Response> {
    let safe: String = filename.chars().filter(|c| c.is_alphanumeric() || *c == '.' || *c == '_' || *c == '-').collect();
    for dir in [st.settings.data_dir.join("tts"), st.settings.data_dir.join("stt")] {
        let p = dir.join(&safe);
        if p.is_file() {
            let bytes = std::fs::read(&p)
                .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
            return Ok(Response::builder()
                .status(StatusCode::OK)
                .header(header::CONTENT_TYPE, "audio/wav")
                .body(Body::from(bytes))
                .unwrap());
        }
    }
    Err(ApiError(localai_core::AppError::NotFound("wav introuvable".into())))
}
