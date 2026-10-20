use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    routing::{delete, get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/finetune/status", get(status))
        .route("/api/finetune/dataset", get(dataset).post(dataset_add))
        .route("/api/finetune/dataset/:index", delete(dataset_del))
        .route("/api/finetune/examples", get(examples_list).post(examples_add))
        .route("/api/finetune/examples/:index", delete(examples_del))
        .route("/api/finetune/merge", post(merge))
        .route("/api/finetune/train", post(train))
        .route("/api/finetune/train/status", get(train_status))
        .route("/api/finetune/convert", post(convert))
        .route("/api/finetune/convert/status", get(convert_status))
        .route("/api/finetune/import-ollama", post(import_ollama))
        .route("/api/finetune/import-ollama/status", get(import_status))
        .route("/api/finetune/rlvr/run", post(rlvr_run))
        .route("/api/finetune/rlvr/status", get(rlvr_status))
}

fn atelier(st: &Arc<crate::AppState>) -> std::path::PathBuf {
    st.settings.data_dir.join("lora_atelier")
}

fn venv_python(st: &Arc<crate::AppState>) -> std::path::PathBuf {
    st.settings.root_dir.join(".venv").join("Scripts").join("python.exe")
}

pub(crate) fn now_unix() -> u64 {
    std::time::SystemTime::now()
        .duration_since(std::time::UNIX_EPOCH)
        .map(|d| d.as_secs())
        .unwrap_or(0)
}

fn read_jsonl(p: &std::path::Path) -> Vec<Value> {
    std::fs::read_to_string(p)
        .map(|t| {
            t.lines()
                .filter(|l| !l.trim().is_empty())
                .filter_map(|l| serde_json::from_str(l).ok())
                .collect()
        })
        .unwrap_or_default()
}

fn write_jsonl(p: &std::path::Path, entries: &[Value]) -> bool {
    use std::io::Write;
    let Ok(mut f) = std::fs::File::create(p) else { return false; };
    for e in entries {
        if writeln!(f, "{e}").is_err() {
            return false;
        }
    }
    true
}

fn clean_tag(s: &str) -> String {
    s.chars()
        .filter(|c| c.is_alphanumeric() || *c == '.' || *c == '_' || *c == '-')
        .collect()
}

pub(crate) async fn pid_alive(pid: u32) -> bool {
    if pid == 0 {
        return false;
    }
    let Ok(out) = tokio::process::Command::new("tasklist")
        .args(["/NH", "/FO", "CSV", "/FI", &format!("PID eq {pid}")])
        .output()
        .await
    else {
        return false;
    };
    String::from_utf8_lossy(&out.stdout).contains(&format!("\"{pid}\""))
}

pub(crate) async fn spawn_detached(
    py: &std::path::Path,
    args: &[&str],
    cwd: &std::path::Path,
    log: &std::path::Path,
) -> Result<u32, String> {
    if let Some(d) = log.parent() {
        let _ = std::fs::create_dir_all(d);
    }
    let f = std::fs::File::create(log).map_err(|e| e.to_string())?;
    let f2 = f.try_clone().map_err(|e| e.to_string())?;
    let mut cmd = tokio::process::Command::new(py);
    cmd.args(args).current_dir(cwd).stdout(f).stderr(f2).kill_on_drop(false);
    let child = cmd.spawn().map_err(|e| e.to_string())?;
    Ok(child.id().unwrap_or(0))
}

fn write_meta(at: &std::path::Path, name: &str, v: Value) {
    let _ = std::fs::create_dir_all(at);
    let _ = std::fs::write(at.join(format!("{name}.json")), v.to_string());
}

fn latest_meta(at: &std::path::Path, prefix: &str) -> Option<(std::path::PathBuf, Value)> {
    let mut best: Option<(std::path::PathBuf, std::time::SystemTime)> = None;
    if let Ok(rd) = std::fs::read_dir(at) {
        for e in rd.flatten() {
            let p = e.path();
            let Some(name) = p.file_name().and_then(|n| n.to_str()) else { continue };
            if !name.starts_with(prefix) || !name.ends_with(".json") {
                continue;
            }
            let Ok(m) = e.metadata().and_then(|m| m.modified()) else { continue };
            if best.as_ref().map(|(_, t)| m > *t).unwrap_or(true) {
                best = Some((p, m));
            }
        }
    }
    let (p, _) = best?;
    let v: Value = serde_json::from_str(&std::fs::read_to_string(&p).ok()?).ok()?;
    Some((p, v))
}

