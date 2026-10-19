use std::sync::Arc;

use axum::{extract::{Path, State}, routing::{delete, get, post}, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/finetune/status", get(status))
        .route("/api/finetune/dataset", get(dataset).post(dataset_add))
        .route("/api/finetune/dataset/:index", delete(dataset_del))
        .route("/api/finetune/train", post(train))
        .route("/api/finetune/train/status", get(train_status))
        .route("/api/finetune/convert", post(convert))
        .route("/api/finetune/merge", post(merge))
        .route("/api/finetune/import-ollama", post(import_ollama))
        .route("/api/finetune/rlvr/run", post(rlvr_run))
        .route("/api/finetune/rlvr/status", get(rlvr_status))
}

fn atelier(st: &Arc<crate::AppState>) -> std::path::PathBuf {
    // Via la junction : E:\ia\data\lora_atelier -> I:\ia\data\lora_atelier.
    st.settings.data_dir.join("lora_atelier")
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.api.routes_finetune import get_status; print(json.dumps(asyncio.run(get_status()), default=str))").await?;
    Ok(Json(v))
}

async fn dataset(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let p = atelier(&st).join("sft.jsonl");
    let entries: Vec<Value> = std::fs::read_to_string(&p).map(|t| {
        t.lines().filter(|l| !l.trim().is_empty()).filter_map(|l| serde_json::from_str(l).ok()).collect()
    }).unwrap_or_default();
    Ok(Json(json!({ "entries": entries })))
}

#[derive(Debug, Deserialize)]
pub struct DsEntry {
    pub messages: Vec<Value>,
    pub category: Option<String>,
}

async fn dataset_add(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<DsEntry>,
) -> ApiResult<Json<Value>> {
    let p = atelier(&st).join("sft.jsonl");
    let mut line = json!({ "messages": b.messages });
    if let Some(c) = b.category { line["category"] = json!(c); }
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&p)
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    writeln!(f, "{}", line).map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    Ok(Json(json!({ "ok": true })))
}

async fn dataset_del(
    State(st): State<Arc<crate::AppState>>,
    Path(index): Path<usize>,
) -> ApiResult<Json<Value>> {
    let p = atelier(&st).join("sft.jsonl");
    let text = std::fs::read_to_string(&p).unwrap_or_default();
    let mut lines: Vec<&str> = text.lines().collect();
    if index >= lines.len() {
        return Err(ApiError(localai_core::AppError::NotFound("index hors borne".into())));
    }
    lines.remove(index);
    std::fs::write(&p, lines.join("\n"))
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    Ok(Json(json!({ "ok": true, "restants": lines.len() })))
}

#[derive(Debug, Deserialize, Default)]
pub struct TrainBody {
    pub tag: Option<String>,
    pub dataset: Option<String>,
    pub epochs: Option<i32>,
}

async fn train(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<TrainBody>,
) -> ApiResult<Json<Value>> {
    // Lancement REEL en detache : le serveur Rust est persistant (tache),
    // l'enfant Python/torch lui survit (pas de SSH dans la boucle).
    let tag = b.tag.unwrap_or_else(|| "v9.3".into());
    let tag: String = tag.chars().filter(|c| c.is_alphanumeric() || *c == '.' || *c == '_' || *c == '-').collect();
    if tag.is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("tag invalide".into())));
    }
    let ds: String = b.dataset.unwrap_or_else(|| "sft_localai_v92.jsonl".into()).chars().filter(|c| *c != '\'' && *c != ' ').collect();
    let epochs = b.epochs.unwrap_or(2).clamp(1, 10);
    let root = st.settings.root_dir.clone();
    let py = root.join(".venv").join("Scripts").join("python.exe");
    if (!py.is_file()) {
        return Err(ApiError(localai_core::AppError::Internal("venv python absent".into())));
    }
    let at = atelier(&st);
    let log = at.join(format!("train_{tag}.out"));
    let logf = std::fs::File::create(&log)
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let logf2 = logf.try_clone()
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let mut cmd = tokio::process::Command::new(&py);
    cmd.args(["-u", "pipeline.py", "train", "--tag", &tag, "--dataset", &ds,
              "--epochs", &epochs.to_string(), "--ckpt", "50", "--rank", "16",
              "--timeout", "172800", "--resume", "--no-unload"]);
    cmd.current_dir(&root);
    cmd.stdout(logf);
    cmd.stderr(logf2);
    cmd.kill_on_drop(false);
    let child = cmd.spawn()
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("lancement train: {e}"))))?;
    let pid = child.id().unwrap_or(0);
    Ok(Json(json!({ "ok": true, "tag": tag, "pid": pid, "log": log.to_string_lossy() })))
}
async fn train_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "import glob, json, os; ms = sorted(glob.glob('I:/ia/logs/train_*.out') + glob.glob('E:/ia/logs/train_*.out')); print(json.dumps({'logs': ms[-5:]}))").await?;
    Ok(Json(v))
}

async fn convert(
    State(_st): State<Arc<crate::AppState>>,
    Json(b): Json<Value>,
) -> ApiResult<Json<Value>> {
    let tag = b.get("tag").and_then(|t| t.as_str()).unwrap_or("v9.2").replace('\'', "");
    Ok(Json(json!({ "ok": true, "note": format!("lancez : pipeline.py convert --tag {tag} --f16 --quant q8_0 (GPU/CPU lourd, via le CLI)") })))
}

async fn merge(
    State(_st): State<Arc<crate::AppState>>,
    Json(b): Json<Value>,
) -> ApiResult<Json<Value>> {
    let tag = b.get("tag").and_then(|t| t.as_str()).unwrap_or("v9.2").replace('\'', "");
    let ckpt = b.get("ckpt").and_then(|t| t.as_i64()).unwrap_or(752);
    Ok(Json(json!({ "ok": true, "note": format!("lancez : pipeline.py merge-gguf --tag {tag} --ckpt {ckpt} --rank 16 --alpha 64 --quant q8_0") })))
}

async fn import_ollama(
    State(_st): State<Arc<crate::AppState>>,
    Json(b): Json<Value>,
) -> ApiResult<Json<Value>> {
    let tag = b.get("tag").and_then(|t| t.as_str()).unwrap_or("v9.2").replace('\'', "");
    Ok(Json(json!({ "ok": true, "note": format!("lancez : pipeline.py deploy --tag {tag} --modelfile E:\\ia\\Modelfile_{tag}") })))
}

async fn rlvr_run(State(_st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    Ok(Json(json!({ "ok": true, "note": "generer() = 200 cibles / 800 essais (tres long) : lancez via Python : from backend.core.rlvr import generer" })))
}

async fn rlvr_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.api.routes_finetune import rlvr_status; print(json.dumps(asyncio.run(rlvr_status()), default=str))").await?;
    Ok(Json(v))
}
