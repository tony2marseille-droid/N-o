use std::sync::Arc;

use axum::{
    extract::State,
    response::sse::{Event, KeepAlive, Sse},
    routing::post,
    Json, Router,
};
use futures_util::{Stream, StreamExt};
use localai_core::ollama::{Msg, DEFAULT_NUM_CTX};
use crate::error::ApiError;
use localai_core::AppError;
use serde::Deserialize;
use serde_json::{json, Value};
use std::convert::Infallible;
use tokio::sync::mpsc;

use crate::AppState;

mod vision;

pub fn router() -> Router<Arc<AppState>> {
    Router::new().route("/api/chat", post(chat)).layer(vision::body_limit())
}

#[derive(Debug, Deserialize, Clone, Default)]
pub struct ChatBody {
    pub conversation_id: Option<i64>,
    pub model: Option<String>,
    pub message: String,
    pub temperature: Option<f32>,
    pub regenerate: Option<bool>,
    pub agent_id: Option<String>,
    pub tools_enabled: Option<bool>,
    pub rag_enabled: Option<bool>,
    pub images: Option<Vec<String>>,
    pub web_enabled: Option<bool>,
}

fn sse_data(v: serde_json::Value) -> String {
    format!("data: {}\n\n", v)
}

const NUM_CTX: usize = DEFAULT_NUM_CTX;
const TOOL_OUT_LIMIT: usize = 4000;

fn is_vision_model(tag: &str) -> bool {
    let t = tag.to_lowercase();
    t.contains("vision")
        || t.contains("vl")
        || t.contains("llava")
        || t.contains("qwen2.5vl")
}

pub async fn chat(
    State(st): State<Arc<AppState>>,
    Json(b): Json<ChatBody>,
) -> Result<Sse<impl Stream<Item = Result<Event, Infallible>>>, ApiError> {
    if b.message.trim().is_empty() {
        return Err(ApiError(AppError::BadRequest("message vide".into())));
    }

    let mut model = if b.model.as_deref().unwrap_or("auto") == "auto" {
        "localai-prog-v9.2-q5km:latest".to_string()
    } else {
        b.model.clone().unwrap_or_default()
    };

    let mut history: Vec<Value> = Vec::new();
    // TODO: restaurer la récupération des messages de conversation après vérification de l'API Db
    let _ = b.conversation_id;

    let n_images = vision::attach_images(&mut history, &b.images.clone().unwrap_or_default());
    if n_images > 0 {
        model = vision::VISION_MODEL.to_string();
    }

    let (tx, rx) = mpsc::unbounded_channel::<String>();
    let tx_meta = tx.clone();
    let _ = tx_meta.send(sse_data(json!({
        "type": "meta",
        "conversation_id": b.conversation_id,
        "model": model,
    })));

    let body = serde_json::json!({
        "messages": history,
        "think": false,
        "options": {
            "temperature": b.temperature.unwrap_or(0.5),
            "num_predict": 2048,
            "num_ctx": NUM_CTX,
        },
    });

    let model2 = model.clone();
    let res = st.ollama.chat_stream(&model2, body, |tok| {
        let _ = tx.send(sse_data(json!({ "type": "token", "content": tok })));
    }).await;

    match res {
        Ok(full) => {
            let _ = tx.send(sse_data(json!({
                "type": "done",
                "conversation_id": b.conversation_id,
                "duration_ms": 0,
            })));
        }
        Err(e) => {
            let _ = tx.send(sse_data(json!({ "type": "error", "error": e.to_string() })));
        }
    }

    let stream = tokio_stream::wrappers::UnboundedReceiverStream::new(rx);
    let raw = stream.map(|s| {
        let payload = s.trim_start_matches("data: ").trim().to_string();
        Ok::<_, Infallible>(Event::default().data(payload))
    });
    Ok(Sse::new(raw).keep_alive(KeepAlive::default()))
}
