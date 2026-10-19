//! Pont generique : execute n'importe quel outil du registre via le pont
//! Python, en MODE JOB ASYNCHRONE (les generations SD durent 1-7 min :
//! aucun client HTTP ne survit a un tel timeout). POST cree le job et
//! rend une id immediatement ; GET /api/outil/job/:id donne l'etat.
use std::collections::{HashMap, VecDeque};
use std::sync::Arc;

use axum::{
    extract::{Path, Query, State},
    routing::{get, post},
    Json, Router,
};
use serde::Deserialize;
use serde_json::{json, Value};
use tokio::sync::Mutex;

use crate::error::{ApiError, ApiResult};
use crate::{tools, AppState};
use localai_core::AppError;

#[derive(Clone)]
struct Job {
    outil: String,
    resultat: Option<String>,
    fini: bool,
}

#[derive(Clone)]
pub struct JobStore {
    jobs: Arc<Mutex<std::collections::HashMap<String, Job>>>,
    ordre: Arc<Mutex<VecDeque<String>>>,
}

impl JobStore {
    pub fn new() -> Self {
        Self {
            jobs: Arc::new(Mutex::new(std::collections::HashMap::new())),
            ordre: Arc::new(Mutex::new(VecDeque::new())),
        }
    }

    async fn creer(&self, outil: String) -> String {
        let mut jobs = self.jobs.lock().await;
        let mut ordre = self.ordre.lock().await;
        let id = format!("j{}", chrono_free_id());
        jobs.insert(id.clone(), Job { outil, resultat: None, fini: false });
        ordre.push_back(id.clone());
        // Limite : garde-fou memoire, on jette les plus anciens finis.
        while ordre.len() > 200 {
            if let Some(vieux) = ordre.pop_front() {
                if let Some(j) = jobs.get(&vieux) {
                    if j.fini {
                        jobs.remove(&vieux);
                    }
                }
            } else {
                break;
            }
        }
        id
    }

    async fn definir_resultat(&self, id: &str, resultat: String) {
        let mut jobs = self.jobs.lock().await;
        if let Some(j) = jobs.get_mut(id) {
            j.resultat = Some(resultat);
            j.fini = true;
        }
    }

    async fn lire(&self, id: &str) -> Option<Job> {
        self.jobs.lock().await.get(id).cloned()
    }
}

fn chrono_free_id() -> String {
    use std::time::{SystemTime, UNIX_EPOCH};
    let nanos = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .map(|d| d.as_nanos())
        .unwrap_or(0);
    format!("{nanos:x}{}", std::process::id())
}

pub fn router() -> Router<Arc<AppState>> {
    Router::new()
        .route("/api/outil/execute", post(executer))
        .route("/api/outil/job/:id", get(lire_job))
}

#[derive(Debug, Deserialize)]
pub struct OutilBody {
    pub tool: String,
    pub args: Option<Value>,
}

/// Lancement : cree un job, execute l'outil en tache de fond, rend l'id.
async fn executer(
    State(st): State<Arc<AppState>>,
    Json(b): Json<OutilBody>,
) -> ApiResult<Json<Value>> {
    let nom = b.tool.trim().to_string();
    if nom.is_empty() {
        return Err(ApiError(AppError::BadRequest("outil vide".into())));
    }
    let store = st.jobs.clone();
    let id = store.creer(nom.clone()).await;
    let root = st.settings.root_dir.clone();
    let niveau = tools::tool_level(&st.db);
    let args = b.args.unwrap_or_else(|| json!({}));
    let nom_spawn = nom.clone();
    let id2 = id.clone();
    tokio::spawn(async move {
        let res = tools::execute(&root, &nom_spawn, &args, &niveau).await;
        store.definir_resultat(&id2, res).await;
    });
    Ok(Json(json!({ "job": id, "outil": nom, "etat": "lance" })))
}

/// Polling : GET /api/outil/job/j<hex><pid>[?attendre=1]
/// attendre=1 : long-poll — la reponse attend la fin du job (max 880 s,
/// check toutes les 2 s) pour que le client n'ait QU'UN appel par job.
async fn lire_job(
    State(st): State<Arc<AppState>>,
    Path(id): Path<String>,
    Query(attendre): Query<HashMap<String, String>>,
) -> ApiResult<Json<Value>> {
    let long_attente = attendre.get("attendre").map(|v| v == "1").unwrap_or(false);
    if !long_attente {
        return match st.jobs.lire(&id).await {
            None => Err(ApiError(AppError::NotFound("job inconnu".into()))),
            Some(j) => Ok(Json(json!({
                "job": id,
                "outil": j.outil,
                "fini": j.fini,
                "resultat": j.resultat,
            }))),
        };
    }
    // Long-poll : attend la fin (max 880 s, sous les 900 s du client).
    let debut = std::time::Instant::now();
    loop {
        match st.jobs.lire(&id).await {
            None => return Err(ApiError(AppError::NotFound("job inconnu".into()))),
            Some(j) if j.fini => {
                return Ok(Json(json!({
                    "job": id,
                    "outil": j.outil,
                    "fini": true,
                    "resultat": j.resultat,
                })));
            }
            Some(_) => {}
        }
        if debut.elapsed().as_secs() >= 880 {
            return Ok(Json(json!({
                "job": id,
                "fini": false,
                "note": "delai d'attente serveur atteint, repoll",
            })));
        }
        tokio::time::sleep(std::time::Duration::from_secs(2)).await;
    }
}