fn exit_code(txt: &str) -> Option<i64> {
    for l in txt.lines().rev() {
        if let Some(i) = l.find("-> exit ") {
            if let Ok(n) = l[i + 8..].trim().parse::<i64>() {
                return Some(n);
            }
        }
    }
    None
}

fn job_log(at: &std::path::Path, logs: &std::path::Path, kind: &str, tag: &str) -> String {
    let out = at.join(format!("{kind}_{tag}.out"));
    if let Ok(t) = std::fs::read_to_string(&out) {
        if !t.trim().is_empty() {
            return t;
        }
    }
    std::fs::read_to_string(logs.join(format!("{kind}_{tag}.log"))).unwrap_or_default()
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "import asyncio, json; from backend.api.routes_finetune import get_status; print(json.dumps(asyncio.run(get_status()), default=str))").await?;
    Ok(Json(v))
}

async fn dataset(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let p = atelier(&st).join("sft.jsonl");
    let entries: Vec<Value> = read_jsonl(&p);
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
    if let Some(c) = b.category {
        line["category"] = json!(c);
    }
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new().create(true).append(true).open(&p)
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    writeln!(f, "{}", line).map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    Ok(Json(json!({ "ok": true })))
}

fn del_line(at: &std::path::Path, index: usize) -> Result<usize, ApiError> {
    let p = at.join("sft.jsonl");
    let text = std::fs::read_to_string(&p).unwrap_or_default();
    let mut lines: Vec<&str> = text.lines().filter(|l| !l.trim().is_empty()).collect();
    if index >= lines.len() {
        return Err(ApiError(localai_core::AppError::NotFound("index hors borne".into())));
    }
    lines.remove(index);
    std::fs::write(&p, lines.join("\n") + "\n")
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    Ok(lines.len())
}

async fn dataset_del(
    State(st): State<Arc<crate::AppState>>,
    Path(index): Path<usize>,
) -> ApiResult<Json<Value>> {
    let n = del_line(&atelier(&st), index)?;
    Ok(Json(json!({ "ok": true, "restants": n })))
}

fn msg_content(msgs: &[Value], role: &str, from_end: bool) -> String {
    let iter: Box<dyn Iterator<Item = &Value>> = if from_end {
        Box::new(msgs.iter().rev())
    } else {
        Box::new(msgs.iter())
    };
    for m in iter {
        if m.get("role").and_then(|r| r.as_str()) == Some(role) {
            return m.get("content").and_then(|c| c.as_str()).unwrap_or("").to_string();
        }
    }
    String::new()
}

fn ex_view(idx: usize, e: &Value) -> Value {
    let msgs: Vec<Value> = e.get("messages").and_then(|m| m.as_array()).cloned().unwrap_or_default();
    json!({
        "index": idx,
        "category": e.get("category").and_then(|c| c.as_str()).unwrap_or(""),
        "user_message": msg_content(&msgs, "user", false),
        "assistant_message": msg_content(&msgs, "assistant", true),
    })
}

#[derive(Debug, Deserialize)]
pub struct ExQuery {
    pub limit: Option<usize>,
}

async fn examples_list(
    State(st): State<Arc<crate::AppState>>,
    Query(q): Query<ExQuery>,
) -> ApiResult<Json<Value>> {
    let at = atelier(&st);
    let entries = read_jsonl(&at.join("sft.jsonl"));
    let total = entries.len();
    let limit = q.limit.unwrap_or(100).clamp(1, 500);
    let start = total.saturating_sub(limit);
    let examples: Vec<Value> = entries[start..]
        .iter()
        .enumerate()
        .map(|(i, e)| ex_view(start + i, e))
        .collect();
    Ok(Json(json!({ "examples": examples, "total": total })))
}

#[derive(Debug, Deserialize)]
pub struct ExBody {
    pub category: Option<String>,
    pub user_message: String,
    pub assistant_message: String,
}

