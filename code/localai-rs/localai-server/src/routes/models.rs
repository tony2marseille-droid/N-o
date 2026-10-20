use std::collections::HashSet;
use std::convert::Infallible;
use std::sync::Arc;

use axum::{
    extract::{Path, State},
    response::sse::{Event, KeepAlive, Sse},
    routing::{delete, get, post},
    Json, Router,
};
use futures_util::{Stream, StreamExt};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::mpsc;

use crate::error::{ApiError, ApiResult};
use crate::AppState;

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/api/models", get(list_models))
        .route("/api/models/installed", get(list_installed))
        .route("/api/models/install", post(install))
        .route("/api/models/update", post(update))
        .route("/api/models/:tag", delete(delete_model))
        .route("/api/models/load", post(load))
        .route("/api/models/unload", post(unload))
}

fn same_model_tag(a: &str, b: &str) -> bool {
    let a = a.strip_suffix(":latest").unwrap_or(a);
    let b = b.strip_suffix(":latest").unwrap_or(b);
    a == b
}

/// Allocation observée via Ollama : mémoire totale, VRAM et part estimée CPU/GPU.
/// Le pourcentage porte sur l'allocation mémoire, pas sur le nombre de couches.
fn runtime_for_model(ps: &Value, tag: &str) -> Value {
    let loaded = ps
        .get("models")
        .and_then(Value::as_array)
        .and_then(|models| {
            models.iter().find(|model| {
                model
                    .get("name")
                    .and_then(Value::as_str)
                    .or_else(|| model.get("model").and_then(Value::as_str))
                    .is_some_and(|name| same_model_tag(name, tag))
            })
        });
    let Some(model) = loaded else {
        return Value::Null;
    };

    let total_bytes = model.get("size").and_then(Value::as_u64).unwrap_or(0);
    let vram_bytes = model
        .get("size_vram")
        .and_then(Value::as_u64)
        .unwrap_or(0);
    let cpu_bytes_estimated = total_bytes.saturating_sub(vram_bytes);
    let gpu_memory_share_pct = if total_bytes == 0 {
        Value::Null
    } else {
        json!((vram_bytes as f64 / total_bytes as f64 * 100.0).round() as u8)
    };

    json!({
        "total_bytes": total_bytes,
        "vram_bytes": vram_bytes,
        "cpu_bytes_estimated": cpu_bytes_estimated,
        "gpu_memory_share_pct": gpu_memory_share_pct,
        "context_length": model.get("context_length"),
        "runner": model.get("runner"),
    })
}

async fn list_models(State(st): State<Arc<AppState>>) -> ApiResult<Json<Value>> {
    // Catalogue (pont Python) + installes/charge (Ollama), fusion comme le Python.
    let cat: Value = crate::pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.core.catalog import build_catalog; print(json.dumps([e.__dict__ for e in asyncio.run(build_catalog())], default=str))")
        .await
        .unwrap_or(json!([]));
    let tags = st.ollama.tags().await.unwrap_or(json!({"models": []}));
    let ps = st.ollama.ps().await.unwrap_or(json!({"models": []}));
    let installed: Vec<&Value> = tags.get("models").and_then(|m| m.as_array())
        .map(|a| a.iter().collect()).unwrap_or_default();
    let loaded: Vec<String> = ps.get("models").and_then(|m| m.as_array())
        .map(|a| a.iter().filter_map(|x| {
            x.get("name").and_then(|n| n.as_str())
                .or_else(|| x.get("model").and_then(|n| n.as_str()))
                .map(|s| s.to_string())
        }).collect()).unwrap_or_default();
    let by_tag: std::collections::HashMap<String, &Value> = installed.iter()
        .filter_map(|m| m.get("name").and_then(|n| n.as_str()).map(|t| (t.to_string(), *m)))
        .collect();
    let mut models: Vec<Value> = Vec::new();
    let mut seen = HashSet::new();
    if let Some(entries) = cat.as_array() {
        for e in entries {
            let tag = e.get("tag").and_then(|t| t.as_str()).unwrap_or("").to_string();
            seen.insert(tag.clone());
            let inst = by_tag.get(&tag);
            models.push(json!({
                "id": e.get("id").or_else(|| e.get("tag")).cloned().unwrap_or(json!(tag)),
                "name": e.get("name").cloned().unwrap_or(json!(tag)),
                "tag": tag,
                "family": e.get("family").cloned().unwrap_or(json!("")),
                "capabilities": e.get("capabilities").cloned().unwrap_or(json!([])),
                "size_label": e.get("size_label").cloned().unwrap_or(json!("")),
                "quantization": e.get("quantization").cloned().unwrap_or(json!("")),
                "vram_gb": e.get("vram_gb").cloned().unwrap_or(json!(0.0)),
                "context": e.get("context").cloned().unwrap_or(json!(0)),
                "description": e.get("description").cloned().unwrap_or(json!("")),
                "source": e.get("source").cloned().unwrap_or(json!("")),
                "installed": inst.is_some(),
                "loaded": loaded.iter().any(|l| same_model_tag(l, &tag)),
                "runtime": runtime_for_model(&ps, &tag),
                "size_bytes": inst.and_then(|m| m.get("size").cloned()).unwrap_or(json!(0)),
                "parameter_size": inst.and_then(|m| m.get("parameter_size").cloned()).unwrap_or(json!("")),
            }));
        }
    }
    for m in installed {
            let tag = m.get("name").and_then(|n| n.as_str()).unwrap_or("").to_string();
            if seen.contains(&tag) {
                continue;
        }
        let base = tag.split(':').next().unwrap_or("").to_string();
        if base.starts_with("localai-prog") || base.starts_with("localai-v") {
            continue;
        }
        models.push(json!({
            "id": tag, "name": tag.split(':').next().unwrap_or(&tag),
            "tag": tag,
            "family": m.get("details").and_then(|d| d.get("family")).cloned().unwrap_or(json!("")),
            "capabilities": [],
            "size_label": m.get("details").and_then(|d| d.get("parameter_size")).cloned().unwrap_or(json!("")),
            "quantization": m.get("details").and_then(|d| d.get("quantization_level")).cloned().unwrap_or(json!("")),
            "vram_gb": 0.0, "context": 0, "description": "", "source": "installed",
            "installed": true,
            "loaded": loaded.iter().any(|l| same_model_tag(l, &tag)),
            "runtime": runtime_for_model(&ps, &tag),
            "size_bytes": m.get("size").cloned().unwrap_or(json!(0)),
            "parameter_size": m.get("details").and_then(|d| d.get("parameter_size")).cloned().unwrap_or(json!("")),
        }));
    }
    models.sort_by(|a, b| {
        let ai = a.get("installed").and_then(|v| v.as_bool()).unwrap_or(false);
        let bi = b.get("installed").and_then(|v| v.as_bool()).unwrap_or(false);
        bi.cmp(&ai).then(
            a.get("name").and_then(|n| n.as_str()).unwrap_or("")
                .cmp(b.get("name").and_then(|n| n.as_str()).unwrap_or("")),
        )
    });
    Ok(Json(json!({ "models": models })))
}

