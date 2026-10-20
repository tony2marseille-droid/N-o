//! Conseil des 100 idees : generation + validation par le conseil multi-
//! etapes (detache), consultation, et lancement d'une amelioration approuvee
//! par l'utilisateur (le dev agent implemente l'idee dans le workspace).

use std::sync::Arc;

use axum::{
    extract::{Path, State},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use crate::routes::finetune::{now_unix, pid_alive, spawn_detached};

static CONSEIL_RUNNING: std::sync::atomic::AtomicBool =
    std::sync::atomic::AtomicBool::new(false);

fn data_file(st: &Arc<crate::AppState>) -> std::path::PathBuf {
    st.settings.data_dir.join("conseil_idees.jsonl")
}

fn idees(st: &Arc<crate::AppState>) -> Vec<Value> {
    std::fs::read_to_string(data_file(st))
        .map(|t| {
            t.lines()
                .filter(|l| !l.trim().is_empty())
                .filter_map(|l| serde_json::from_str(l).ok())
                .collect()
        })
        .unwrap_or_default()
}

fn sauver(st: &Arc<crate::AppState>, idees: &[Value]) -> bool {
    use std::io::Write;
    let Ok(mut f) = std::fs::File::create(data_file(st)) else {
        return false;
    };
    for e in idees {
        if writeln!(f, "{e}").is_err() {
            return false;
        }
    }
    true
}

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/conseil/lancer", post(lancer))
        .route("/api/conseil/status", get(etat))
        .route("/api/conseil/idees", get(liste))
        .route("/api/conseil/idee/:id/lancer", post(lancer_idee))
}

async fn lancer(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    if CONSEIL_RUNNING.swap(true, std::sync::atomic::Ordering::SeqCst) {
        return Ok(Json(json!({ "ok": false, "detail": "conseil deja en cours" })));
    }
    let root = st.settings.root_dir.clone();
    let py = root.join(".venv").join("Scripts").join("python.exe");
    if !py.is_file() {
        CONSEIL_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
        return Err(ApiError(localai_core::AppError::Internal(
            "venv python absent".into(),
        )));
    }
    let logs = root.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    let log = logs.join("conseil.out");
    match spawn_detached(&py, &["-u", "conseil.py"], &root, &log).await {
        Ok(pid) => {
            let _ = std::fs::write(
                root.join("data").join("conseil.json"),
                json!({ "pid": pid, "started": now_unix() }).to_string(),
            );
            Ok(Json(json!({ "ok": true, "pid": pid })))
        }
        Err(e) => {
            CONSEIL_RUNNING.store(false, std::sync::atomic::Ordering::SeqCst);
            Err(ApiError(localai_core::AppError::Internal(format!(
                "lancement conseil: {e}"
            ))))
        }
    }
}

async fn etat(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let meta: Value = std::fs::read_to_string(st.settings.data_dir.join("conseil.json"))
        .ok()
        .and_then(|t| serde_json::from_str(&t).ok())
        .unwrap_or(json!({}));
    let pid = meta.get("pid").and_then(|v| v.as_u64()).unwrap_or(0) as u32;
    let running = pid > 0 && pid_alive(pid).await;
    let nb = idees(&st).len();
    Ok(Json(json!({ "running": running, "idees": nb })))
}

async fn liste(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    Ok(Json(json!({ "idees": idees(&st) })))
}

#[derive(Debug, Deserialize)]
pub struct LancerIdeeBody {
    pub id: i64,
}

async fn lancer_idee(
    State(st): State<Arc<crate::AppState>>,
    Path(id): Path<i64>,
) -> ApiResult<Json<Value>> {
    let mut liste_idees = idees(&st);
    let Some(idee) = liste_idees
        .iter_mut()
        .find(|v| v.get("id").and_then(|x| x.as_i64()) == Some(id))
    else {
        return Err(ApiError(localai_core::AppError::NotFound(
            "idee introuvable".into(),
        )));
    };
    let statut = idee
        .get("statut")
        .and_then(|v| v.as_str())
        .unwrap_or("proposee");
    if statut == "en_cours" {
        return Ok(Json(json!({ "ok": false, "detail": "deja en cours" })));
    }
    idee["statut"] = json!("approuvee");
    let titre = idee
        .get("titre")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let desc = idee
        .get("description")
        .and_then(|v| v.as_str())
        .unwrap_or("")
        .to_string();
    let root = st.settings.root_dir.clone();
    let py = root.join(".venv").join("Scripts").join("python.exe");
    let script = root.join("impl_idee.py");
    if !py.is_file() || !script.is_file() {
        return Ok(Json(
            json!({ "ok": true, "statut": "approuvee", "detail": "impl_idee absent, idee approuvee" }),
        ));
    }
    let logs = root.join("logs");
    let _ = std::fs::create_dir_all(&logs);
    let log = logs.join(format!("impl_idee_{id}.out"));
    let _ = spawn_detached(
        &py,
        &["-u", &script.to_string_lossy(), &id.to_string(), &titre, &desc],
        &root,
        &log,
    )
    .await;
    idee["statut"] = json!("en_cours");
    if !sauver(&st, &liste_idees) {
        return Err(ApiError(localai_core::AppError::Internal(
            "ecriture impossible".into(),
        )));
    }
    Ok(Json(json!({ "ok": true, "statut": "en_cours" })))
}