async fn examples_add(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<ExBody>,
) -> ApiResult<Json<Value>> {
    if b.user_message.trim().is_empty() || b.assistant_message.trim().is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("messages vides".into())));
    }
    let at = atelier(&st);
    let line = json!({
        "messages": [
            { "role": "user", "content": b.user_message.trim() },
            { "role": "assistant", "content": b.assistant_message.trim() },
        ],
        "category": b.category.unwrap_or_default(),
    });
    use std::io::Write;
    let mut f = std::fs::OpenOptions::new().create(true).append(true)
        .open(at.join("sft.jsonl"))
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    writeln!(f, "{line}")
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let total = read_jsonl(&at.join("sft.jsonl")).len();
    Ok(Json(json!({ "ok": true, "index": total.saturating_sub(1) })))
}

async fn examples_del(
    State(st): State<Arc<crate::AppState>>,
    Path(index): Path<usize>,
) -> ApiResult<Json<Value>> {
    let n = del_line(&atelier(&st), index)?;
    Ok(Json(json!({ "ok": true, "restants": n })))
}

async fn merge(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let at = atelier(&st);
    let sft = read_jsonl(&at.join("sft.jsonl"));
    let rlvr = read_jsonl(&at.join("sft_rlvr.jsonl"));
    let mut seen = std::collections::HashSet::new();
    let mut merged: Vec<Value> = Vec::new();
    for e in sft.iter().chain(rlvr.iter()) {
        let key = serde_json::to_string(e.get("messages").unwrap_or(&Value::Null)).unwrap_or_default();
        if seen.insert(key) {
            merged.push(e.clone());
        }
    }
    let (n_sft, n_rlvr) = (sft.len(), rlvr.len());
    let n = merged.len();
    if !write_jsonl(&at.join("sft.jsonl"), &merged) {
        return Err(ApiError(localai_core::AppError::Internal("ecriture sft.jsonl impossible".into())));
    }
    Ok(Json(json!({
        "ok": true,
        "sft_before": n_sft,
        "rlvr_total": n_rlvr,
        "merged_count": n,
        "duplicates_removed": n_sft + n_rlvr - n,
    })))
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
    let tag = clean_tag(&b.tag.unwrap_or_else(|| "v9.3".into()));
    if tag.is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("tag invalide".into())));
    }
    let epochs = b.epochs.unwrap_or(2).clamp(1, 10);
    let root = st.settings.root_dir.clone();
    let py = venv_python(&st);
    if !py.is_file() {
        return Err(ApiError(localai_core::AppError::Internal("venv python absent".into())));
    }
    let at = atelier(&st);
    let ds_default = format!("sft_localai_v{}.jsonl", tag.replace('.', ""));
    let mut ds: String = b.dataset.unwrap_or_default().chars().filter(|c| *c != '\'' && *c != ' ').collect();
    if ds.is_empty() && at.join(&ds_default).is_file() {
        ds = ds_default.clone();
    }
    if ds.is_empty() && at.join("sft.jsonl").is_file() {
        ds = "sft.jsonl".into();
    }
    if ds.is_empty() || !at.join(&ds).is_file() {
        return Err(ApiError(localai_core::AppError::BadRequest(format!("corpus introuvable dans l'atelier ({ds_default})"))));
    }
    let log = at.join(format!("train_{tag}.out"));
    let eps = epochs.to_string();
    let args: Vec<&str> = vec![
        "-u", "pipeline.py", "train", "--tag", &tag, "--dataset", &ds,
        "--epochs", &eps, "--ckpt", "50", "--rank", "16",
        "--timeout", "172800", "--resume", "--no-unload",
    ];
    let pid = spawn_detached(&py, &args, &root, &log).await
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("lancement train: {e}"))))?;
    write_meta(&at, &format!("train_{tag}"), json!({
        "pid": pid, "tag": tag, "epochs": epochs, "dataset": ds, "started": now_unix(),
    }));
    Ok(Json(json!({
        "ok": true, "tag": tag, "dataset": ds, "epochs": epochs, "pid": pid,
        "log": log.to_string_lossy(),
    })))
}

