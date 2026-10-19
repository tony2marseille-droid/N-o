use std::sync::Arc;

use axum::{extract::{Query, State}, routing::{get, post}, Json, Router};
use serde::Deserialize;
use serde_json::Value;

use crate::error::ApiResult;
use crate::pybridge;

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/security", get(status))
        .route("/api/security/folders", post(add_folder).delete(del_folder))
        .route("/api/security/rules", get(rules).put(set_rules))
        .route("/api/security/log", get(log))
}

async fn status(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core import security; r = security.charger_regles(); print(json.dumps({'home': str(security.HOME()), 'dossiers_autorises': security.dossiers_autorises(), 'regles_version': r.get('version'), 'require_permission_outside_home': r.get('require_permission_outside_home', True), 'stockage': security.usage_disque(), 'journal_taille': len(security.lire_journal(limite=10000))}, default=str))").await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize)]
pub struct FolderBody {
    pub path: String,
}

async fn add_folder(State(st): State<Arc<crate::AppState>>, Json(b): Json<FolderBody>) -> ApiResult<Json<Value>> {
    let p = b.path.replace('\'', "");
    let v = pybridge::py_json(&st.settings.root_dir, &format!(
        "from backend.core import security; print(json.dumps({{'ok': True, 'dossiers_autorises': security.accorder_dossier(r'{}')}}))", p)).await;
    match v {
        Ok(j) => Ok(Json(j)),
        Err(_) => Ok(Json(serde_json::json!({ "ok": false, "error": "dossier introuvable" }))),
    }
}

async fn del_folder(State(st): State<Arc<crate::AppState>>, Json(b): Json<FolderBody>) -> ApiResult<Json<Value>> {
    let p = b.path.replace('\'', "");
    let v = pybridge::py_json(&st.settings.root_dir, &format!(
        "from backend.core import security; print(json.dumps({{'ok': True, 'dossiers_autorises': security.retirer_dossier(r'{}')}}))", p)).await?;
    Ok(Json(v))
}

async fn rules(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let v = pybridge::py_json(&st.settings.root_dir,
        "from backend.core import security; print(json.dumps(security.charger_regles()))").await?;
    Ok(Json(v))
}

async fn set_rules(State(st): State<Arc<crate::AppState>>, Json(b): Json<Value>) -> ApiResult<Json<Value>> {
    let raw = serde_json::to_string(&b).unwrap_or_default().replace('\'', "");
    let v = pybridge::py_json(&st.settings.root_dir, &format!(
        "import json; from backend.core import security; s = security.sauvegarder_regles(json.loads('{}')); security.journaliser('rules_updated', {{'version': s.get('version')}}); print(json.dumps({{'ok': True, 'rules': s}}))", raw)).await?;
    Ok(Json(v))
}

#[derive(Debug, Deserialize, Default)]
pub struct LogQuery {
    pub limite: Option<i64>,
}

async fn log(
    State(st): State<Arc<crate::AppState>>,
    Query(q): Query<LogQuery>,
) -> ApiResult<Json<Value>> {
    let lim = q.limite.unwrap_or(100).clamp(1, 1000);
    let v = pybridge::py_json(&st.settings.root_dir, &format!(
        "from backend.core import security; print(json.dumps({{'events': security.lire_journal({})}}))", lim)).await?;
    Ok(Json(v))
}
