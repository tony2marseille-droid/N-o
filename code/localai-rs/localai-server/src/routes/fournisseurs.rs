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
        .route("/api/fournisseurs/credits", get(credits))
}

/// Table de suivi de consommation par fournisseur (consigne tokens).
fn assurer_table_usage(db: &localai_core::db::Db) {
    let _ = db.execute(
        "CREATE TABLE IF NOT EXISTS provider_usage ( \
         provider TEXT PRIMARY KEY, tokens_in INTEGER DEFAULT 0, \
         tokens_out INTEGER DEFAULT 0, requests INTEGER DEFAULT 0, updated_at TEXT)",
        &[],
    );
}

/// Enregistre une consommation (appele par le chat apres chaque requete
/// cloud reussie). Cumulatif : tokens entree/sortie + nombre de requetes.
pub fn enregistrer_usage(db: &localai_core::db::Db, fournisseur: &str, tin: i64, tout: i64) {
    assurer_table_usage(db);
    let mtn = localai_core::db::now_iso();
    let _ = db.execute(
        "INSERT INTO provider_usage (provider, tokens_in, tokens_out, requests, updated_at) \
         VALUES (?,?,?,1,?) \
         ON CONFLICT(provider) DO UPDATE SET tokens_in=tokens_in+excluded.tokens_in, \
         tokens_out=tokens_out+excluded.tokens_out, requests=requests+1, \
         updated_at=excluded.updated_at",
        &[&fournisseur, &tin, &tout, &mtn.as_str()],
    );
}

/// Note la derniere erreur de quota d un fournisseur (affichage en direct
/// dans le panneau tokens ; effacee a la prochaine requete reussie).
pub fn noter_quota_epuise(db: &localai_core::db::Db, fournisseur: &str, err: &str) {
    let court: String = err.chars().take(140).collect();
    let _ = db.set_setting(&format!("provider_{fournisseur}_quota_err"), &court);
}

pub fn effacer_quota_err(db: &localai_core::db::Db, fournisseur: &str) {
    let _ = db.set_setting(&format!("provider_{fournisseur}_quota_err"), "");
}

/// GET /api/fournisseurs/credits : suivi en direct par fournisseur.
/// - OpenRouter : credit reel via l API officielle (total/usage/restant).
/// - Autres : consommation locale cumulee (tokens entree/sortie) et
///   derniere erreur de quota eventuelle.
async fn credits(State(st): State<Arc<crate::AppState>>) -> ApiResult<Json<Value>> {
    assurer_table_usage(&st.db);
    let client = reqwest::Client::builder()
        .timeout(std::time::Duration::from_secs(15))
        .build()
        .map_err(|e| ApiError(localai_core::AppError::Internal(e.to_string())))?;
    let mut resultat: Vec<Value> = Vec::new();
    for f in providers::FOURNISSEURS {
        let cle = st.db.get_setting(&cle_db(f.id), "");
        let modele = st.db.get_setting(&modele_db(f.id), f.modele_defaut);
        let usage = st.db.query_one_json(
            "SELECT tokens_in, tokens_out, requests, updated_at FROM provider_usage WHERE provider=?",
            &[&f.id],
        )?;
        let (tin, tout, reqs, maj) = match usage {
            Some(u) => (
                u.get("tokens_in").and_then(|v| v.as_i64()).unwrap_or(0),
                u.get("tokens_out").and_then(|v| v.as_i64()).unwrap_or(0),
                u.get("requests").and_then(|v| v.as_i64()).unwrap_or(0),
                u.get("updated_at").and_then(|v| v.as_str()).unwrap_or("").to_string(),
            ),
            None => (0, 0, 0, String::new()),
        };
        // Credit reel : uniquement OpenRouter (API /credits officielle).
        let mut credit = Value::Null;
        if f.id == "openrouter" && !cle.is_empty() {
            if let Ok(rep) = client
                .get("https://openrouter.ai/api/v1/credits")
                .bearer_auth(&cle)
                .send()
                .await
            {
                if let Ok(v) = rep.json::<Value>().await {
                    if let Some(d) = v.get("data") {
                        let total = d.get("total_credits").and_then(|x| x.as_f64()).unwrap_or(0.0);
                        let utilise = d.get("total_usage").and_then(|x| x.as_f64()).unwrap_or(0.0);
                        credit = json!({
                            "total": total,
                            "utilise": utilise,
                            "restant": (total - utilise).max(0.0),
                        });
                    }
                }
            }
        }
        let err_quota = st.db.get_setting(&format!("provider_{}_quota_err", f.id), "");
        resultat.push(json!({
            "id": f.id,
            "nom": f.nom,
            "configue": !cle.is_empty(),
            "modele": modele,
            "tokens_in": tin,
            "tokens_out": tout,
            "requests": reqs,
            "updated_at": maj,
            "credit": credit,
            "quota_err": if err_quota.is_empty() { Value::Null } else { json!(err_quota) },
        }));
    }
    Ok(Json(json!({ "fournisseurs": resultat })))
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