async fn list_installed(State(st): State<Arc<AppState>>) -> ApiResult<Json<Value>> {
    let v: Value = crate::pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.inference import get_active_backend; print(json.dumps([vars(m) for m in asyncio.run(get_active_backend().list_installed())], default=str))")
        .await
        .unwrap_or(json!([]));
    let arr = v.as_array().cloned().unwrap_or_default();
    Ok(Json(json!({ "models": arr })))
}

#[derive(Debug, Deserialize, Clone)]
pub struct TagBody {
    pub tag: String,
}

#[derive(Debug, Deserialize, Clone, Default)]
pub struct TagsBody {
    pub tags: Option<Vec<String>>,
}

fn sse_data(v: Value) -> String {
    format!("data: {}\n\n", v)
}

async fn pull_to_sse(base: &str, tag: &str, tx: mpsc::UnboundedSender<String>) -> bool {
    let client = reqwest::Client::new();
    let resp = match client
        .post(format!("{base}/api/pull"))
        .json(&json!({ "name": tag, "stream": true }))
        .send()
        .await
    {
        Ok(r) => r,
        Err(e) => {
            let _ = tx.send(sse_data(json!({
                "type": "error", "tag": tag, "error": e.to_string()
            })));
            return false;
        }
    };
    if !resp.status().is_success() {
        let status = resp.status();
        let detail = resp.text().await.unwrap_or_default();
        let detail: String = detail.chars().take(300).collect();
        let _ = tx.send(sse_data(json!({
            "type": "error", "tag": tag,
            "error": format!("Ollama HTTP {status}: {detail}")
        })));
        return false;
    }
    let mut stream = resp.bytes_stream();
    let mut buf: Vec<u8> = Vec::new();
    while let Some(chunk) = stream.next().await {
        let bytes = match chunk {
            Ok(b) => b,
            Err(e) => {
                let _ = tx.send(sse_data(json!({
                    "type": "error", "tag": tag, "error": e.to_string()
                })));
                return false;
            }
        };
        buf.extend_from_slice(&bytes);
        while let Some(pos) = buf.iter().position(|&b| b == b'\n') {
            let line: Vec<u8> = buf.drain(..=pos).collect();
            let s = String::from_utf8_lossy(&line);
            let s = s.trim();
            if s.is_empty() {
                continue;
            }
            if let Ok(v) = serde_json::from_str::<Value>(s) {
                let done = v.get("status").and_then(|x| x.as_str()) == Some("success");
                let _ = tx.send(sse_data(json!({
                    "type": if done { "done" } else { "progress" },
                    "tag": tag,
                    "status": v.get("status"),
                    "completed": v.get("completed"),
                    "total": v.get("total"),
                })));
                if done {
                    return true;
                }
            }
        }
    }
    let _ = tx.send(sse_data(json!({
        "type": "error", "tag": tag,
        "error": "Flux Ollama terminé sans confirmation de succès"
    })));
    false
}

