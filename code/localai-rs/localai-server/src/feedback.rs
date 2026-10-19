//! Boucle d'auto-amelioration : feedback utilisateur, evaluations
//! automatiques (juge leger) et regles apprises par le coach hebdomadaire.
use std::sync::Arc;

use axum::{extract::State, routing::{get, post}, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/feedback", post(soumettre))
        .route("/api/feedback/stats", get(stats))
        .route("/api/selfimprove/rules", get(regles))
        .route("/api/selfimprove/coach", post(coach))
}

/// Cree les tables d'auto-amelioration si absentes (idempotent).
pub fn init_tables(st: &crate::AppState) {
    let _ = st.db.execute(
        "CREATE TABLE IF NOT EXISTS feedback (id INTEGER PRIMARY KEY AUTOINCREMENT, \
         conversation_id INTEGER, message_id INTEGER, rating INTEGER, note TEXT, created_at TEXT)",
        &[],
    );
    let _ = st.db.execute(
        "CREATE TABLE IF NOT EXISTS auto_evals (id INTEGER PRIMARY KEY AUTOINCREMENT, \
         conversation_id INTEGER, score INTEGER, rationale TEXT, created_at TEXT)",
        &[],
    );
    let _ = st.db.execute(
        "CREATE TABLE IF NOT EXISTS ameliorations (id INTEGER PRIMARY KEY AUTOINCREMENT, \
         regle TEXT, source TEXT, active INTEGER DEFAULT 1, created_at TEXT)",
        &[],
    );
}

/// Enregistre une evaluation d'echange (juge interne ou LLM). Non bloquant.
pub fn noter_echange(db: &localai_core::db::Db, conversation_id: i64, score: i64, rationale: &str) {
    let now = localai_core::db::now_iso();
    let _ = db.execute(
        "INSERT INTO auto_evals (conversation_id, score, rationale, created_at) VALUES (?,?,?,?)",
        &[&conversation_id, &score, &rationale, &now.as_str()],
    );
}

#[derive(Debug, Deserialize)]
pub struct FeedbackBody {
    pub conversation_id: Option<i64>,
    pub message_id: Option<i64>,
    pub rating: i64,
    pub note: Option<String>,
}

async fn soumettre(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<FeedbackBody>,
) -> ApiResult<Json<Value>> {
    init_tables(&st);
    let rating = b.rating.clamp(1, 5);
    let now = localai_core::db::now_iso();
    let id = st.db.execute(
        "INSERT INTO feedback (conversation_id, message_id, rating, note, created_at) VALUES (?,?,?,?,?)",
        &[
            &b.conversation_id,
            &b.message_id,
            &rating,
            &b.note.unwrap_or_default(),
            &now.as_str(),
        ],
    )?;
    Ok(Json(json!({ "id": id, "rating": rating })))
}

async fn stats(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    init_tables(&st);
    let total = st.db.query_one_json(
        "SELECT COUNT(*) AS n, COALESCE(AVG(rating),0) AS moyenne FROM feedback", &[])?;
    let par_note = st.db.query_all_json(
        "SELECT rating, COUNT(*) AS n FROM feedback GROUP BY rating ORDER BY rating", &[])?;
    let evals = st.db.query_one_json(
        "SELECT COUNT(*) AS n, COALESCE(AVG(score),0) AS moyenne FROM auto_evals", &[])?;
    Ok(Json(json!({
        "total": total.as_ref().and_then(|t| t.get("n")).cloned().unwrap_or(json!(0)),
        "moyenne": total.as_ref().and_then(|t| t.get("moyenne")).cloned().unwrap_or(json!(0)),
        "par_note": par_note,
        "auto_evals": evals,
    })))
}

async fn regles(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    init_tables(&st);
    let lignes = st.db.query_all_json(
        "SELECT id, regle, source, created_at FROM ameliorations WHERE active=1 ORDER BY id DESC LIMIT 20",
        &[],
    )?;
    Ok(Json(json!({ "regles": lignes })))
}

async fn coach(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    init_tables(&st);
    let v = crate::pybridge::py_json(
        &st.settings.root_dir,
        "from backend.core.coach import lancer; import json; print(json.dumps(lancer(), default=str))",
    )
    .await?;
    Ok(Json(v))
}
