//! Fournisseurs de modeles cloud : cles API stockees en base, test de
//! connexion, et modeles cloud exposables dans le selecteur du chat.

use std::sync::Arc;

use axum::{extract::State, routing::{get, post}, Json, Router};
use serde::Deserialize;
use serde_json::{json, Value};

use crate::error::{ApiError, ApiResult};
use localai_core::providers;

fn cle_db(fournisseur: &str) -> String {
    format!("provider_{fournisseur}_key")
}

fn modele_db(fournisseur: &str) -> String {
    format!("provider_{fournisseur}_model")
}

#[derive(Clone)]
struct Ctx {
    db: localai_core::db::Db,
    root: std::path::PathBuf,
}

pub fn router() -> Router<Arc<crate::AppState>> {
    Router::new()
        .route("/api/fournisseurs", get(liste))
        .route("/api/fournisseurs", post(enregistrer))
        .route("/api/fournisseurs/test", post(tester))
}

async fn liste(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    let mut f_liste = Vec::new();
    for f in providers::FOURNISSEURS {
        let cle = st.db.get_setting(&cle_db(f.id), "");
        let modele = st.db.get_setting(&modele_db(f.id), f.modele_defaut);
        f_liste.push(json!({
            "id": f.id,
            "nom": f.nom,
            "configue": !cle.is_empty(),
            "modele": modele,
        }));
    }
    Ok(Json(json!({ "fournisseurs": f_liste })))
}

#[derive(Debug, Deserialize)]
struct CorpsEnreg {
    fournisseur: String,
    cle: Option<String>,
    modele: Option<String>,
}

async fn enregistrer(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<CorpsEnreg>,
) -> ApiResult<Json<Value>> {
    let f = providers::trouver(&b.fournisseur)
        .ok_or_else(|| ApiError(localai_core::AppError::BadRequest("fournisseur inconnu".into())))?;
    if let Some(cle) = b.cle {
        st.db.set_setting(&cle_db(f.id), cle.trim())?;
    }
    if let Some(m) = b.modele {
        let m = m.trim().to_string();
        st.db.set_setting(&modele_db(f.id), if m.is_empty() { f.modele_defaut } else { &m })?;
    }
    Ok(Json(json!({ "ok": true })))
}

#[derive(Debug, Deserialize)]
struct CorpsTest {
    fournisseur: String,
}

async fn tester(
    State(st): State<Arc<crate::AppState>>,
    Json(b): Json<CorpsTest>,
) -> ApiResult<Json<Value>> {
    let f = providers::trouver(&b.fournisseur)
        .ok_or_else(|| ApiError(localai_core::AppError::BadRequest("fournisseur inconnu".into())))?;
    let cle = st.db.get_setting(&cle_db(f.id), "");
    if cle.is_empty() {
        return Ok(Json(json!({ "ok": false, "detail": "cle absente" })));
    }
    let modele = st.db.get_setting(&modele_db(f.id), f.modele_defaut);
    let messages = vec![localai_core::ollama::Msg {
        role: "user".into(),
        content: "Reponds exactement : OK".into(),
    }];
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(30))
        .build()
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    match providers::chat(&client, f, &cle, &modele, &messages, 0.0, 20).await {
        Ok(rep) => Ok(Json(json!({ "ok": true, "reponse": rep.chars().take(120).collect::<String>() }))),
        Err(e) => Ok(Json(json!({ "ok": false, "detail": e }))),
    }
}