async fn install(
    State(st): State<Arc<AppState>>,
    Json(b): Json<TagBody>,
) -> ApiResult<Sse<impl Stream<Item = Result<Event, Infallible>>>> {
    let (tx, rx) = mpsc::unbounded_channel::<String>();
    let base = st.ollama.base().to_string();
    let tag = b.tag.clone();
    tokio::spawn(async move { pull_to_sse(&base, &tag, tx).await });
    let raw = tokio_stream::wrappers::UnboundedReceiverStream::new(rx).map(|s| {
        let payload = s.trim_start_matches("data: ").trim().to_string();
        Ok::<_, Infallible>(Event::default().data(payload))
    });
    Ok(Sse::new(raw).keep_alive(KeepAlive::default()))
}

async fn update(
    State(st): State<Arc<AppState>>,
    Json(b): Json<TagsBody>,
) -> ApiResult<Sse<impl Stream<Item = Result<Event, Infallible>>>> {
    let (tx, rx) = mpsc::unbounded_channel::<String>();
    let base = st.ollama.base().to_string();
    let tags: Vec<String> = match b.tags {
        Some(t) => t,
        None => st
            .ollama
            .tags()
            .await
            .ok()
            .and_then(|v| v.get("models").cloned())
            .and_then(|m| {
                m.as_array().map(|a| {
                    a.iter()
                        .filter_map(|x| x.get("name")?.as_str().map(|s| s.to_string()))
                        .collect()
                })
            })
            .unwrap_or_default(),
    };
    let total = tags.len();
    tokio::spawn(async move {
        let mut updated = 0i32;
        for tag in tags {
            let _ = tx.send(sse_data(json!({ "type": "model_start", "tag": tag })));
            let ok = pull_to_sse(&base, &tag, tx.clone()).await;
            if ok {
                updated += 1;
            }
            let _ = tx.send(sse_data(json!({ "type": "model_done", "tag": tag, "ok": ok })));
        }
        let _ = tx.send(sse_data(json!({
            "type": "done",
            "updated": updated,
            "failed": total.saturating_sub(updated as usize)
        })));
    });
    let raw = tokio_stream::wrappers::UnboundedReceiverStream::new(rx).map(|s| {
        let payload = s.trim_start_matches("data: ").trim().to_string();
        Ok::<_, Infallible>(Event::default().data(payload))
    });
    Ok(Sse::new(raw).keep_alive(KeepAlive::default()))
}

async fn delete_model(
    State(st): State<Arc<AppState>>,
    Path(tag): Path<String>,
) -> ApiResult<Json<Value>> {
    let client = reqwest::Client::new();
    let r = client
        .request(reqwest::Method::DELETE, format!("{}/api/delete", st.ollama.base()))
        .json(&json!({ "name": tag }))
        .send()
        .await
        .map_err(localai_core::AppError::from)?;
    if !r.status().is_success() {
        return Err(ApiError(localai_core::AppError::Ollama(format!("delete {tag}: {}", r.status()))));
    }
    Ok(Json(json!({ "deleted": tag })))
}

async fn load(
    State(st): State<Arc<AppState>>,
    Json(b): Json<TagBody>,
) -> ApiResult<Json<Value>> {
    let client = reqwest::Client::new();
    let r = client
        .post(format!("{}/api/generate", st.ollama.base()))
        .json(&json!({
            "model": b.tag,
            "prompt": " ",
            "stream": false,
            "options": { "num_ctx": localai_core::ollama::DEFAULT_NUM_CTX }
        }))
        .send()
        .await
        .map_err(localai_core::AppError::from)?;
    if !r.status().is_success() {
        return Err(ApiError(localai_core::AppError::Ollama(format!("load {}: {}", b.tag, r.status()))));
    }
    Ok(Json(json!({ "loaded": b.tag })))
}

async fn unload(
    State(st): State<Arc<AppState>>,
    Json(b): Json<TagBody>,
) -> ApiResult<Json<Value>> {
    st.ollama.unload(&b.tag).await?;
    Ok(Json(json!({ "unloaded": b.tag })))
}

#[cfg(test)]
mod tests {
    use super::runtime_for_model;
    use serde_json::json;

    #[test]
    fn runtime_reports_gpu_cpu_split_and_context() {
        let ps = json!({
            "models": [{
                "name": "localai-prog-latest:latest",
                "size": 10_000_000_000u64,
                "size_vram": 8_400_000_000u64,
                "context_length": 8192,
                "runner": "llamacpp"
            }]
        });
        let runtime = runtime_for_model(&ps, "localai-prog-latest");
        assert_eq!(runtime["vram_bytes"], json!(8_400_000_000u64));
        assert_eq!(runtime["cpu_bytes_estimated"], json!(1_600_000_000u64));
        assert_eq!(runtime["gpu_memory_share_pct"], json!(84));
        assert_eq!(runtime["context_length"], json!(8192));
        assert_eq!(runtime["runner"], json!("llamacpp"));
    }

    #[test]
    fn runtime_is_null_when_model_is_not_loaded() {
        let ps = json!({ "models": [] });
        assert!(runtime_for_model(&ps, "localai-prog-latest").is_null());
    }
}