async fn train_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let at = atelier(&st);
    let logs = st.settings.root_dir.join("logs");
    let meta = match latest_meta(&at, "train_") {
        Some((_, m)) => m,
        None => {
            return Ok(Json(json!({
                "running": false, "status": "idle", "tag": "",
                "epoch": 0, "total_epochs": 0, "progress": 0.0, "loss": 0.0, "elapsed_s": 0,
            })))
        }
    };
    let pid = meta.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    let tag = meta.get("tag").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let epochs = meta.get("epochs").and_then(|v| v.as_f64()).unwrap_or(2.0);
    let started = meta.get("started").and_then(|v| v.as_u64()).unwrap_or(0);
    let running = pid_alive(pid).await;
    let mut epoch = 0.0f64;
    let mut loss = 0.0f64;
    let mut m_elapsed = 0.0f64;
    if let Ok(txt) = std::fs::read_to_string(logs.join(format!("train_{tag}_metrics.jsonl"))) {
        if let Some(last) = txt.lines().rev().find(|l| !l.trim().is_empty()) {
            if let Ok(v) = serde_json::from_str::<Value>(last) {
                epoch = v.get("epoch").and_then(|x| x.as_f64()).unwrap_or(0.0);
                loss = v.get("loss").and_then(|x| x.as_f64()).unwrap_or(0.0);
                m_elapsed = v.get("elapsed_s").and_then(|x| x.as_f64()).unwrap_or(0.0);
            }
        }
    }
    let progress = if epochs > 0.0 { (epoch / epochs).clamp(0.0, 1.0) } else { 0.0 };
    let mut status = if running { "running" } else { "stopped" };
    if !running && pid > 0 {
        let txt = job_log(&at, &logs, "train", &tag);
        if let Some(rc) = exit_code(&txt) {
            status = if rc == 0 { "finished" } else { "error" };
        } else if txt.contains("TERMINE train") {
            status = "finished";
        } else if txt.contains("Traceback") || txt.contains("ECHEC") || txt.contains("GATE") {
            status = "error";
        }
    }
    let elapsed_s = if running { now_unix().saturating_sub(started) } else { m_elapsed as u64 };
    Ok(Json(json!({
        "running": running, "status": status, "tag": tag,
        "epoch": epoch.round() as u64, "total_epochs": epochs as u64,
        "progress": progress, "loss": loss, "elapsed_s": elapsed_s,
    })))
}

#[derive(Debug, Deserialize, Default)]
pub struct TagBody {
    pub tag: Option<String>,
}

async fn convert(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<TagBody>,
) -> ApiResult<Json<Value>> {
    let tag = clean_tag(&b.tag.unwrap_or_else(|| "v9.3".into()));
    if tag.is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("tag invalide".into())));
    }
    let root = st.settings.root_dir.clone();
    let py = venv_python(&st);
    if !py.is_file() {
        return Err(ApiError(localai_core::AppError::Internal("venv python absent".into())));
    }
    let at = atelier(&st);
    let log = at.join(format!("convert_{tag}.out"));
    let args: Vec<&str> = vec![
        "-u", "pipeline.py", "convert", "--tag", &tag,
        "--f16", "--quant", "q8_0", "--timeout", "14400",
    ];
    let pid = spawn_detached(&py, &args, &root, &log).await
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("lancement convert: {e}"))))?;
    write_meta(&at, &format!("convert_{tag}"), json!({ "pid": pid, "tag": tag, "started": now_unix() }));
    Ok(Json(json!({
        "status": "started", "tag": tag, "pid": pid, "log": log.to_string_lossy(),
    })))
}

async fn convert_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let at = atelier(&st);
    let logs = st.settings.root_dir.join("logs");
    let meta = match latest_meta(&at, "convert_") {
        Some((_, m)) => m,
        None => {
            return Ok(Json(json!({ "running": false, "status": "idle", "tag": "", "output_path": "" })))
        }
    };
    let pid = meta.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    let tag = meta.get("tag").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let running = pid_alive(pid).await;
    let mut status = if running { "running" } else { "stopped" };
    let mut output = String::new();
    if !running && pid > 0 {
        let txt = job_log(&at, &logs, "convert", &tag);
        if let Some(i) = txt.rfind("convert -> ") {
            let tail = txt[i + 11..].trim();
            output = tail.lines().next().unwrap_or("").trim().to_string();
            status = "finished";
        } else if txt.contains("ECHEC") || txt.contains("introuvable") || txt.contains("Traceback") {
            status = "error";
        }
    }
    Ok(Json(json!({ "running": running, "status": status, "tag": tag, "output_path": output })))
}

async fn import_ollama(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<TagBody>,
) -> ApiResult<Json<Value>> {
    let tag = clean_tag(&b.tag.unwrap_or_else(|| "v9.3".into()));
    if tag.is_empty() {
        return Err(ApiError(localai_core::AppError::BadRequest("tag invalide".into())));
    }
    let root = st.settings.root_dir.clone();
    let py = venv_python(&st);
    if !py.is_file() {
        return Err(ApiError(localai_core::AppError::Internal("venv python absent".into())));
    }
    let at = atelier(&st);
    let mf = at.join(format!("Modelfile_{tag}"));
    if !mf.is_file() {
        let tpl = "FROM {{GGUF}}\nPARAMETER temperature 0.7\nPARAMETER num_ctx 8192\n";
        std::fs::write(&mf, tpl)
            .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    }
    let log = at.join(format!("deploy_{tag}.out"));
    let args: Vec<&str> = vec!["-u", "pipeline.py", "deploy", "--tag", &tag, "--timeout", "3600"];
    let pid = spawn_detached(&py, &args, &root, &log).await
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("lancement deploy: {e}"))))?;
    write_meta(&at, &format!("deploy_{tag}"), json!({ "pid": pid, "tag": tag, "started": now_unix() }));
    Ok(Json(json!({ "status": "started", "tag": tag, "pid": pid })))
}

async fn import_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let at = atelier(&st);
    let logs = st.settings.root_dir.join("logs");
    let meta = match latest_meta(&at, "deploy_") {
        Some((_, m)) => m,
        None => {
            return Ok(Json(json!({ "running": false, "status": "idle", "tag": "" })))
        }
    };
    let pid = meta.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    let tag = meta.get("tag").and_then(|v| v.as_str()).unwrap_or("").to_string();
    let running = pid_alive(pid).await;
    let mut status = if running { "running" } else { "stopped" };
    if !running && pid > 0 {
        let txt = job_log(&at, &logs, "deploy", &tag);
        if txt.contains("EXIT=0") {
            status = "finished";
        } else if txt.contains("EXIT=") || txt.contains("Modelfile absent")
            || txt.contains("ERROR:") || txt.contains("Traceback")
        {
            status = "error";
        }
    }
    Ok(Json(json!({ "running": running, "status": status, "tag": tag })))
}

#[derive(Debug, Deserialize, Default)]
pub struct RlvrBody {
    pub n_cibles: Option<u32>,
}

async fn rlvr_run(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<RlvrBody>,
) -> ApiResult<Json<Value>> {
    let n = b.n_cibles.unwrap_or(200).clamp(1, 2000);
    let root = st.settings.root_dir.clone();
    let py = venv_python(&st);
    if !py.is_file() {
        return Err(ApiError(localai_core::AppError::Internal("venv python absent".into())));
    }
    let at = atelier(&st);
    let code = format!("from backend.core.rlvr import generer; generer(n_cibles={n})");
    let log = at.join("rlvr.out");
    let pid = spawn_detached(&py, &["-u", "-c", &code], &root, &log).await
        .map_err(|e| ApiError(localai_core::AppError::Internal(format!("lancement rlvr: {e}"))))?;
    write_meta(&at, "rlvr", json!({ "pid": pid, "n_cibles": n, "started": now_unix() }));
    Ok(Json(json!({ "ok": true, "status": "started", "pid": pid, "n_cibles": n })))
}

async fn rlvr_status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let at = atelier(&st);
    let entries = read_jsonl(&at.join("sft_rlvr.jsonl"));
    let total = entries.len();
    let validated = entries
        .iter()
        .filter(|e| e.get("verifie").and_then(|v| v.as_bool()).unwrap_or(false))
        .count();
    let running = match latest_meta(&at, "rlvr") {
        Some((_, m)) => {
            pid_alive(m.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32).await
        }
        None => false,
    };
    Ok(Json(json!({ "total": total, "validated": validated, "running": running })))
}
